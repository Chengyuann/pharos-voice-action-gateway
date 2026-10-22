#!/usr/bin/env python3
"""Read-only live proof collection, or offline replay (not proof of chain authenticity)."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from payment_chain import (
    SKILL_COMMIT, collect_chain_state, decide_mainnet_payment,
    load_mainnet_registry, make_mainnet_review,
)
from pharos_mcp import DEFAULT_MCP_URL, McpToolError
from pharos_voice_action_gateway import sha256_json, strict_json_loads, validate_policy


class ReplayMismatch(RuntimeError):
    pass


class ReplayClient:
    """Replays exactly the recorded calls; it has no network transport."""
    def __init__(self, trace):
        if not isinstance(trace, list) or not 1 <= len(trace) <= 32:
            raise ValueError("Invalid trace length")
        self.entries = deepcopy(trace)
        self.index = 0

    def call(self, tool, arguments=None):
        if self.index >= len(self.entries):
            raise ReplayMismatch("Missing recorded Chain MCP call")
        entry = self.entries[self.index]
        self.index += 1
        if (not isinstance(entry, dict) or entry.get("tool") != tool
                or entry.get("arguments") != (arguments or {})
                or set(entry) not in ({"tool", "arguments", "result"}, {"tool", "arguments", "error"})):
            raise ReplayMismatch(f"Call {self.index} does not match {tool}")
        if "error" in entry:
            value = entry["error"]
            error = value.get("error") if isinstance(value, dict) else None
            message = error.get("message") if isinstance(error, dict) else None
            raise McpToolError(message or f"{tool} failed")
        return deepcopy(entry["result"])


def verify_proof(proof):
    if not isinstance(proof, dict) or proof.get("schema") != "pharos-payment-proof/1":
        raise ValueError("Unsupported proof schema")
    material = deepcopy(proof)
    digest = material.pop("digest", None)
    if digest != "0x" + sha256_json(material):
        raise ValueError("Proof digest mismatch")
    if proof.get("endpoint") != DEFAULT_MCP_URL or proof.get("transport") != "Streamable HTTP":
        raise ValueError("Unexpected claimed MCP endpoint or transport")
    if proof.get("skill") != {
        "name": "pharos-payment-review", "upstream_commit": SKILL_COMMIT,
        "handler": "payment_chain.decide_mainnet_payment", "fee_rule": "ceil(estimate_gas * 1.2)",
    }:
        raise ValueError("Unexpected skill definition")
    if proof.get("unpinned") != [
        "get_transaction_count: pending", "estimate_gas: current", "get_gas_price: current",
    ]:
        raise ValueError("Proof does not disclose unpinned fee / nonce calls")
    payment, sender = proof["input"]["payment"], proof["input"]["sender"]
    token = next((t for t in load_mainnet_registry()["tokens"] if t["symbol"] == payment["token"]), None)
    if token is None or token != proof.get("token"):
        raise ValueError("Token does not match the pinned official asset configuration")
    validate_policy(proof["policy"])
    now = datetime.fromisoformat(proof["evaluated_at"].replace("Z", "+00:00"))
    if now.tzinfo is None:
        raise ValueError("Proof time must be timezone-aware")
    replay = ReplayClient(proof["trace"])
    state = collect_chain_state(replay, payment, sender, token)
    if replay.index != len(replay.entries):
        raise ValueError("Unused Chain MCP calls in proof")
    if state != proof["state"]:
        raise ValueError("State does not replay from the recorded calls")
    head = datetime.fromtimestamp(state["block_timestamp"], timezone.utc)
    if not -30 <= (now - head).total_seconds() <= 600:
        raise ValueError("Recorded block was stale or future-dated")
    decision = decide_mainnet_payment(payment, state, token, proof["policy"], now)
    if decision != proof["decision"]:
        raise ValueError("Decision does not replay")
    transaction = None
    if decision["outcome"] == "allow":
        transaction = {"chainId": 1672, **state["transaction"], "nonce": hex(state["nonce"]),
                       "gas": hex(state["gas"]), "gasPrice": hex(state["gas_price"])}
    if transaction != proof.get("transaction"):
        raise ValueError("Unsigned transaction does not match the checked payment")
    return {
        "verified": True, "scope": "offline self-consistency only",
        "chain_authenticity_verified": False, "authorization_verified": False,
        "calls": replay.index, "block": state["block_number"],
        "decision": decision["outcome"], "deciding_rule": decision["deciding_rule"],
        "signed": False, "broadcast": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("file", type=Path)
    review = sub.add_parser("review")
    review.add_argument("request")
    review.add_argument("--sender", required=True, help="Public lowercase address to inspect; not ownership proof")
    review.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            if args.file.stat().st_size > 2_000_000:
                raise ValueError("Proof exceeds 2 MB")
            value = strict_json_loads(args.file.read_text(encoding="utf-8"))
            result = verify_proof(value.get("proof", value))
            print(json.dumps(result, indent=2))
        else:
            result = make_mainnet_review(args.request, sender=args.sender)
            print(f"{result['status'].upper()}: {result.get('payment')}")
            for check in result["checks"]:
                print(f"  {'PASS' if check['passed'] else 'FAIL'} {check['label']}: {check['detail']}")
            if "proof" not in result:
                raise ValueError(result.get("message") or result.get("question") or "No proof collected")
            verify_proof(result["proof"])
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump(result["proof"], stream, ensure_ascii=False, indent=2, allow_nan=False)
            print(f"Proof: {args.output}. No signature or broadcast. Use the local UI for confirmed draft handoff.")
        return 0
    except (ValueError, ReplayMismatch, KeyError, TypeError, OSError, OverflowError) as exc:
        print(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
