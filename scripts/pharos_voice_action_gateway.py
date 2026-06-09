#!/usr/bin/env python3
"""Pharos Voice Action Gateway.

This module upgrades Local Duplex Voice Gateway from turn-taking into a
confirmation-gated on-chain action Skill. It is mock-first by design: the demo
can be validated without a wallet or RPC endpoint, while the output schema is
compatible with a later Pharos/EVM transaction adapter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from duplex_voice_gateway import GatewayState, load_events, process_events  # noqa: E402


PHAROS_TESTNET_CHAIN_ID = 688688
PHAROS_MAINNET_CHAIN_ID = 1672
DEFAULT_CHAIN = {
    "name": "Pharos Testnet",
    "chain_id": PHAROS_TESTNET_CHAIN_ID,
    "native_token": "PHRS",
    "rpc_env": "PHAROS_RPC_URL",
}

ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
AMOUNT_RE = re.compile(r"(?:send|pay|transfer|付款|转账|支付)\s*([0-9]+(?:\.[0-9]+)?)", re.I)
TOKEN_RE = re.compile(r"\b(PHRS|PROS|USDC|USDT|ETH)\b", re.I)
CONFIRM_RE = re.compile(r"(confirm|confirmed|yes execute|approve|send it|确认|确认执行|批准|可以执行|执行吧)", re.I)
CANCEL_RE = re.compile(r"(cancel|abort|stop|do not|don't|取消|停止|不要执行|别执行)", re.I)
HIGH_RISK_ACTIONS = {"send_payment", "contract_call", "write_session_proof"}
DEFAULT_POLICY = {
    "max_single_payment": "0.05",
    "native_token": "PHRS",
    "allowed_tokens": ["PHRS", "PROS", "USDC", "USDT"],
    "trusted_recipients": [
        "0x1111111111111111111111111111111111111111",
        "0x2222222222222222222222222222222222222222",
    ],
    "challenge_word_count": 3,
}


@dataclass
class VoiceIntent:
    action: str
    confidence: float
    summary: str
    raw_text: str
    params: dict[str, Any] = field(default_factory=dict)
    risk: str = "low"
    requires_confirmation: bool = False
    intent_hash: str = ""


@dataclass
class ConfirmationDecision:
    status: str
    confirmed: bool
    reason: str
    confirmation_text: str = ""


@dataclass
class PreparedAction:
    action_id: str
    chain: dict[str, Any]
    mode: str
    intent: VoiceIntent
    transaction_preview: dict[str, Any]
    policy: dict[str, Any]
    proof_payload: dict[str, Any]
    mandate: dict[str, Any]
    policy_decision: dict[str, Any]
    challenge: dict[str, Any]
    audit_timeline: list[dict[str, Any]]


@dataclass
class SubmissionResult:
    action_id: str
    mode: str
    status: str
    tx_hash: str
    explorer_url: str
    receipt: dict[str, Any]


@dataclass
class GatewayRun:
    source: str
    generated_at: str
    voice: dict[str, Any]
    intent: dict[str, Any]
    confirmation: dict[str, Any]
    prepared_action: dict[str, Any]
    submission: dict[str, Any]
    mcp_tools: list[dict[str, Any]]


def sha256_json(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def short_hash(data: Any, length: int = 16) -> str:
    return sha256_json(data)[:length]


def decimal_or_zero(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def extract_voice_intent(state: GatewayState) -> VoiceIntent:
    text = " ".join(state.committed_turns).strip()
    lower = text.lower()
    params: dict[str, Any] = {}

    action = "summarize_intent"
    confidence = 0.64
    summary = text or "No complete user intent was committed."
    risk = "low"

    if any(keyword in lower for keyword in ["balance", "余额", "查一下", "查询"]):
        action = "check_balance"
        confidence = 0.78
        summary = "Check wallet balance on Pharos."
        risk = "low"

    if any(keyword in lower for keyword in ["pay", "send", "transfer", "付款", "转账", "支付"]):
        action = "send_payment"
        confidence = 0.86
        summary = "Prepare a Pharos payment from a voice command."
        risk = "high"
        amount_match = AMOUNT_RE.search(text)
        if amount_match:
            params["amount"] = amount_match.group(1)
        token_match = TOKEN_RE.search(text)
        params["token"] = token_match.group(1).upper() if token_match else DEFAULT_CHAIN["native_token"]
        address_match = ADDRESS_RE.search(text)
        if address_match:
            params["to"] = address_match.group(0)
        else:
            params["to"] = "0x0000000000000000000000000000000000000000"
            params["needs_recipient_resolution"] = True

    if any(keyword in lower for keyword in ["proof", "record", "attest", "证明", "记录", "上链"]):
        if action == "summarize_intent":
            action = "write_session_proof"
            confidence = 0.82
            summary = "Write a hash of the voice session and user intent as an on-chain proof."
            risk = "medium"
        params["write_proof"] = True

    if state.interrupts:
        params["barge_in_detected"] = True

    intent = VoiceIntent(
        action=action,
        confidence=confidence,
        summary=summary,
        raw_text=text,
        params=params,
        risk=risk,
        requires_confirmation=action in HIGH_RISK_ACTIONS or risk in {"medium", "high"},
    )
    intent.intent_hash = "0x" + sha256_json({
        "action": intent.action,
        "summary": intent.summary,
        "raw_text": intent.raw_text,
        "params": intent.params,
        "risk": intent.risk,
    })
    return intent


def decide_confirmation(state: GatewayState, intent: VoiceIntent) -> ConfirmationDecision:
    if not intent.requires_confirmation:
        return ConfirmationDecision(status="not_required", confirmed=True, reason="low-risk action does not require explicit confirmation")

    texts = state.committed_turns[-3:]
    for text in reversed(texts):
        if CANCEL_RE.search(text):
            return ConfirmationDecision(status="cancelled", confirmed=False, reason="user cancelled the high-risk action", confirmation_text=text)
        if CONFIRM_RE.search(text):
            return ConfirmationDecision(status="confirmed", confirmed=True, reason="explicit user confirmation detected", confirmation_text=text)

    return ConfirmationDecision(
        status="pending_confirmation",
        confirmed=False,
        reason="high-risk voice action requires an explicit confirmation phrase before signing or submitting",
    )


def build_voice_mandate(state: GatewayState, intent: VoiceIntent, voice_hash: str) -> dict[str, Any]:
    """Build an AP2-inspired mandate from voice evidence without copying AP2."""
    scope = {
        "action": intent.action,
        "risk": intent.risk,
        "chain_id": DEFAULT_CHAIN["chain_id"],
        "token": intent.params.get("token", DEFAULT_CHAIN["native_token"]),
        "max_amount": intent.params.get("amount", "0"),
        "recipient": intent.params.get("to", "connected_wallet"),
    }
    mandate = {
        "type": "voice_mandate",
        "version": "0.1",
        "subject": "local_user",
        "scope": scope,
        "voice_hash": "0x" + voice_hash,
        "intent_hash": intent.intent_hash,
        "turns": len(state.committed_turns),
        "interrupts": state.interrupts,
        "expires_after_seconds": 300,
        "raw_audio_stored": False,
    }
    mandate["mandate_hash"] = "0x" + sha256_json(mandate)
    return mandate


def evaluate_policy(intent: VoiceIntent, mandate: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    amount = decimal_or_zero(intent.params.get("amount", "0"))
    max_single = decimal_or_zero(policy["max_single_payment"])
    token = str(intent.params.get("token", DEFAULT_CHAIN["native_token"])).upper()
    recipient = str(intent.params.get("to", ""))

    if intent.action == "send_payment":
        checks.append({
            "name": "amount_limit",
            "passed": amount <= max_single and amount > 0,
            "details": f"{amount} <= {max_single} {policy['native_token']}",
        })
        checks.append({
            "name": "token_allowlist",
            "passed": token in policy["allowed_tokens"],
            "details": token,
        })
        checks.append({
            "name": "recipient_trust",
            "passed": recipient in policy["trusted_recipients"],
            "details": recipient or "missing",
        })

    checks.append({
        "name": "voice_mandate_hash",
        "passed": bool(mandate.get("mandate_hash", "").startswith("0x")),
        "details": mandate.get("mandate_hash", ""),
    })
    checks.append({
        "name": "no_raw_audio_storage",
        "passed": mandate.get("raw_audio_stored") is False,
        "details": "stores hashes and text-event evidence only",
    })

    blocking = [check for check in checks if not check["passed"]]
    decision = "approved_for_confirmation" if not blocking else "blocked_by_policy"
    return {
        "decision": decision,
        "checks": checks,
        "blocking_reasons": [check["name"] for check in blocking],
        "policy_hash": "0x" + sha256_json(policy),
    }


def build_challenge(action_id: str, intent: VoiceIntent, mandate: dict[str, Any], policy_decision: dict[str, Any]) -> dict[str, Any]:
    words = ["PHAROS", action_id[-4:].upper(), mandate["mandate_hash"][2:8].upper()]
    phrase = " ".join(words)
    return {
        "required": intent.requires_confirmation or policy_decision["decision"] != "approved_for_confirmation",
        "type": "readback_phrase",
        "phrase": phrase,
        "why": "binds spoken approval to this exact action id and mandate hash",
        "accepted_confirmation_examples": [
            f"confirm {phrase}",
            f"approve {phrase}",
            f"确认执行 {phrase}",
        ],
    }


def build_audit_timeline(
    state: GatewayState,
    intent: VoiceIntent,
    confirmation: ConfirmationDecision,
    mandate: dict[str, Any],
    policy_decision: dict[str, Any],
) -> list[dict[str, Any]]:
    timeline = [
        {"step": "voice_events_processed", "result": f"{len(state.output_events)} turn-taking events"},
        {"step": "intent_committed", "result": intent.action, "intent_hash": intent.intent_hash},
        {"step": "voice_mandate_created", "result": mandate["mandate_hash"]},
        {"step": "policy_evaluated", "result": policy_decision["decision"], "policy_hash": policy_decision["policy_hash"]},
        {"step": "confirmation_checked", "result": confirmation.status},
    ]
    if state.interrupts:
        timeline.insert(1, {"step": "barge_in_detected", "result": f"{state.interrupts} interruption(s)"})
    return timeline


def build_transaction_preview(intent: VoiceIntent, voice_hash: str) -> dict[str, Any]:
    if intent.action == "send_payment":
        amount = intent.params.get("amount", "0")
        token = intent.params.get("token", DEFAULT_CHAIN["native_token"])
        return {
            "type": "native_or_token_transfer",
            "to": intent.params.get("to", "0x0000000000000000000000000000000000000000"),
            "amount": amount,
            "token": token,
            "value_hint": f"{amount} {token}",
            "data": "0x",
            "safety_note": "recipient resolution and wallet signature are required before live submission",
        }

    if intent.action == "write_session_proof":
        return {
            "type": "session_proof_attestation",
            "to": "VoiceSessionProofRegistry",
            "method": "recordVoiceIntent(bytes32 voiceHash, bytes32 intentHash, string action)",
            "args": {
                "voice_hash": "0x" + voice_hash,
                "intent_hash": intent.intent_hash,
                "action": intent.action,
            },
        }

    if intent.action == "check_balance":
        return {
            "type": "read_only_balance_check",
            "target": intent.params.get("address", "connected_wallet"),
            "token": intent.params.get("token", DEFAULT_CHAIN["native_token"]),
        }

    return {
        "type": "agent_intent_summary",
        "intent_hash": intent.intent_hash,
        "note": "no chain write prepared for generic summary intent",
    }


def prepare_action(state: GatewayState, intent: VoiceIntent, mode: str) -> PreparedAction:
    voice_material = {
        "committed_turns": state.committed_turns,
        "interrupts": state.interrupts,
        "events": [asdict(event) for event in state.output_events],
    }
    voice_hash = sha256_json(voice_material)
    preview = build_transaction_preview(intent, voice_hash)
    action_id = "voice-action-" + short_hash({"voice_hash": voice_hash, "intent_hash": intent.intent_hash, "preview": preview})
    mandate = build_voice_mandate(state, intent, voice_hash)

    proof_payload = {
        "voice_hash": "0x" + voice_hash,
        "intent_hash": intent.intent_hash,
        "mandate_hash": mandate["mandate_hash"],
        "action_id": action_id,
        "action": intent.action,
        "committed_turn_count": len(state.committed_turns),
        "interrupts": state.interrupts,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    policy = {
        "requires_confirmation": intent.requires_confirmation,
        "max_voice_payment": f"{DEFAULT_POLICY['max_single_payment']} {DEFAULT_POLICY['native_token']} in demo policy",
        "allowed_tokens": DEFAULT_POLICY["allowed_tokens"],
        "trusted_recipients": DEFAULT_POLICY["trusted_recipients"],
        "never_signs_private_keys": True,
        "mock_first": mode == "mock",
        "confirmation_phrases": ["confirm", "确认执行", "approve"],
    }
    policy_decision = evaluate_policy(intent, mandate, {**DEFAULT_POLICY, **policy})
    challenge = build_challenge(action_id, intent, mandate, policy_decision)
    audit_timeline = build_audit_timeline(state, intent, ConfirmationDecision("not_checked", False, ""), mandate, policy_decision)

    return PreparedAction(
        action_id=action_id,
        chain=DEFAULT_CHAIN.copy(),
        mode=mode,
        intent=intent,
        transaction_preview=preview,
        policy=policy,
        proof_payload=proof_payload,
        mandate=mandate,
        policy_decision=policy_decision,
        challenge=challenge,
        audit_timeline=audit_timeline,
    )


def submit_or_simulate(prepared: PreparedAction, confirmation: ConfirmationDecision) -> SubmissionResult:
    if prepared.policy_decision["decision"] != "approved_for_confirmation":
        return SubmissionResult(
            action_id=prepared.action_id,
            mode=prepared.mode,
            status="blocked_by_policy",
            tx_hash="",
            explorer_url="",
            receipt={
                "reason": "policy checks failed before confirmation",
                "blocking_reasons": prepared.policy_decision["blocking_reasons"],
                "policy_hash": prepared.policy_decision["policy_hash"],
            },
        )

    if not confirmation.confirmed:
        return SubmissionResult(
            action_id=prepared.action_id,
            mode=prepared.mode,
            status="blocked_by_confirmation_gate",
            tx_hash="",
            explorer_url="",
            receipt={"reason": confirmation.reason},
        )

    stable_proof = {
        key: value for key, value in prepared.proof_payload.items() if key != "created_at"
    }
    material = {
        "action_id": prepared.action_id,
        "chain_id": prepared.chain["chain_id"],
        "preview": prepared.transaction_preview,
        "proof": stable_proof,
        "mandate": prepared.mandate,
        "policy_decision": prepared.policy_decision,
        "mode": prepared.mode,
    }
    tx_hash = "0x" + sha256_json(material)
    status = "simulated" if prepared.mode == "mock" else "ready_for_wallet_signature"
    return SubmissionResult(
        action_id=prepared.action_id,
        mode=prepared.mode,
        status=status,
        tx_hash=tx_hash,
        explorer_url=f"pharos://tx/{tx_hash}",
        receipt={
            "chain": prepared.chain,
            "gas_used": 0 if prepared.mode == "mock" else None,
            "proof_hash": prepared.proof_payload["voice_hash"],
            "intent_hash": prepared.intent.intent_hash,
            "mandate_hash": prepared.mandate["mandate_hash"],
            "policy_hash": prepared.policy_decision["policy_hash"],
            "note": "mock mode creates deterministic evidence without broadcasting a transaction",
        },
    )


def tool_schema() -> list[dict[str, Any]]:
    return [
        {
            "name": "process_voice_events",
            "description": "Consume ASR/VAD/TTS JSONL events and emit turn-taking decisions.",
            "input_schema": {"type": "object", "properties": {"jsonl_path": {"type": "string"}}, "required": ["jsonl_path"]},
        },
        {
            "name": "prepare_onchain_action",
            "description": "Convert committed voice turns into a Pharos action preview and voice-session proof payload.",
            "input_schema": {"type": "object", "properties": {"voice_report": {"type": "object"}, "mode": {"enum": ["mock", "wallet"]}}},
        },
        {
            "name": "confirm_action",
            "description": "Require explicit user confirmation and readback challenge before high-risk voice actions can be submitted.",
            "input_schema": {"type": "object", "properties": {"confirmation_text": {"type": "string"}, "action_id": {"type": "string"}}},
        },
        {
            "name": "evaluate_voice_policy",
            "description": "Evaluate payment limits, token allowlists, trusted recipients and voice mandate evidence.",
            "input_schema": {"type": "object", "properties": {"intent": {"type": "object"}, "policy": {"type": "object"}}},
        },
        {
            "name": "submit_transaction",
            "description": "Submit or simulate a Pharos transaction after confirmation.",
            "input_schema": {"type": "object", "properties": {"action_id": {"type": "string"}, "mode": {"enum": ["mock", "wallet"]}}},
        },
        {
            "name": "write_session_proof",
            "description": "Create an on-chain-ready proof payload for a voice intent session.",
            "input_schema": {"type": "object", "properties": {"voice_hash": {"type": "string"}, "intent_hash": {"type": "string"}}},
        },
    ]


def run(input_path: Path, mode: str = "mock") -> GatewayRun:
    events = load_events(input_path)
    state = process_events(events)
    intent = extract_voice_intent(state)
    confirmation = decide_confirmation(state, intent)
    prepared = prepare_action(state, intent, mode)
    prepared.audit_timeline = build_audit_timeline(state, intent, confirmation, prepared.mandate, prepared.policy_decision)
    submission = submit_or_simulate(prepared, confirmation)
    return GatewayRun(
        source=str(input_path),
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        voice={
            "committed_turns": state.committed_turns,
            "interrupts": state.interrupts,
            "events": [asdict(event) for event in state.output_events],
        },
        intent=asdict(intent),
        confirmation=asdict(confirmation),
        prepared_action={
            "action_id": prepared.action_id,
            "chain": prepared.chain,
            "mode": prepared.mode,
            "transaction_preview": prepared.transaction_preview,
            "policy": prepared.policy,
            "proof_payload": prepared.proof_payload,
            "mandate": prepared.mandate,
            "policy_decision": prepared.policy_decision,
            "challenge": prepared.challenge,
            "audit_timeline": prepared.audit_timeline,
        },
        submission=asdict(submission),
        mcp_tools=tool_schema(),
    )


def write_report(result: GatewayRun, output_path: Optional[Path] = None) -> Path:
    if output_path is None:
        source = Path(result.source)
        output_path = Path.cwd() / f"pharos_voice_action_report_{source.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def print_summary(result: GatewayRun, output_path: Path) -> None:
    print("=" * 72)
    print("Pharos Voice Action Gateway")
    print("=" * 72)
    print(f"Action: {result.intent['action']}")
    print(f"Risk: {result.intent['risk']}")
    print(f"Confirmation: {result.confirmation['status']}")
    print(f"Submission: {result.submission['status']}")
    tx_hash = result.submission.get("tx_hash")
    if tx_hash:
        print(f"Tx/proof hash: {tx_hash}")
    print(f"Action ID: {result.prepared_action['action_id']}")
    print(f"Report: {output_path}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Pharos voice-to-onchain action gateway on JSONL events.")
    parser.add_argument("input", help="JSONL event file")
    parser.add_argument("--mode", choices=["mock", "wallet"], default="mock")
    parser.add_argument("--output", "-o")
    args = parser.parse_args(argv)

    try:
        result = run(Path(args.input).expanduser(), args.mode)
        output = Path(args.output).expanduser() if args.output else None
        report_path = write_report(result, output)
        print_summary(result, report_path)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
