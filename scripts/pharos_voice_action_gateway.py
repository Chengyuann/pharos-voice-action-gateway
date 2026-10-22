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
import math
import re
import sys
from copy import deepcopy
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
ZERO_VERIFYING_CONTRACT = "0x0000000000000000000000000000000000000000"

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
    "allowed_time_windows": [
        {
            "days": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
            "start": "00:00",
            "end": "24:00",
            "timezone": "UTC",
        },
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
    eip712: dict[str, Any]
    registry_call: dict[str, Any]
    integrity_hash: str = ""


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
        text = str(value)
        if len(text) > 100 or not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,18})?", text):
            return Decimal("0")
        parsed = Decimal(text)
        return parsed if parsed.is_finite() else Decimal("0")
    except (InvalidOperation, ValueError):
        return Decimal("0")


def bytes32(value: str) -> str:
    if value.startswith("0x") and len(value) == 66:
        return value
    return "0x" + sha256_json(value)


def amount_to_units(amount: Any, decimals: int = 18) -> str:
    if type(decimals) is not int or not 0 <= decimals <= 77:
        raise ValueError("token decimals must be an integer between 0 and 77")
    text = str(amount)
    if len(text) > 100 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
        raise ValueError("amount must be a finite unsigned plain decimal")
    whole, _, fraction = text.partition(".")
    if len(fraction) > decimals:
        raise ValueError("amount has more precision than token decimals")
    # Integer string arithmetic avoids Decimal's default 28-digit rounding.
    units = int(whole + fraction.ljust(decimals, "0"))
    if units > 2 ** 256 - 1:
        raise ValueError("amount exceeds uint256")
    return str(units)


