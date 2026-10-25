#!/usr/bin/env python3
"""Collect or verify batch review evidence. Never confirms, reserves, signs or sends."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

from evm_address import validate_address
from payment_batch import make_batch_review, aggregate_proofs, BATCH_BOUNDARY
from payment_preview import review_fingerprint, units_to_amount
from pharos_mcp import DEFAULT_MCP_URL
from pharos_voice_action_gateway import sha256_json, strict_json_loads

SCHEMA = "pharos-batch-evidence/1"
BOUNDARY = ("Historical read-only observations. Self-consistency is not authenticated chain provenance, "
            "wallet ownership or payment authority. No confirmation, reservation, signing or broadcast.")


def envelope(review, elapsed):
    value = {
        "schema": SCHEMA, "capture_method": "official_mcp_readonly",
        "endpoint": DEFAULT_MCP_URL, "captured_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed, 3), "review": deepcopy(review),
        "confirmed": False, "reserved": False, "signed": False, "broadcast": False,
        "boundary": BOUNDARY,
    }
    value["digest"] = "0x" + sha256_json(value)
    return value


def verify_evidence(value):
    """Recompute every displayed payment, balance total, fee and nonce from row proofs."""
    if (not isinstance(value, dict) or set(value) != {
            "schema", "capture_method", "endpoint", "captured_at", "elapsed_seconds", "review",
            "confirmed", "reserved", "signed", "broadcast", "boundary", "digest"}
            or value["schema"] != SCHEMA or value["endpoint"] != DEFAULT_MCP_URL
            or value["capture_method"] != "official_mcp_readonly" or value["boundary"] != BOUNDARY
            or any(value[key] is not False for key in ("confirmed", "reserved", "signed", "broadcast"))):
        raise ValueError("Unsupported read-only batch evidence")
    material = {key: item for key, item in value.items() if key != "digest"}
    if value["digest"] != "0x" + sha256_json(material):
        raise ValueError("Batch evidence digest mismatch")
    if (type(value["elapsed_seconds"]) not in (int, float)
            or not 0 <= value["elapsed_seconds"] <= 65):
        raise ValueError("Invalid evidence collection duration")
    captured = datetime.fromisoformat(value["captured_at"])
    review = value["review"]
    if (not isinstance(review, dict) or review.get("kind") != "batch"
            or review.get("record_type") != "payment_review" or review.get("mode") != "mcp"
            or review.get("integrity_hash") != review_fingerprint(review)
            or review.get("boundary") != BATCH_BOUNDARY or review.get("recorded") is not False
            or not isinstance(review.get("items"), list) or not 1 <= len(review["items"]) <= 5
            or any(key in review for key in ("action_id", "action_revision", "rolling_budget", "token"))):
        raise ValueError("Expected an intact batch review without action credentials")
    started, expires = (datetime.fromisoformat(review[key]) for key in ("created_at", "expires_at"))
    if (any(moment.tzinfo is None for moment in (captured, started, expires))
            or not 0 <= (captured-started).total_seconds() <= 65 or expires <= started):
        raise ValueError("Invalid batch evidence timestamps")
    if review.get("payments") != review.get("plan", {}).get("payments"):
        raise ValueError("Displayed batch rows disagree with the plan")
    proofs = [item["proof"] for item in review["items"]]
    aggregate = aggregate_proofs(review["plan"], proofs)
    if review.get("aggregate") != aggregate:
        raise ValueError("Batch aggregate does not replay")
    row_configs = set()
    for row, payment, proof in zip(review["items"], review["payments"], proofs):
        state = proof["state"]
        if (row.get("integrity_hash") != review_fingerprint(row)
                or row.get("record_type") != "payment_review" or row.get("mode") != "mcp"
                or row.get("status") != "ready" or row.get("draft_allowed") is not True
                or row.get("prepared_transaction") != proof["transaction"]
                or row.get("payment") != {k: payment[k] for k in ("amount", "token", "recipient")}
                or row.get("wallet", {}).get("address") != proof["input"]["sender"]
                or row.get("policy_decision") != proof["decision"]):
            raise ValueError("Row review does not agree with its replayed proof")
        created, evaluated = (datetime.fromisoformat(row["created_at"]),
                              datetime.fromisoformat(proof["evaluated_at"]))
        if (created.tzinfo is None or not started <= created <= evaluated <= captured
                or (evaluated-started).total_seconds() > 60):
            raise ValueError("Row evidence is outside the batch collection window")
        expected_fee = {"status": "estimated", "amount": units_to_amount(state["gas"] * state["gas_price"], 18),
                        "symbol": "PROS", "gas": state["gas"], "gas_price": state["gas_price"],
                        "source": "chain_mcp", "native_available": units_to_amount(state["native_balance"], 18)}
        expected_balance = {"available": units_to_amount(state["token_balance"], proof["token"]["decimals"]),
                            "required": payment["amount"], "source": "chain_mcp"}
        if row.get("fee") != expected_fee or row.get("balance") != expected_balance:
            raise ValueError("Displayed row fee or balance disagrees with its proof")
        network = row.get("network", {})
        if (network.get("chain_id") != 1672 or network.get("source") != "chain_mcp"
                or network.get("block_number") != state["block_number"]
                or network.get("block_hash") != state["block_hash"]
                or network.get("fetched_at") != proof["evaluated_at"]):
            raise ValueError("Row network does not match its proof")
        config = row.get("configuration_hash", "")
        if not isinstance(config, str) or not re.fullmatch(r"0x[0-9a-f]{64}", config):
            raise ValueError("Missing configuration binding")
        row_configs.add(config)
        secondary = row.get("secondary_check")
        if secondary is not None and (secondary.get("status") != "matched"
                or secondary.get("block") != state["block_number"] or not secondary.get("checks")
                or any(check.get("matches") is not True for check in secondary["checks"])):
            raise ValueError("Additional configured RPC check did not pass")
    first = review["items"][0]
    if len(row_configs) != 1 or review.get("configuration_hash") not in row_configs:
        raise ValueError("Batch configurations differ")
    secondary_present = ["secondary_check" in row for row in review["items"]]
    if any(secondary_present) and not all(secondary_present):
        raise ValueError("Secondary checks must cover every row")
    if review.get("network") != first["network"] or review.get("wallet") != first["wallet"]:
        raise ValueError("Batch network or sender disagrees with row evidence")
    expected_fee = {"status": "estimated", "amount": units_to_amount(int(aggregate["native_fee_budget_wei"]), 18),
                    "symbol": "PROS"}
    if review.get("fee") != expected_fee:
        raise ValueError("Displayed total fee disagrees with row proofs")
    nonce = proofs[0]["state"]["nonce"]
    sequence = [str(nonce+i) for i in range(len(proofs))]
    if (review.get("pending_nonce") != str(nonce) or review.get("nonce_sequence") != sequence
            or int(sequence[-1]) >= 2**64-1):
        raise ValueError("Batch nonce ordering is invalid")
    ready = aggregate["status"] == "candidate_plan"
    if review.get("status") != ("ready" if ready else "blocked") or review.get("draft_allowed") is not ready:
        raise ValueError("Batch status does not follow aggregate checks")
    expected_checks = [{"name": c["name"], "label": c["name"].replace("_", " ").title(),
                        "passed": c["passed"], "detail": "Whole-batch check", "source": "chain_mcp_skill"}
                       for c in aggregate["checks"]]
    if review.get("checks") != expected_checks:
        raise ValueError("Displayed checks disagree with aggregate")
    return {
        "verified": True, "scope": "offline_self_consistency_only",
        "chain_authenticity_verified": False, "authorization_verified": False,
        "status": review["status"], "rows": len(proofs), "block": str(aggregate["block"]),
        "calls": sum(len(proof["trace"]) for proof in proofs),
        "secondary_checks": sum(len(row.get("secondary_check", {}).get("checks", [])) for row in review["items"]),
        "nonce_sequence": sequence, "signed": False, "broadcast": False,
    }


def load_evidence(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("Evidence must be a regular JSON file under 2 MB")
    return strict_json_loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect", help="Read official MCP only; no action API or export reservation")
    collect.add_argument("request")
    collect.add_argument("--sender", required=True, help="Public address to inspect; no ownership claim")
    collect.add_argument("--output", required=True, type=Path)
    verify = sub.add_parser("verify", help="Replay a historical batch capture offline")
    verify.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            result = verify_evidence(load_evidence(args.file))
        else:
            sender = validate_address(args.sender)
            with args.output.open("x", encoding="utf-8") as stream:
                started = time.monotonic()
                review = make_batch_review(args.request, sender=sender)
                value = envelope(review, time.monotonic()-started)
                json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            if review["status"] not in {"ready", "blocked"} or "aggregate" not in review:
                raise ValueError(f"Capture retained as {review['status']}: {review['message']}")
            result = verify_evidence(value)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, OverflowError, RuntimeError) as exc:
        print(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
