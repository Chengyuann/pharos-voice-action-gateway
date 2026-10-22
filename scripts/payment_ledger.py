"""Local issuance budgets and append-only audit; neither is on-chain spend proof."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3

from pharos_voice_action_gateway import amount_to_units, sha256_json, strict_json_loads
from evm_address import validate_address

ZERO_HASH = "0x" + "0" * 64
DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "config/payment_p1_policy.json"


class BudgetDenied(ValueError):
    pass


class AuditIntegrityError(ValueError):
    pass


def _utc(value=None):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("Ledger time must be timezone-aware")
    return value.astimezone(timezone.utc)


def load_budget_policy(path=DEFAULT_POLICY):
    value = strict_json_loads(Path(path).read_text())
    if (not isinstance(value, dict) or set(value) != {"window_seconds", "limits", "counting_basis"}
            or value["counting_basis"] != "unsigned_draft_issued"
            or type(value["window_seconds"]) is not int or not 60 <= value["window_seconds"] <= 2678400
            or not isinstance(value["limits"], dict) or not value["limits"]):
        raise ValueError("Invalid P1 issuance-budget policy")
    for chain, limits in value["limits"].items():
        if chain not in {"1672", "688689"} or not isinstance(limits, dict) or not limits:
            raise ValueError("Unsupported budget network")
        for symbol, limit in limits.items():
            supported = {"PROS", "USDC", "WPROS"} if chain == "1672" else {"PHRS"}
            if symbol not in supported or not isinstance(limit, str):
                raise ValueError("Invalid budget asset/limit")
            decimals = 6 if symbol == "USDC" else 18
            if int(amount_to_units(limit, decimals)) <= 0:
                raise ValueError("Budget limit must be positive")
    return value


def draft_charges(draft, policy):
    """Count native fee budgets too; no cross-chain, cross-sender or asset pooling."""
    if draft.get("kind") == "unsigned_batch":
        if not isinstance(draft.get("items"), list) or not 1 <= len(draft["items"]) <= 5:
            raise ValueError("Expected 1-5 batch items")
        combined = {}
        for item in draft["items"]:
            if item.get("kind") == "unsigned_batch" or item.get("mode") != "mcp":
                raise ValueError("Nested or non-mainnet batches are unsupported")
            for charge in draft_charges(item, policy):
                scope = charge["scope"]
                if scope not in combined:
                    combined[scope] = {**charge, "units": "0"}
                combined[scope]["units"] = str(int(combined[scope]["units"]) + int(charge["units"]))
        return [combined[scope] for scope in sorted(combined)]
    chain = str(draft["network"]["chain_id"])
    sender = validate_address(draft["transaction"]["from"])
    token = draft["payment"]["token"]
    native = "PROS" if chain == "1672" else "PHRS"
    decimals = 6 if token == "USDC" else 18
    amount = int(amount_to_units(draft["payment"]["amount"], decimals))
    fee = int(amount_to_units(draft["fee"].get("amount", "0"), 18))
    amounts = {token: amount}
    if fee:
        amounts[native] = amounts.get(native, 0) + fee
    limits = policy["limits"].get(chain, {})
    charges = []
    for symbol, units in sorted(amounts.items()):
        if symbol not in limits:
            raise BudgetDenied("No rolling budget configured for this network/asset")
        limit = int(amount_to_units(limits[symbol], 6 if symbol == "USDC" else 18))
        if units <= 0 or units >= 2**256:
            raise ValueError("Invalid budget charge")
        mode = draft.get("mode")
        if mode not in {"mcp", "live", "demo"}:
            raise ValueError("Invalid draft mode")
        charges.append({"scope": f"{mode}:{chain}:{sender}:{symbol}", "asset": symbol,
                        "units": str(units), "limit_units": str(limit)})
    return charges


def draft_nonces(draft):
    """Only explicit mainnet nonce requests are reserved; never invent a gap."""
    if draft.get("mode") != "mcp":
        return []
    transactions = ([item["transaction"] for item in draft["items"]]
                    if draft.get("kind") == "unsigned_batch" else [draft["transaction"]])
    if not 1 <= len(transactions) <= 5:
        raise ValueError("Invalid nonce request count")
    reservations = []
    for tx in transactions:
        if (tx.get("chainId") != 1672 or type(tx["chainId"]) is not int
                or not isinstance(tx.get("nonce"), str)
                or not re.fullmatch(r"0x(?:0|[1-9a-f][0-9a-f]*)", tx["nonce"])
                or int(tx["nonce"], 16) >= 2**64 - 1):
            raise ValueError("Invalid mainnet nonce request")
        reservations.append({"scope": f"1672:{validate_address(tx['from'])}",
                             "nonce": str(int(tx["nonce"], 16))})
    if any(item["scope"] != reservations[0]["scope"]
           or int(item["nonce"]) != int(reservations[0]["nonce"]) + index
           for index, item in enumerate(reservations)):
        raise ValueError("Batch requires one sender and contiguous ordered nonces")
    return reservations


def verify_audit_chain(value, expected_tip=None):
    if (not isinstance(value, dict) or set(value) != {"schema", "entries", "tip"}
            or value["schema"] != "pharos-payment-audit-chain/1"
            or not isinstance(value["entries"], list) or len(value["entries"]) > 100000):
        raise AuditIntegrityError("Unsupported audit-chain schema/count")
    previous, last_time = ZERO_HASH, None
    for index, entry in enumerate(value["entries"], 1):
        if (not isinstance(entry, dict) or set(entry) != {"sequence", "created_at", "kind", "payload", "previous_hash", "entry_hash"}
                or type(entry["sequence"]) is not int or entry["sequence"] != index
                or entry["previous_hash"] != previous or not isinstance(entry["payload"], dict)):
            raise AuditIntegrityError(f"Broken audit link at sequence {index}")
        try:
            now = _utc(datetime.fromisoformat(entry["created_at"]))
        except (ValueError, TypeError) as exc:
            raise AuditIntegrityError("Invalid audit timestamp") from exc
        if not isinstance(entry["kind"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", entry["kind"]):
            raise AuditIntegrityError("Invalid audit event kind")
        if last_time and now < last_time:
            raise AuditIntegrityError("Audit timestamps moved backwards")
        material = {k: v for k, v in entry.items() if k != "entry_hash"}
        if entry["entry_hash"] != "0x" + sha256_json(material):
            raise AuditIntegrityError(f"Invalid audit hash at sequence {index}")
        previous, last_time = entry["entry_hash"], now
    if value["tip"] != previous or expected_tip is not None and expected_tip != previous:
        raise AuditIntegrityError("Audit tip mismatch")
    return {"verified": True, "entries": len(value["entries"]), "tip": previous,
            "scope": "self-consistency; truncation or full rewrite requires a separately retained trusted tip"}


class PaymentLedger:
    def __init__(self, path):
        self.path = Path(path)

    @staticmethod
    def ensure_schema(connection):
        connection.execute("""CREATE TABLE IF NOT EXISTS p1_issuances (
            draft_id TEXT PRIMARY KEY, action_id TEXT NOT NULL, material_hash TEXT NOT NULL,
            issued_at TEXT NOT NULL, charges TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS p1_audit (
            sequence INTEGER PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL,
            payload TEXT NOT NULL, previous_hash TEXT NOT NULL, entry_hash TEXT NOT NULL UNIQUE)""")
        # Guard accidental UPDATE/DELETE. A database administrator can remove these;
        # external trusted checkpoints, not triggers, establish a tamper boundary.
        for table in ("p1_audit", "p1_issuances"):
            for operation in ("UPDATE", "DELETE"):
                connection.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                    BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END""")

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            os.chmod(self.path, 0o600)
            self.ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    @staticmethod
    def export_connection(connection):
        entries = []
        for row in connection.execute("SELECT * FROM p1_audit ORDER BY sequence LIMIT 100001"):
            entries.append({"sequence": row["sequence"], "created_at": row["created_at"], "kind": row["kind"],
                            "payload": strict_json_loads(row["payload"]), "previous_hash": row["previous_hash"],
                            "entry_hash": row["entry_hash"]})
        return {"schema": "pharos-payment-audit-chain/1", "entries": entries,
                "tip": entries[-1]["entry_hash"] if entries else ZERO_HASH}

    @classmethod
    def append_to(cls, connection, kind, payload, now=None):
        now = _utc(now)
        if (not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", kind)
                or not isinstance(payload, dict)):
            raise ValueError("Invalid audit event")
        chain = cls.export_connection(connection)
        verify_audit_chain(chain)
        if len(chain["entries"]) >= 100000:
            raise AuditIntegrityError("Audit capacity reached; archive externally before starting a new reviewed ledger")
        if chain["entries"] and now < datetime.fromisoformat(chain["entries"][-1]["created_at"]):
            raise AuditIntegrityError("Clock moved backwards; issuance is blocked")
        material = {"sequence": len(chain["entries"])+1, "created_at": now.isoformat(), "kind": kind,
                    "payload": deepcopy(payload), "previous_hash": chain["tip"]}
        event = {**material, "entry_hash": "0x" + sha256_json(material)}
        connection.execute("INSERT INTO p1_audit VALUES(?,?,?,?,?,?)", (
            event["sequence"], event["created_at"], kind, json.dumps(payload, ensure_ascii=False),
            event["previous_hash"], event["entry_hash"]))
        return event

    @staticmethod
    def verify_projection(connection, chain):
        expected = {}
        for entry in chain["entries"]:
            if entry["kind"] == "draft_budget_reserved":
                payload = entry["payload"]
                if payload["draft_id"] in expected:
                    raise AuditIntegrityError("Duplicate issuance audit record")
                expected[payload["draft_id"]] = {
                    "draft_id": payload["draft_id"], "action_id": payload["action_id"],
                    "material_hash": payload["material_hash"], "issued_at": entry["created_at"],
                    "charges": payload["charges"],
                }
        actual = {}
        for row in connection.execute("SELECT * FROM p1_issuances"):
            actual[row["draft_id"]] = {**dict(row), "charges": strict_json_loads(row["charges"])}
        if actual != expected:
            raise AuditIntegrityError("Issuance rows do not match the append-only audit")

    @classmethod
    def authorize_in(cls, connection, *, draft, action_id, charges, window_seconds, now=None):
        """Caller MUST own BEGIN IMMEDIATE; commit with the action state, not before."""
        if not connection.in_transaction:
            raise ValueError("An immediate write transaction is required")
        now = _utc(now)
        if (not isinstance(draft, dict) or not re.fullmatch(r"[0-9a-f]{32}", str(draft.get("id", "")))
                or not re.fullmatch(r"[0-9a-f]{32}", action_id)
                or type(window_seconds) is not int or not 60 <= window_seconds <= 2678400
                or not isinstance(charges, list) or not 1 <= len(charges) <= 4):
            raise ValueError("Invalid issuance request")
        scopes = set()
        for item in charges:
            if (not isinstance(item, dict) or set(item) != {"scope", "asset", "units", "limit_units"}
                    or any(not isinstance(value, str) for value in item.values())
                    or not re.fullmatch(r"(?:mcp|demo|live):[0-9]+:0x[0-9a-f]{40}:[A-Z0-9]{1,12}", item["scope"])
                    or item["scope"] in scopes
                    or not isinstance(item["asset"], str) or not item["scope"].endswith(":" + item["asset"])
                    or not re.fullmatch(r"[1-9][0-9]{0,77}", item["units"])
                    or not re.fullmatch(r"[1-9][0-9]{0,77}", item["limit_units"])
                    or not 0 < int(item["units"]) < 2**256 or not 0 < int(item["limit_units"]) < 2**256):
                raise ValueError("Invalid issuance charges")
            scopes.add(item["scope"])
        material_hash = sha256_json({"action_id": action_id, "draft": draft, "charges": charges,
                                    "window_seconds": window_seconds})
        chain = cls.export_connection(connection)
        verify_audit_chain(chain)
        cls.verify_projection(connection, chain)
        if chain["entries"] and now < datetime.fromisoformat(chain["entries"][-1]["created_at"]):
            raise AuditIntegrityError("Clock moved backwards; issuance is blocked")
        old = connection.execute("SELECT * FROM p1_issuances WHERE draft_id=?", (draft["id"],)).fetchone()
        if old:
            if old["material_hash"] != material_hash:
                raise ValueError("Draft ID was reused with different details")
            return {"status": "already_counted", "counting_basis": "unsigned_draft_issued"}
        nonces = draft_nonces(draft)
        requested = {(item["scope"], item["nonce"]) for item in nonces}
        for entry in chain["entries"]:
            if entry["kind"] != "draft_budget_reserved" or not nonces:
                continue
            previous = entry["payload"]
            if "nonce_reservations" not in previous:
                for charge in previous["charges"]:
                    scope = charge["scope"].split(":")
                    if scope[0] == "mcp" and ":".join(scope[1:3]) == nonces[0]["scope"]:
                        raise BudgetDenied("Legacy mainnet exports need nonce reconciliation before new exports.")
            for item in previous.get("nonce_reservations", []):
                if (item["scope"], item["nonce"]) in requested:
                    raise BudgetDenied("Pending nonce is already reserved by a local export. "
                                       "Wait for chain progress and review again; cancellation does not release it.")
        cutoff = (now - timedelta(seconds=window_seconds)).isoformat()
        used = {scope: 0 for scope in scopes}
        for row in connection.execute("SELECT charges FROM p1_issuances WHERE issued_at > ?", (cutoff,)):
            for charge in strict_json_loads(row[0]):
                if charge["scope"] in used:
                    used[charge["scope"]] += int(charge["units"])
        for charge in charges:
            if used[charge["scope"]] + int(charge["units"]) > int(charge["limit_units"]):
                raise BudgetDenied(f"Rolling unsigned-draft budget exceeded for {charge['asset']}")
        connection.execute("INSERT INTO p1_issuances VALUES(?,?,?,?,?)", (
            draft["id"], action_id, material_hash, now.isoformat(), json.dumps(charges)))
        event = cls.append_to(connection, "draft_budget_reserved", {
            "draft_id": draft["id"], "action_id": action_id, "charges": charges,
            "material_hash": material_hash, "window_seconds": window_seconds,
            "nonce_reservations": nonces,
            "counting_basis": "unsigned_draft_issued"}, now)
        return {"status": "counted", "counting_basis": "unsigned_draft_issued", "charges": charges,
                "window_seconds": window_seconds, "audit_hash": event["entry_hash"],
                "nonce_reservations": nonces,
                "boundary": "Includes conservatively reserved/failed exports, not actual on-chain spend; cancellation does not refund."}

    def authorize(self, **kwargs):
        with self.transaction() as connection:
            return self.authorize_in(connection, **kwargs)

    def append(self, kind, payload, now=None):
        with self.transaction() as connection:
            return self.append_to(connection, kind, payload, now)

    def export(self):
        with self.transaction() as connection:
            value = self.export_connection(connection)
            verify_audit_chain(value)
            self.verify_projection(connection, value)
            return value


def verify_report_files(chain, directory):
    """Compare retained JSON records to hashes in an already verified export."""
    verify_audit_chain(chain)
    checked = 0
    for entry in chain["entries"]:
        if entry["kind"] != "report_recorded":
            continue
        record_id = entry["payload"].get("record_id")
        if not isinstance(record_id, str) or not re.fullmatch(r"[0-9a-f]{32}", record_id):
            raise AuditIntegrityError("Invalid record reference")
        path = Path(directory) / (record_id + ".json")
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 2_000_000:
            raise AuditIntegrityError("Referenced report is missing or invalid")
        record = strict_json_loads(path.read_text())
        if "0x" + sha256_json(record) != entry["payload"].get("record_hash"):
            raise AuditIntegrityError("Referenced report hash mismatch")
        checked += 1
    return {"report_files_verified": checked}
