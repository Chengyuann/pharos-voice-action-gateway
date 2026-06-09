#!/usr/bin/env python3
"""Smoke tests for the Pharos Voice Action Gateway."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pharos_voice_action_gateway import run


def assert_payment_confirmed(root: Path) -> None:
    result = run(root / "demo" / "pharos_payment_confirmed.jsonl")
    assert result.intent["action"] == "send_payment", result.intent
    assert result.intent["requires_confirmation"] is True, result.intent
    assert result.confirmation["status"] == "confirmed", result.confirmation
    assert result.prepared_action["policy_decision"]["decision"] == "approved_for_confirmation", result.prepared_action
    assert result.prepared_action["mandate"]["mandate_hash"].startswith("0x"), result.prepared_action
    assert result.prepared_action["challenge"]["type"] == "readback_phrase", result.prepared_action
    assert result.submission["status"] == "simulated", result.submission
    assert result.submission["tx_hash"].startswith("0x"), result.submission
    assert result.prepared_action["transaction_preview"]["amount"] == "0.02", result.prepared_action
    print("PASS pharos_payment_confirmed: mandate + policy + confirmation + simulated tx")


def assert_payment_pending(root: Path) -> None:
    result = run(root / "demo" / "pharos_payment_pending.jsonl")
    assert result.intent["action"] == "send_payment", result.intent
    assert result.prepared_action["policy_decision"]["decision"] == "approved_for_confirmation", result.prepared_action
    assert result.confirmation["status"] == "pending_confirmation", result.confirmation
    assert result.submission["status"] == "blocked_by_confirmation_gate", result.submission
    assert not result.submission["tx_hash"], result.submission
    print("PASS pharos_payment_pending: high-risk action blocked without confirmation")


def assert_payment_policy_blocked(root: Path) -> None:
    result = run(root / "demo" / "pharos_payment_policy_blocked.jsonl")
    assert result.intent["action"] == "send_payment", result.intent
    assert result.confirmation["status"] == "confirmed", result.confirmation
    assert result.prepared_action["policy_decision"]["decision"] == "blocked_by_policy", result.prepared_action
    assert "amount_limit" in result.prepared_action["policy_decision"]["blocking_reasons"], result.prepared_action
    assert "recipient_trust" in result.prepared_action["policy_decision"]["blocking_reasons"], result.prepared_action
    assert result.submission["status"] == "blocked_by_policy", result.submission
    print("PASS pharos_payment_policy_blocked: confirmation cannot bypass policy")


def assert_session_proof(root: Path) -> None:
    result = run(root / "demo" / "pharos_session_proof_confirmed.jsonl")
    assert result.intent["action"] == "write_session_proof", result.intent
    assert result.prepared_action["transaction_preview"]["type"] == "session_proof_attestation", result.prepared_action
    assert result.prepared_action["proof_payload"]["voice_hash"].startswith("0x"), result.prepared_action
    assert result.prepared_action["proof_payload"]["mandate_hash"].startswith("0x"), result.prepared_action
    assert len(result.mcp_tools) == 6, result.mcp_tools
    print("PASS pharos_session_proof_confirmed: proof payload + MCP tool schema")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    assert_payment_confirmed(root)
    assert_payment_pending(root)
    assert_payment_policy_blocked(root)
    assert_session_proof(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
