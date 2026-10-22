"""Local action lifecycle. Capability tokens are not user identity or wallet authority."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import os
import re
import secrets
import sqlite3
from uuid import uuid4
from datetime import datetime, timezone

from payment_preview import parse_payment_request
from pharos_voice_action_gateway import amount_to_units
from payment_ledger import PaymentLedger, BudgetDenied, load_budget_policy, draft_charges


PROCESS_EPOCH = uuid4().hex


class ActionConflict(ValueError):
    pass


class ActionAccessDenied(ValueError):
    pass


def validate_input(payload):
    if not isinstance(payload, dict) or set(payload) != {"text", "answers", "mode"}:
        raise ValueError("expected text, answers and mode")
    if not isinstance(payload["text"], str) or not 1 <= len(payload["text"].strip()) <= 500:
        raise ValueError("instruction must contain 1-500 characters")
    if payload["mode"] not in ("demo", "live", "mcp"):
        raise ValueError("mode must be demo, live or mcp")
    answers = payload["answers"]
    if (not isinstance(answers, dict) or set(answers) - {"amount", "token", "recipient"}
            or any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 100 for v in answers.values())):
        raise ValueError("invalid payment answers")
    return deepcopy(payload)


def revise_amount(payload, instruction):
    """Only explicit, single-field corrections are accepted; ambiguous input is not guessed."""
    payload = validate_input(payload)
    if not isinstance(instruction, str) or not 1 <= len(instruction.strip()) <= 100:
        raise ValueError("correction must contain 1-100 characters")
    match = re.fullmatch(
        r"(?:不是[，, ]*)?(?:改成|金额改为|金额改成|change(?: the)?(?: amount)? to)\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*(PHRS|PROS|USDC|WPROS)?[。.!]?",
        instruction.strip(), re.I,
    )
    parser = parse_payment_request
    if payload["mode"] == "mcp":
        from payment_language import parse_mainnet_request
        parser = parse_mainnet_request
    parsed = parser(payload["text"], payload["answers"])
    if not match or parsed["status"] != "complete":
        raise ValueError("Use an explicit amount correction, or rewrite the full payment request.")
    amount = match.group(1)
    if int(amount_to_units(amount)) <= 0:
        raise ValueError("The corrected amount must be positive.")
    payment = parsed["payment"]
    if match.group(2) and payment["token"] != match.group(2).upper():
        raise ValueError("An amount correction cannot change the token.")
    return {
        "text": f"Send {amount} {payment['token']} to {payment['recipient']}",
        "answers": {}, "mode": payload["mode"],
    }, {"field": "amount", "before": payment["amount"], "after": amount}


class ActionStore:
    def __init__(self, path: Path, epoch: str = PROCESS_EPOCH, budget_policy_path=None):
        self.path = path
        self.epoch = epoch
        self.budget_policy_path = budget_policy_path

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            os.chmod(self.path, 0o600)
            connection.execute("""CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, epoch TEXT NOT NULL,
                revision INTEGER NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                review_id TEXT NOT NULL DEFAULT '', draft_id TEXT NOT NULL DEFAULT ''
            )""")
            PaymentLedger.ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _get(self, connection, action_id, token, revision=None):
        if (not isinstance(action_id, str) or not re.fullmatch(r"[a-f0-9]{32}", action_id)
                or not isinstance(token, str) or not 32 <= len(token) <= 100):
            raise ActionAccessDenied("Invalid action session.")
        row = connection.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        digest = hashlib.sha256(token.encode()).hexdigest()
        if row is None or not hmac.compare_digest(row["token_hash"], digest):
            raise ActionAccessDenied("Invalid action session.")
        if row["epoch"] != self.epoch:
            raise ActionConflict("The server session ended. Start a new payment review.")
        if revision is not None:
            if type(revision) is not int or revision < 0:
                raise ValueError("revision must be a non-negative integer")
            if row["revision"] != revision:
                raise ActionConflict("The instruction changed. Review the current version.")
        return dict(row)

    @staticmethod
    def public(row):
        return {
            "action_id": row["id"], "revision": row["revision"], "status": row["status"],
            "review_id": row["review_id"], "draft_id": row["draft_id"],
        }

    def create(self):
        action_id, token = uuid4().hex, secrets.token_urlsafe(32)
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO actions (id, token_hash, epoch, revision, status, payload) VALUES (?, ?, ?, 0, 'editing', '{}')",
                (action_id, hashlib.sha256(token.encode()).hexdigest(), self.epoch),
            )
            row = self._get(connection, action_id, token)
        return {**self.public(row), "token": token}

    def update(self, action_id, token, revision, payload):
        payload = validate_input(payload)
        with self.transaction() as connection:
            row = self._get(connection, action_id, token, revision)
            if row["status"] == "cancelled":
                raise ActionConflict("This action was cancelled. Start a new review.")
            connection.execute(
                "UPDATE actions SET revision = revision + 1, status = 'editing', payload = ?, review_id = '', draft_id = '' WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), action_id),
            )
            row = self._get(connection, action_id, token)
        return self.public(row)

    def invalidate(self, action_id, token, revision):
        with self.transaction() as connection:
            row = self._get(connection, action_id, token, revision)
            if row["status"] == "cancelled":
                raise ActionConflict("This action was cancelled. Start a new review.")
            connection.execute(
                "UPDATE actions SET revision = revision + 1, status = 'editing', review_id = '', draft_id = '' WHERE id = ?",
                (action_id,),
            )
            return self.public(self._get(connection, action_id, token))

    def correct(self, action_id, token, revision, instruction):
        with self.transaction() as connection:
            row = self._get(connection, action_id, token, revision)
            if row["status"] == "cancelled":
                raise ActionConflict("This action was cancelled. Start a new review.")
            previous = json.loads(row["payload"])
            try:
                payload, change = revise_amount(previous, instruction)
                message = "Amount corrected. A new review is required."
            except ValueError as exc:
                # Even an unclear correction invalidates the old review and confirmation.
                payload = {"text": instruction, "answers": {}, "mode": previous.get("mode", "demo")}
                payload = validate_input(payload)
                change, message = None, str(exc)
            connection.execute(
                "UPDATE actions SET revision = revision + 1, status = 'editing', payload = ?, review_id = '', draft_id = '' WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), action_id),
            )
            result = self.public(self._get(connection, action_id, token))
        return {**result, "input": payload, "change": change, "message": message}

    def cancel(self, action_id, token):
        with self.transaction() as connection:
            row = self._get(connection, action_id, token)
            if row["status"] != "cancelled":
                connection.execute(
                    "UPDATE actions SET revision = revision + 1, status = 'cancelled', review_id = '' WHERE id = ?",
                    (action_id,),
                )
                PaymentLedger.append_to(connection, "action_cancelled", {
                    "action_id": action_id, "revision": row["revision"],
                    "issued_budget_refunded": False,
                })
            return self.public(self._get(connection, action_id, token))

    def begin_review(self, action_id, token, revision):
        with self.transaction() as connection:
            row = self._get(connection, action_id, token, revision)
            if row["status"] not in ("editing", "reviewed", "confirmed"):
                raise ActionConflict("Action is cancelled or already processing.")
            payload = validate_input(json.loads(row["payload"]))
            connection.execute("UPDATE actions SET status = 'reviewing', review_id = '' WHERE id = ?", (action_id,))
        return payload

    def finish_review(self, action_id, token, revision, review, save):
        with self.transaction() as connection:
            row = self._get(connection, action_id, token, revision)
            if row["status"] != "reviewing":
                raise ActionConflict("Review was superseded or cancelled.")
            save(review)
            PaymentLedger.append_to(connection, "review_recorded", {
                "action_id": action_id, "revision": revision, "review_id": review["id"],
                "status": review.get("status"), "review_hash": review.get("integrity_hash"),
            })
            connection.execute("UPDATE actions SET status = 'reviewed', review_id = ? WHERE id = ?", (review["id"], action_id))

    def fail(self, action_id, token, revision, expected_status):
        with self.transaction() as connection:
            row = self._get(connection, action_id, token)
            statuses = {expected_status, "draft_reserved"} if expected_status == "drafting" else {expected_status}
            if row["revision"] == revision and row["status"] in statuses:
                connection.execute("UPDATE actions SET status = 'editing', review_id = '' WHERE id = ?", (action_id,))

    def confirm(self, action_id, token, revision, review_id, validate):
        with self.transaction() as connection:
            row = self._get(connection, action_id, token, revision)
            if row["status"] not in ("reviewed", "confirmed") or row["review_id"] != review_id:
                raise ActionConflict("Only the current review can be confirmed.")
            validate()
            PaymentLedger.append_to(connection, "review_confirmed", {
                "action_id": action_id, "revision": revision, "review_id": review_id,
            })
            connection.execute("UPDATE actions SET status = 'confirmed' WHERE id = ?", (action_id,))
            return self.public(self._get(connection, action_id, token))

    def begin_draft(self, action_id, token, revision, review_id):
        with self.transaction() as connection:
            row = self._get(connection, action_id, token, revision)
            if row["review_id"] != review_id:
                raise ActionConflict("This review does not belong to the current action version.")
            if row["status"] == "drafted":
                return row["draft_id"]
            if row["status"] != "confirmed":
                raise ActionConflict("Confirm the current review before creating a draft.")
            connection.execute("UPDATE actions SET status = 'drafting' WHERE id = ?", (action_id,))
        return ""

    def finish_draft(self, action_id, token, revision, draft, save):
        # Commit a conservative budget reservation BEFORE publishing a JSON file.
        # A crash or failed save can consume budget, never create an uncounted
        # export. Cancellation/revision is rechecked in a second transaction.
        policy = (load_budget_policy(self.budget_policy_path) if self.budget_policy_path
                  else load_budget_policy())
        if draft.get("issuance_policy") != policy:
            raise ActionConflict("The rolling issuance policy changed. Review and confirm again.")
        denied = None
        with self.transaction() as connection:
            row = self._get(connection, action_id, token, revision)
            if row["status"] != "drafting":
                raise ActionConflict("Draft was superseded or cancelled.")
            try:
                charges = draft_charges(draft, policy)
                receipt = PaymentLedger.authorize_in(connection, draft=draft, action_id=action_id,
                    charges=charges, window_seconds=policy["window_seconds"])
            except BudgetDenied as exc:
                denied = str(exc)
                PaymentLedger.append_to(connection, "budget_denied", {
                    "action_id": action_id, "revision": revision, "draft_id": draft["id"],
                    "reason": denied,
                })
                connection.execute("UPDATE actions SET status='editing', review_id='' WHERE id=?", (action_id,))
            else:
                connection.execute("UPDATE actions SET status='draft_reserved' WHERE id=?", (action_id,))
        if denied:
            raise ActionConflict(denied)
        with self.transaction() as connection:
            row = self._get(connection, action_id, token, revision)
            if row["status"] != "draft_reserved":
                raise ActionConflict("Reserved draft was superseded or cancelled.")
            if datetime.now(timezone.utc) >= datetime.fromisoformat(draft["expires_at"]):
                raise ActionConflict("Draft expired during budget reservation. Review again.")
            latest_policy = (load_budget_policy(self.budget_policy_path) if self.budget_policy_path
                             else load_budget_policy())
            if latest_policy != policy:
                raise ActionConflict("Issuance policy changed during reservation. Review again.")
            draft["rolling_budget"] = receipt
            from payment_preview import review_fingerprint
            draft["integrity_hash"] = review_fingerprint(draft)
            save(draft)
            PaymentLedger.append_to(connection, "draft_published", {
                "action_id": action_id, "revision": revision, "draft_id": draft["id"],
                "draft_hash": draft["integrity_hash"],
            })
            connection.execute("UPDATE actions SET status = 'drafted', draft_id = ? WHERE id = ?", (draft["id"], action_id))