def load_policy(path: Path) -> dict[str, Any]:
    policy = strict_json_loads(path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise ValueError("policy file must contain a JSON object")
    validate_policy(policy)
    return policy


def strict_json_loads(raw: Any) -> Any:
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON field")
            value[key] = item
        return value

    def reject_constant(value):
        raise ValueError("non-finite JSON number")

    def finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite JSON number")
        return parsed

    try:
        return json.loads(raw, object_pairs_hook=unique_object,
                          parse_constant=reject_constant, parse_float=finite_float)
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds the supported depth") from exc


def parse_evaluation_time(value: Optional[str]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("evaluation time must include Z or a UTC offset")
    try:
        return parsed.astimezone(timezone.utc)
    except OverflowError as exc:
        raise ValueError("evaluation time is outside the supported UTC range") from exc


def time_to_minutes(value: str, allow_24: bool = False) -> int:
    match = re.fullmatch(r"([0-9]{2}):([0-9]{2})", value)
    if not match:
        raise ValueError(f"invalid policy time: {value}")
    hour, minute = (int(part) for part in match.groups())
    if allow_24 and hour == 24 and minute == 0:
        return 24 * 60
    if hour > 23 or minute > 59:
        raise ValueError(f"invalid policy time: {value}")
    return hour * 60 + minute


def validate_windows(windows: Any) -> None:
    if not isinstance(windows, list):
        raise ValueError("allowed_time_windows must be a list (empty denies all times)")
    for window in windows:
        if not isinstance(window, dict) or set(window) != {"days", "start", "end", "timezone"}:
            raise ValueError("a time window requires days, start, end and timezone")
        if window.get("timezone", "UTC") != "UTC":
            raise ValueError("policy time windows currently require UTC")
        days = window["days"]
        if not isinstance(days, list) or not days or any(
            day not in ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"] for day in days
        ):
            raise ValueError("days must contain uppercase UTC weekday names")
        start = time_to_minutes(str(window["start"]))
        end = time_to_minutes(str(window["end"]), allow_24=True)
        if start >= end:
            raise ValueError("window start must precede end; split overnight windows at midnight")


def validate_policy(policy: Any) -> None:
    if not isinstance(policy, dict):
        raise ValueError("policy must be an object")
    required = {"max_single_payment", "native_token", "allowed_tokens",
                "trusted_recipients", "allowed_time_windows"}
    if not required.issubset(policy):
        raise ValueError("missing policy fields: " + ", ".join(sorted(required - set(policy))))
    if decimal_or_zero(policy["max_single_payment"]) <= 0:
        raise ValueError("max_single_payment must be a positive plain decimal, at most 18 decimal places")
    amount_to_units(policy["max_single_payment"])
    tokens = policy["allowed_tokens"]
    if not isinstance(tokens, list) or any(
        not isinstance(token, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{0,11}", token)
        for token in tokens
    ):
        raise ValueError("allowed_tokens must be a list of uppercase token symbols")
    if not isinstance(policy["native_token"], str):
        raise ValueError("native_token must be a string")
    recipients = policy["trusted_recipients"]
    if not isinstance(recipients, list) or any(
        not isinstance(address, str) or not ADDRESS_RE.fullmatch(address)
        or address.lower() == ZERO_VERIFYING_CONTRACT for address in recipients
    ):
        raise ValueError("trusted_recipients must be a list of nonzero EVM addresses")
    validate_windows(policy["allowed_time_windows"])


def evaluate_time_window(evaluated_at: datetime, windows: list[dict[str, Any]]) -> tuple[bool, str]:
    validate_windows(windows)
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluation time must be timezone-aware")
    current = evaluated_at.astimezone(timezone.utc)
    day = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"][current.weekday()]
    minute = current.hour * 60 + current.minute
    for window in windows:
        days = window["days"]
        start = time_to_minutes(window["start"])
        end = time_to_minutes(window["end"], allow_24=True)
        if day in days and start <= minute < end:
            return True, "current UTC time is inside an allowed window"
    return False, "current UTC time is outside every allowed window"


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


def evaluate_policy(
    intent: VoiceIntent,
    mandate: dict[str, Any],
    policy: dict[str, Any],
    evaluated_at: Optional[datetime] = None,
) -> dict[str, Any]:
    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    checks: list[dict[str, Any]] = []
    try:
        validate_policy(policy)
        evaluate_time_window(evaluated_at, policy["allowed_time_windows"])
    except (TypeError, ValueError) as exc:
        checks.append({"name": "policy_config", "passed": False, "details": str(exc)})
        return policy_result(checks, policy, evaluated_at)

    amount = decimal_or_zero(intent.params.get("amount", "0"))
    amount_valid = amount > 0
    try:
        amount_to_units(intent.params.get("amount", "0"))
    except ValueError:
        amount_valid = False
    max_single = decimal_or_zero(policy["max_single_payment"])
    token = str(intent.params.get("token", DEFAULT_CHAIN["native_token"])).upper()
    recipient = str(intent.params.get("to", ""))

    if intent.action == "send_payment":
        checks.append({
            "name": "amount_limit",
            "passed": amount_valid and amount <= max_single,
            "details": f"{intent.params.get('amount', 'missing')} {token}; maximum {max_single} token units; must be positive and representable",
        })
        checks.append({
            "name": "token_allowlist",
            "passed": token in policy["allowed_tokens"],
            "details": token,
        })
        checks.append({
            "name": "recipient_trust",
            "passed": bool(ADDRESS_RE.fullmatch(recipient))
            and recipient.lower() in [address.lower() for address in policy["trusted_recipients"]],
            "details": recipient or "missing",
        })
        time_allowed, time_details = evaluate_time_window(
            evaluated_at,
            policy["allowed_time_windows"],
        )
        checks.append({
            "name": "time_window",
            "passed": time_allowed,
            "details": time_details,
        })
    elif intent.action not in {"check_balance", "summarize_intent", "write_session_proof"}:
        checks.append({"name": "action_scope", "passed": False, "details": "unsupported action"})

    checks.append({
        "name": "voice_mandate_hash",
        "passed": bool(re.fullmatch(r"0x[0-9a-fA-F]{64}", str(mandate.get("mandate_hash", "")))),
        "details": mandate.get("mandate_hash", ""),
    })
    checks.append({
        "name": "no_raw_audio_storage",
        "passed": mandate.get("raw_audio_stored") is False,
        "details": "stores hashes and text-event evidence only",
    })

    return policy_result(checks, policy, evaluated_at)


def policy_result(checks: list[dict[str, Any]], policy: dict[str, Any], evaluated_at: datetime) -> dict[str, Any]:
    blocking = [check for check in checks if not check["passed"]]
    decision = "approved_for_confirmation" if not blocking else "blocked_by_policy"
    outcome = "allow" if not blocking else "deny"
    deciding_rule = "all_rules_passed" if not blocking else blocking[0]["name"]
    audit_log = [
        {
            "rule": check["name"],
            "outcome": "allow" if check["passed"] else "deny",
            "details": check["details"],
        }
        for check in checks
    ]
    audit_log.append({
        "rule": deciding_rule,
        "outcome": outcome,
        "details": f"final decision at {evaluated_at.isoformat()}",
    })
    return {
        "decision": decision,
        "outcome": outcome,
        "deciding_rule": deciding_rule,
        "evaluated_at": evaluated_at.isoformat(),
        "policy_passed": not blocking,
        "checks": checks,
        "blocking_reasons": [check["name"] for check in blocking],
        "audit_log": audit_log,
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


def build_eip712_typed_data(
    action_id: str,
    intent: VoiceIntent,
    mandate: dict[str, Any],
    policy_decision: dict[str, Any],
    challenge: dict[str, Any],
) -> dict[str, Any]:
    """Create wallet-readable EIP-712 typed data for the voice mandate."""
    scope = mandate["scope"]
    try:
        units = amount_to_units(scope["max_amount"])
    except ValueError:
        if policy_decision["outcome"] != "deny":
            raise
        units = "0"  # Rejected requests get a non-signable diagnostic payload.
    message = {
        "subject": mandate["subject"],
        "actionId": action_id,
        "action": scope["action"],
        "chainId": scope["chain_id"],
        "token": scope["token"],
        "amount": units,
        "recipient": scope["recipient"] if ADDRESS_RE.fullmatch(str(scope["recipient"])) else ZERO_VERIFYING_CONTRACT,
        "voiceHash": bytes32(mandate["voice_hash"]),
        "intentHash": bytes32(intent.intent_hash),
        "mandateHash": bytes32(mandate["mandate_hash"]),
        "policyHash": bytes32(policy_decision["policy_hash"]),
        "challengeHash": bytes32(challenge["phrase"]),
        "expiresAfterSeconds": mandate["expires_after_seconds"],
    }
    typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "VoiceMandate": [
                {"name": "subject", "type": "string"},
                {"name": "actionId", "type": "string"},
                {"name": "action", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "token", "type": "string"},
                {"name": "amount", "type": "uint256"},
                {"name": "recipient", "type": "address"},
                {"name": "voiceHash", "type": "bytes32"},
                {"name": "intentHash", "type": "bytes32"},
                {"name": "mandateHash", "type": "bytes32"},
                {"name": "policyHash", "type": "bytes32"},
                {"name": "challengeHash", "type": "bytes32"},
                {"name": "expiresAfterSeconds", "type": "uint256"},
            ],
        },
        "primaryType": "VoiceMandate",
        "domain": {
            "name": "Pharos Voice Action Gateway",
            "version": "0.2.0",
            "chainId": DEFAULT_CHAIN["chain_id"],
            "verifyingContract": ZERO_VERIFYING_CONTRACT,
        },
        "message": message,
    }
    return {
        "standard": "EIP-712",
        "typed_data": typed_data,
        "typed_data_hash": "0x" + sha256_json(typed_data),
        "signature_status": "unsigned_demo_payload",
        "signing_note": "Send typed_data to a wallet or delegated account signer before live submission.",
    }


def build_registry_call(action_id: str, intent: VoiceIntent, mandate: dict[str, Any], eip712: dict[str, Any]) -> dict[str, Any]:
    message = eip712["typed_data"]["message"]
    return {
        "contract": "VoiceSessionProofRegistry",
        "function": "recordVoiceMandate",
        "args": {
            "actionId": action_id,
            "action": intent.action,
            "voiceHash": message["voiceHash"],
            "intentHash": message["intentHash"],
            "mandateHash": message["mandateHash"],
            "policyHash": message["policyHash"],
            "challengeHash": message["challengeHash"],
            "subject": mandate["subject"],
            "signature": "0x",
        },
        "calldata_preview": "recordVoiceMandate(string,string,bytes32,bytes32,bytes32,bytes32,bytes32,string,bytes)",
        "typed_data_hash": eip712["typed_data_hash"],
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


def prepare_action(
    state: GatewayState,
    intent: VoiceIntent,
    mode: str,
    policy_config: Optional[dict[str, Any]] = None,
    evaluated_at: Optional[datetime] = None,
) -> PreparedAction:
    if mode not in {"mock", "wallet"}:
        raise ValueError("mode must be mock or wallet")
    if evaluated_at is not None and mode != "mock":
        raise ValueError("replay time is supported only in mock mode")
    intent = deepcopy(intent)
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

    policy_source = deepcopy(DEFAULT_POLICY if policy_config is None else policy_config)
    validate_policy(policy_source)
    policy = {
        **policy_source,
        "requires_confirmation": intent.requires_confirmation,
        "max_voice_payment": f"{policy_source['max_single_payment']} {policy_source['native_token']} in policy",
        "allowed_tokens": policy_source["allowed_tokens"],
        "trusted_recipients": policy_source["trusted_recipients"],
        "allowed_time_windows": policy_source["allowed_time_windows"],
        "never_signs_private_keys": True,
        "mock_first": mode == "mock",
        "confirmation_phrases": ["confirm", "确认执行", "approve"],
    }
    policy_decision = evaluate_policy(
        intent,
        mandate,
        {**policy_source, **policy},
        evaluated_at or datetime.now(timezone.utc),
    )
    challenge = build_challenge(action_id, intent, mandate, policy_decision)
    audit_timeline = build_audit_timeline(state, intent, ConfirmationDecision("not_checked", False, ""), mandate, policy_decision)
    eip712 = build_eip712_typed_data(action_id, intent, mandate, policy_decision, challenge)
    registry_call = build_registry_call(action_id, intent, mandate, eip712)

    prepared = PreparedAction(
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
        eip712=eip712,
        registry_call=registry_call,
    )
    prepared.integrity_hash = prepared_fingerprint(prepared)
    return prepared


def prepared_fingerprint(prepared: PreparedAction) -> str:
    """An accidental-mutation guard, NOT authentication of in-process code."""
    material = asdict(prepared)
    material.pop("integrity_hash", None)
    material.pop("audit_timeline", None)
    return sha256_json(material)


def submit_or_simulate(
    prepared: PreparedAction,
    confirmation: ConfirmationDecision,
    evaluated_at: Optional[datetime] = None,
) -> SubmissionResult:
    try:
        intact = bool(prepared.integrity_hash) and prepared_fingerprint(prepared) == prepared.integrity_hash
    except (TypeError, ValueError, RecursionError):
        intact = False
    if not intact:
        prepared.audit_timeline.append({"step": "integrity_check", "result": "deny"})
        return SubmissionResult(
            prepared.action_id, prepared.mode, "blocked_by_integrity", "", "",
            {"reason": "prepared action changed; prepare and confirm again",
             "deciding_rule": "prepared_integrity", "execution_attempted": False,
             "mock_executor_called": False},
        )
    if evaluated_at is not None and prepared.mode != "mock":
        raise ValueError("replay time is supported only in mock mode")
    expected_preview = build_transaction_preview(
        prepared.intent, prepared.proof_payload["voice_hash"].removeprefix("0x"),
    )
    if expected_preview != prepared.transaction_preview:
        return SubmissionResult(
            prepared.action_id, prepared.mode, "blocked_by_integrity", "", "",
            {"reason": "transaction preview does not match intent",
             "deciding_rule": "prepared_integrity", "execution_attempted": False,
             "mock_executor_called": False},
        )
    # Never reuse an earlier allow after the policy's time window closes.
    latest = evaluate_policy(prepared.intent, prepared.mandate, prepared.policy, evaluated_at)
    if prepared.policy_decision["outcome"] == "allow":
        prepared.policy_decision = latest
    prepared.integrity_hash = prepared_fingerprint(prepared)
    prepared.audit_timeline.append({
        "step": "policy_rechecked", "result": latest["outcome"],
        "evaluated_at": latest["evaluated_at"], "deciding_rule": latest["deciding_rule"],
    })
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
                "deciding_rule": prepared.policy_decision["deciding_rule"],
                "execution_attempted": False,
                "mock_executor_called": False,
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
            receipt={
                "reason": confirmation.reason,
                "execution_attempted": False,
                "mock_executor_called": False,
            },
        )

    stable_proof = {
        key: value for key, value in prepared.proof_payload.items() if key != "created_at"
    }
    material = {
        "action_id": prepared.action_id,
        "chain_id": prepared.chain["chain_id"],
        "preview": expected_preview,
        "proof": stable_proof,
        "mandate": prepared.mandate,
        "policy_decision": {
            key: value for key, value in prepared.policy_decision.items()
            if key not in {"evaluated_at", "audit_log"}
        },
        "typed_data_hash": prepared.eip712["typed_data_hash"],
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
            "typed_data_hash": prepared.eip712["typed_data_hash"],
            "note": "mock mode creates deterministic evidence without broadcasting a transaction",
            "execution_attempted": False,
            "mock_executor_called": prepared.mode == "mock",
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
        {
            "name": "export_eip712_voice_mandate",
            "description": "Export wallet-readable EIP-712 typed data for the committed voice mandate.",
            "input_schema": {"type": "object", "properties": {"action_id": {"type": "string"}, "mandate_hash": {"type": "string"}}},
        },
    ]


def run(
    input_path: Path,
    mode: str = "mock",
    policy_config: Optional[dict[str, Any]] = None,
    evaluated_at: Optional[datetime] = None,
) -> GatewayRun:
    if mode not in {"mock", "wallet"}:
        raise ValueError("mode must be mock or wallet")
    if evaluated_at is not None and mode != "mock":
        raise ValueError("replay time is supported only in mock mode")
    events = load_events(input_path)
    state = process_events(events)
    intent = extract_voice_intent(state)
    confirmation = decide_confirmation(state, intent)
    prepared = prepare_action(state, intent, mode, policy_config, evaluated_at)
    prepared.audit_timeline = build_audit_timeline(state, intent, confirmation, prepared.mandate, prepared.policy_decision)
    submission = submit_or_simulate(prepared, confirmation, evaluated_at)
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
            "eip712": prepared.eip712,
            "registry_call": prepared.registry_call,
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
    parser.add_argument("--policy", help="JSON policy file")
    parser.add_argument("--at", help="Mock replay time, ISO-8601 with offset; omitted uses server UTC")
    parser.add_argument("--output", "-o")
    args = parser.parse_args(argv)

    try:
        policy = load_policy(Path(args.policy).expanduser()) if args.policy else None
        evaluated_at = parse_evaluation_time(args.at) if args.at else None
        result = run(Path(args.input).expanduser(), args.mode, policy, evaluated_at)
        output = Path(args.output).expanduser() if args.output else None
        report_path = write_report(result, output)
        print_summary(result, report_path)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
