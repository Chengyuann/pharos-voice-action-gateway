#!/usr/bin/env python3
"""Payment review and unsigned-draft core for the local product demo."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from duplex_voice_gateway import GatewayState
from pharos_voice_action_gateway import (
    ADDRESS_RE,
    VoiceIntent,
    amount_to_units,
    build_voice_mandate,
    evaluate_policy,
    load_policy,
    parse_evaluation_time,
    sha256_json,
    strict_json_loads,
)


ROOT = Path(__file__).resolve().parents[1]
DEMO_WALLET_PATH = ROOT / "config" / "payment_demo_wallet.json"
TOKEN_REGISTRY_PATH = ROOT / "config" / "token_registry.json"
POLICY_PATH = ROOT / "config" / "policy_gatekeeper.json"
ACTION_RE = re.compile(r"(?:please\s+)?(?:send|pay|transfer)\b(?P<body>[^\r\n]*)", re.IGNORECASE)
ACTION_WORD_RE = re.compile(r"\b(?:send|pay|transfer)\b", re.IGNORECASE)
UNSUPPORTED_REQUEST_RE = re.compile(
    r"\b(?:don'?t|do\s+not|not|cancel|schedule|tomorrow|later|recurring|every)\b|不要|取消|明天|稍后|每[天周月]",
    re.IGNORECASE,
)
PLAIN_AMOUNT_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")
REVIEW_TTL_SECONDS = 600


def load_demo_wallet(path: Path = DEMO_WALLET_PATH) -> dict[str, Any]:
    value = strict_json_loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("demo wallet config must be an object")
    network = value.get("network")
    wallet = value.get("wallet")
    balances = value.get("balances")
    if (not isinstance(network, dict) or type(network.get("chain_id")) is not int
            or not 0 < network["chain_id"] < 2 ** 53):
        raise ValueError("demo network requires an integer chain_id")
    if not isinstance(network.get("name"), str) or not isinstance(network.get("source"), str):
        raise ValueError("demo network requires name and source")
    if not isinstance(wallet, dict) or not ADDRESS_RE.fullmatch(str(wallet.get("address", ""))):
        raise ValueError("demo wallet requires a full EVM address")
    if int(wallet["address"], 16) == 0:
        raise ValueError("demo wallet cannot be the zero address")
    if not isinstance(wallet.get("source"), str) or not isinstance(balances, dict):
        raise ValueError("demo wallet requires source and balances")
    for symbol, balance in balances.items():
        if not isinstance(symbol, str) or not isinstance(balance, str):
            raise ValueError("demo balances must be non-negative decimal strings")
        amount_to_units(balance)
    if not isinstance(value.get("policy_time"), str):
        raise ValueError("demo policy_time must be explicit")
    parse_evaluation_time(value["policy_time"])
    return value


def load_token_registry(path: Path = TOKEN_REGISTRY_PATH) -> dict[str, Any]:
    value = strict_json_loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or type(value.get("network_chain_id")) is not int
            or not 0 < value["network_chain_id"] < 2 ** 53):
        raise ValueError("token registry requires an integer network_chain_id")
    tokens = value.get("tokens")
    if not isinstance(value.get("source"), str) or not isinstance(tokens, list) or len(tokens) != 1:
        raise ValueError("token registry requires source and tokens")
    seen: set[str] = set()
    for token in tokens:
        if not isinstance(token, dict):
            raise ValueError("token entries must be objects")
        symbol = token.get("symbol")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{0,11}", symbol):
            raise ValueError("token symbol must be uppercase")
        if symbol in seen:
            raise ValueError("token symbols must be unique")
        seen.add(symbol)
        if token.get("type") != "native" or type(token.get("decimals")) is not int or token["decimals"] != 18:
            raise ValueError("this product supports one native token with 18 decimals only")
    return value


def token_map(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {token["symbol"]: token for token in registry["tokens"]}


def parse_payment_request(
    text: str,
    answers: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
        return {"status": "invalid", "message": "Enter a payment request."}
    answers = {} if answers is None else answers
    if not isinstance(answers, dict) or set(answers) - {"amount", "token", "recipient"}:
        return {"status": "invalid", "message": "Payment answers are invalid."}
    if any(not isinstance(value, str) or not 1 <= len(value.strip()) <= 100 for value in answers.values()):
        return {"status": "invalid", "message": "Payment answers must be non-empty text."}
    answers = {key: value.strip() for key, value in answers.items()}

    if UNSUPPORTED_REQUEST_RE.search(text):
        return {
            "status": "invalid",
            "message": "Scheduled, recurring, cancelled, or negated payments are not supported.",
        }
    actions = list(ACTION_WORD_RE.finditer(text))
    match = ACTION_RE.fullmatch(text.strip())
    if not match or len(actions) != 1:
        return {
            "status": "invalid",
            "message": "Use one payment instruction starting with send, pay, or transfer.",
        }
    body = match.group("body").strip()
    if len(re.findall(r"\b0x", body, re.IGNORECASE)) > 1:
        return {
            "status": "needs_details",
            "field": "recipient",
            "question": "Edit the request to include one recipient address.",
            "reason": "More than one address was found.", "requires_rewrite": True,
        }
    body = re.sub(r"\s+please$", "", body, flags=re.IGNORECASE)
    segments = re.split(r"\s*\bto\b\s*", body, flags=re.IGNORECASE)
    if len(segments) > 2:
        return {"status": "invalid", "message": "Use one recipient after 'to'."}
    parts = segments[0].split()
    text_recipient = segments[1].strip() if len(segments) == 2 else ""
    if len(text_recipient.split()) > 1 or len(parts) > 2:
        return {"status": "invalid", "message": "Use one amount, one token, and one recipient address."}
    amount_indexes = [index for index, part in enumerate(parts) if PLAIN_AMOUNT_RE.fullmatch(part)]
    if len(amount_indexes) > 1:
        return {
            "status": "needs_details",
            "field": "amount",
            "question": "Edit the request to include one amount.",
            "reason": "More than one amount was found.", "requires_rewrite": True,
        }
    text_amount, text_symbol = "", ""
    if len(parts) == 2:
        text_amount, text_symbol = parts[0], parts[1].upper()
    elif parts:
        if re.match(r"[0-9.+-]", parts[0]):
            text_amount = parts[0]
        else:
            text_symbol = parts[0].upper()

    original = {"amount": text_amount, "token": text_symbol, "recipient": text_recipient}
    for field, value in answers.items():
        normalized = value.upper() if field == "token" else value
        if original[field] and original[field].lower() != normalized.lower():
            return {
                "status": "invalid",
                "message": f"The supplied {field} conflicts with the payment request.",
            }
    amount = text_amount or answers.get("amount", "")
    symbol = text_symbol or answers.get("token", "").upper()
    recipient = text_recipient or answers.get("recipient", "")

    parsed = {"amount": amount, "token": symbol, "recipient": recipient}
    for field, question in [
        ("amount", "How much would you like to send?"),
        ("token", "Which token should be used?"),
        ("recipient", "Enter the recipient's full address."),
    ]:
        if not parsed[field]:
            return {
                "status": "needs_details",
                "field": field,
                "question": question,
                "payment": parsed,
            }
    return {"status": "complete", "payment": parsed}


def units_to_amount(units: int, decimals: int) -> str:
    digits = str(units).rjust(decimals + 1, "0")
    if decimals == 0:
        return digits
    whole, fraction = digits[:-decimals], digits[-decimals:].rstrip("0")
    return whole if not fraction else f"{whole}.{fraction}"


class RpcReader:
    def __init__(self, url: str, timeout: float = 8.0):
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
            raise ValueError("RPC URL must be HTTP(S) without embedded credentials")
        self.url = url
        self.timeout = timeout
        self.request_id = 0

    def call(self, method: str, params: list[Any]) -> Any:
        if method not in {"eth_chainId", "eth_getBalance", "eth_estimateGas", "eth_gasPrice", "eth_blockNumber"}:
            raise ValueError("only read-only RPC methods are permitted")
        self.request_id += 1
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method,
            "params": params,
        }).encode("utf-8")
        request = Request(self.url, data=body, headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=self.timeout) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("RPC response is too large")
            payload = strict_json_loads(raw.decode("utf-8"))
        if (not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0"
                or type(payload.get("id")) is not int or payload["id"] != self.request_id):
            raise ValueError("RPC response did not match the request")
        if "error" in payload:
            raise ValueError(f"RPC {method} failed")
        if "result" not in payload:
            raise ValueError(f"RPC {method} returned no result")
        return payload["result"]

    def quantity(self, method: str, params: list[Any]) -> int:
        result = self.call(method, params)
        if not isinstance(result, str) or not re.fullmatch(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]{0,63})", result):
            raise ValueError(f"RPC {method} returned an invalid quantity")
        return int(result, 16)

    def snapshot(self, wallet: str, recipient: str, amount_units: int) -> dict[str, Any]:
        if (not ADDRESS_RE.fullmatch(wallet) or not ADDRESS_RE.fullmatch(recipient)
                or type(amount_units) is not int or not 0 < amount_units < 2 ** 256):
            raise ValueError("RPC payment inputs are invalid")
        chain_id = self.quantity("eth_chainId", [])
        block = self.quantity("eth_blockNumber", [])
        balance_units = self.quantity("eth_getBalance", [wallet, hex(block)])
        transaction = {
            "from": wallet,
            "to": recipient,
            "value": hex(amount_units),
            "data": "0x",
        }
        gas_units, gas_price_units, fee_units = None, None, None
        fee_error = ""
        try:
            gas_units = self.quantity("eth_estimateGas", [transaction])
            gas_price_units = self.quantity("eth_gasPrice", [])
            if gas_units < 21000 or gas_units * gas_price_units >= 2 ** 256:
                raise ValueError("invalid gas estimate")
            fee_units = gas_units * gas_price_units
        except (OSError, ValueError):
            fee_error = "The network could not estimate the fee. No transaction draft will be created."
        return {
            "chain_id": chain_id,
            "balance_units": balance_units,
            "gas_units": gas_units,
            "gas_price_units": gas_price_units,
            "fee_units": fee_units, "fee_error": fee_error, "block_number": block,
            "source": "live_rpc",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }


def validate_snapshot(snapshot: dict, now: datetime) -> None:
    if not isinstance(snapshot, dict) or not {"chain_id", "balance_units", "fee_units", "fetched_at"} <= snapshot.keys():
        raise ValueError("live network data is incomplete")
    if not isinstance(snapshot["fetched_at"], str):
        raise ValueError("live data requires a timestamp")
    if snapshot.get("source") != "live_rpc":
        raise ValueError("snapshot must come from live RPC")
    for field in ("chain_id", "balance_units"):
        if type(snapshot.get(field)) is not int or not 0 <= snapshot[field] < 2 ** 256:
            raise ValueError("invalid live network quantity")
    age = (now - parse_evaluation_time(snapshot["fetched_at"])).total_seconds()
    if not -5 <= age <= 60:
        raise ValueError("live data expired; run the checks again")
    if snapshot.get("fee_units") is not None:
        for field in ("fee_units", "gas_units", "gas_price_units"):
            if type(snapshot.get(field)) is not int or not 0 <= snapshot[field] < 2 ** 256:
                raise ValueError("invalid live fee quantity")
        if snapshot["gas_units"] < 21000 or snapshot["fee_units"] != snapshot["gas_units"] * snapshot["gas_price_units"]:
            raise ValueError("live fee estimate is inconsistent")


def review_fingerprint(review: dict[str, Any]) -> str:
    material = deepcopy(review)
    material.pop("integrity_hash", None)
    material.pop("recorded", None)
    return "0x" + sha256_json(material)


def review_payment(
    text: str,
    answers: Optional[dict[str, Any]],
    mode: str,
    wallet_config: dict[str, Any],
    registry: dict[str, Any],
    policy: dict[str, Any],
    live_snapshot: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if mode not in {"demo", "live"}:
        raise ValueError("mode must be demo or live")
    if wallet_config["network"]["chain_id"] != registry["network_chain_id"]:
        raise ValueError("network and token registry configuration disagree")
    parsed = parse_payment_request(text, answers)
    review = {
        "id": uuid4().hex,
        "record_type": "payment_review",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=REVIEW_TTL_SECONDS)).isoformat(),
        "mode": mode,
        "request_text": text,
        "answers": deepcopy(answers or {}),
        "status": parsed["status"],
        "field": parsed.get("field", ""),
        "question": parsed.get("question", ""),
        "reason": parsed.get("reason", ""),
        "requires_rewrite": parsed.get("requires_rewrite", False),
        "message": parsed.get("message", ""),
        "payment": parsed.get("payment"),
        "checks": [],
        "network": deepcopy(wallet_config["network"]),
        "wallet": deepcopy(wallet_config["wallet"]),
        "balance": None,
        "fee": {"status": "not_checked", "source": "unavailable"},
        "policy_decision": None,
        "draft_allowed": False,
        "draft_label": "",
        "configuration_hash": "0x" + sha256_json({"wallet": wallet_config, "registry": registry, "policy": policy}),
    }
    if mode == "live":
        review["network"]["source"] = "configured_network"
        if review["wallet"]["address"]:
            review["wallet"]["source"] = "configured_readonly_address"
    if parsed["status"] != "complete":
        review["integrity_hash"] = review_fingerprint(review)
        return review

    payment = parsed["payment"]
    tokens = token_map(registry)
    token = tokens.get(payment["token"])
    recipient_valid = bool(ADDRESS_RE.fullmatch(payment["recipient"])) and int(
        payment["recipient"], 16,
    ) != 0
    review["checks"].append({
        "name": "recipient_format",
        "label": "Recipient address",
        "passed": recipient_valid,
        "detail": "Full EVM address" if recipient_valid else "Enter a valid nonzero EVM address.",
        "source": "request",
    })
    review["checks"].append({
        "name": "token_registry",
        "label": "Token",
        "passed": token is not None and token["type"] == "native" and token["decimals"] == 18,
        "detail": payment["token"] if token else f"{payment['token']} is not supported. Choose PHRS.",
        "source": registry["source"] if mode == "demo" else "configured_native_token",
    })
    if token is None or token["type"] != "native" or token["decimals"] != 18 or not recipient_valid:
        review["status"] = "blocked"
        review["integrity_hash"] = review_fingerprint(review)
        return review

    try:
        amount_units = int(amount_to_units(payment["amount"], token["decimals"]))
    except ValueError as exc:
        review["checks"].append({
            "name": "amount",
            "label": "Amount",
            "passed": False,
            "detail": str(exc),
            "source": "request",
        })
        review["status"] = "blocked"
        review["integrity_hash"] = review_fingerprint(review)
        return review
    if amount_units <= 0:
        review["checks"].append({
            "name": "amount",
            "label": "Amount",
            "passed": False,
            "detail": "Amount must be greater than zero.",
            "source": "request",
        })
        review["status"] = "blocked"
        review["integrity_hash"] = review_fingerprint(review)
        return review
    review["checks"].append({
        "name": "amount",
        "label": "Amount",
        "passed": True,
        "detail": f"{payment['amount']} {payment['token']}",
        "source": "request",
    })

    fee_valid = True
    if mode == "live":
        if live_snapshot is None:
            review["status"] = "data_unavailable"
            review["message"] = "Live network data is unavailable."
            review["integrity_hash"] = review_fingerprint(review)
            return review
        try:
            validate_snapshot(live_snapshot, now)
        except (ValueError, TypeError, KeyError) as exc:
            review["status"] = "data_unavailable"
            review["message"] = str(exc)
            review["integrity_hash"] = review_fingerprint(review)
            return review
        if live_snapshot["chain_id"] != registry["network_chain_id"]:
            review["status"] = "data_unavailable"
            review["message"] = "The RPC is connected to a different network."
            review["integrity_hash"] = review_fingerprint(review)
            return review
        balance_units = live_snapshot["balance_units"]
        fee_units = live_snapshot["fee_units"]
        fee_valid = fee_units is not None
        review["network"] = {
            "name": wallet_config["network"]["name"],
            "chain_id": live_snapshot["chain_id"],
            "source": "live_rpc",
            "fetched_at": live_snapshot["fetched_at"],
            "block_number": live_snapshot.get("block_number"),
        }
        review["balance"] = {
            "available": units_to_amount(balance_units, token["decimals"]),
            "required": units_to_amount(amount_units + (fee_units or 0), token["decimals"]),
            "source": "live_rpc",
        }
        review["fee"] = {
            "status": "estimated",
            "amount": units_to_amount(fee_units or 0, token["decimals"]),
            "gas": live_snapshot["gas_units"],
            "gas_price": live_snapshot["gas_price_units"],
            "source": "live_rpc",
        }
        if not fee_valid:
            review["fee"] = {"status": "unavailable", "source": "live_rpc",
                             "message": live_snapshot.get("fee_error", "Fee estimate is unavailable.")}
        sufficient = balance_units >= amount_units + (fee_units or 0)
    else:
        available = wallet_config["balances"].get(payment["token"])
        if available is None:
            review["status"] = "data_unavailable"
            review["message"] = "No demo balance is configured for this token."
            review["integrity_hash"] = review_fingerprint(review)
            return review
        balance_units = int(amount_to_units(available, token["decimals"]))
        review["balance"] = {
            "available": available,
            "required": payment["amount"],
            "source": wallet_config["wallet"]["source"],
        }
        sufficient = balance_units >= amount_units

    review["checks"].append({
        "name": "balance",
        "label": "Available balance",
        "passed": sufficient,
        "detail": (
            f"{review['balance']['available']} {payment['token']} available"
            if sufficient else
            f"{review['balance']['required']} {payment['token']} required; "
            f"{review['balance']['available']} available"
        ),
        "source": review["balance"]["source"],
    })
    if mode == "live":
        review["checks"].append({
            "name": "network_fee", "label": "Network fee", "passed": fee_valid,
            "detail": f"Estimated {review['fee']['amount']} PHRS" if fee_valid else review["fee"]["message"],
            "source": "live_rpc",
        })

    intent = VoiceIntent(
        action="send_payment",
        confidence=1.0,
        summary="Payment preview request.",
        raw_text="",
        params={
            "amount": payment["amount"],
            "token": payment["token"],
            "to": payment["recipient"],
        },
        risk="high",
        requires_confirmation=True,
        intent_hash="0x" + sha256_json(payment),
    )
    evidence_hash = sha256_json({"request": text, "payment": payment, "mode": mode})
    mandate = build_voice_mandate(GatewayState(), intent, evidence_hash)
    mandate["scope"]["chain_id"] = wallet_config["network"]["chain_id"]
    mandate["mandate_hash"] = "0x" + sha256_json({key: value for key, value in mandate.items() if key != "mandate_hash"})
    policy_time = (
        parse_evaluation_time(wallet_config["policy_time"])
        if mode == "demo" else now
    )
    decision = evaluate_policy(intent, mandate, policy, policy_time)
    review["policy_decision"] = decision
    review["checks"].append({
        "name": "payment_policy",
        "label": "Payment policy",
        "passed": decision["outcome"] == "allow",
        "detail": (
            "Allowed by the configured payment policy."
            if decision["outcome"] == "allow" else
            {
                "recipient_trust": "This address is not on the allowed recipients list.",
                "amount_limit": f"This payment exceeds the {policy['max_single_payment']} PHRS limit.",
                "time_window": "The current time is outside the allowed payment hours. See Payment rules.",
                "token_allowlist": "This token is not allowed by the payment policy.",
            }.get(decision["deciding_rule"], "The payment policy could not approve this request.")
        ),
        "source": "policy_gatekeeper",
    })
    review["status"] = "ready" if sufficient and decision["outcome"] == "allow" else "blocked"
    if review["status"] == "ready" and not fee_valid:
        review["status"] = "data_unavailable"
        review["message"] = "Balance loaded, but the network fee could not be estimated. Try again."
    review["draft_allowed"] = review["status"] == "ready"
    review["draft_label"] = (
        "Create example draft" if mode == "demo" else "Create unsigned transaction"
    )
    review["amount_units"] = str(amount_units)
    review["token"] = token
    review["integrity_hash"] = review_fingerprint(review)
    return review


def create_transaction_draft(review: dict[str, Any], now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if review.get("record_type") != "payment_review":
        raise ValueError("review record is invalid")
    if review.get("integrity_hash") != review_fingerprint(review):
        raise ValueError("review record changed")
    if review.get("status") != "ready" or not review.get("draft_allowed"):
        raise ValueError("payment review is not ready")
    if now >= parse_evaluation_time(review["expires_at"]):
        raise ValueError("payment review expired")

    payment = review["payment"]
    transaction = {
        "chainId": review["network"]["chain_id"],
        "from": review["wallet"]["address"],
        "to": payment["recipient"],
        "value": hex(int(amount_to_units(payment["amount"], review["token"]["decimals"]))),
        "data": "0x",
    }
    if review["mode"] == "live":
        if review["network"]["source"] != "live_rpc" or review["fee"]["status"] != "estimated":
            raise ValueError("live checks are incomplete")
        age = (now - parse_evaluation_time(review["network"]["fetched_at"])).total_seconds()
        if not -5 <= age <= 60:
            raise ValueError("live data expired; review the payment again")
        transaction["gas"] = hex(int(review["fee"]["gas"]))
        transaction["gasPrice"] = hex(int(review["fee"]["gas_price"]))

    draft = {
        "id": uuid4().hex,
        "record_type": "payment_draft",
        "review_id": review["id"],
        "created_at": now.isoformat(),
        "mode": review["mode"],
        "status": "draft_created",
        "kind": "example_transaction_draft" if review["mode"] == "demo" else "unsigned_transaction",
        "wallet_ready": review["mode"] == "live",
        "payment": deepcopy(payment),
        "network": deepcopy(review["network"]),
        "expires_at": review["expires_at"],
        "checks": deepcopy(review["checks"]),
        "fee": deepcopy(review["fee"]),
        "balance": deepcopy(review["balance"]),
        "transaction": transaction,
        "signed": False,
        "broadcast": False,
        "message": (
            "Example only. Run live checks before using a wallet."
            if review["mode"] == "demo" else
            "Unsigned payment request. A wallet must recheck fees, balance and nonce. Nothing has been sent."
        ),
    }
    draft["integrity_hash"] = "0x" + sha256_json(draft)
    return draft


def rpc_from_environment() -> Optional[RpcReader]:
    url = os.environ.get("PHAROS_RPC_URL", "").strip()
    return RpcReader(url) if url else None


def wallet_for_mode(wallet: dict[str, Any], mode: str) -> dict[str, Any]:
    wallet = deepcopy(wallet)
    if mode == "live":
        address = os.environ.get("PHAROS_READ_ADDRESS", "").strip()
        if not ADDRESS_RE.fullmatch(address) or int(address, 16) == 0:
            raise ValueError("Set PHAROS_READ_ADDRESS to the full address you want to inspect. No wallet connection is required.")
        wallet["wallet"] = {"address": address, "source": "configured_readonly_address"}
    return wallet
