"""Official Chain MCP payment review. No keys, signatures or broadcasting."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import re
from typing import Any
from uuid import uuid4

from payment_preview import review_fingerprint, units_to_amount
from payment_language import load_contacts, parse_mainnet_request
from pharos_mcp import DEFAULT_MCP_URL, McpToolError, PharosMcpClient, quantity
from pharos_voice_action_gateway import (
    ADDRESS_RE, amount_to_units, evaluate_time_window, load_policy,
    policy_result, sha256_json, strict_json_loads,
)


ROOT = Path(__file__).resolve().parents[1]
MAINNET_REGISTRY = ROOT / "config" / "pharos_mainnet_tokens.json"
MAINNET_POLICY = ROOT / "config" / "payment_mainnet_policy.json"
SKILL_COMMIT = "a873387c9e34e9ae8e71360d43dc33329a335a24"
KNOWN_TOKENS = {
    "PROS": (18, None),
    "USDC": (6, "0xc879c018db60520f4355c26ed1a6d572cdac1815"),
    "WPROS": (18, "0x52c48d4213107b20bc583832b0d951fb9ca8f0b0"),
}


def load_mainnet_registry(path: Path = MAINNET_REGISTRY) -> dict:
    data = strict_json_loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or type(data.get("network_chain_id")) is not int
            or data["network_chain_id"] != 1672 or data.get("native_symbol") != "PROS"):
        raise ValueError("Expected Pacific Mainnet configuration (1672 / PROS)")
    tokens = data.get("tokens")
    if not isinstance(tokens, list) or len(tokens) != 3:
        raise ValueError("Expected the three supported official assets")
    seen = set()
    for token in tokens:
        symbol = token.get("symbol") if isinstance(token, dict) else None
        if symbol not in KNOWN_TOKENS or symbol in seen:
            raise ValueError("Unknown or duplicate official asset")
        decimals, address = KNOWN_TOKENS[symbol]
        if (type(token.get("decimals")) is not int or token["decimals"] != decimals
                or token.get("address") != address
                or token.get("type") != ("erc20" if address else "native")):
            raise ValueError("Official asset configuration changed; verify the registry")
        seen.add(symbol)
    return data


def mainnet_sender() -> str:
    sender = os.environ.get("PHAROS_MCP_READ_ADDRESS", "").strip()
    return validate_address(sender) if sender else ""


def validate_address(value: str) -> str:
    if (not isinstance(value, str) or not ADDRESS_RE.fullmatch(value)
            or int(value, 16) in {0, 0xDEAD}):
        raise ValueError("Use a full nonzero, non-burn EVM address")
    # Mixed case requires EIP-55 validation, not an unchecked lower-case conversion.
    # shortcut: no Keccak dependency here; ask for an explicit lowercase address.
    letters = value[2:]
    if letters != letters.lower() and letters != letters.upper():
        raise ValueError("Mixed-case checksum is not checked here; explicitly enter the lowercase address")
    return value.lower()


def hex_data(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", value):
        raise ValueError("Malformed EVM data")
    return value.lower()


def abi_uint(value: Any) -> int:
    raw = hex_data(value)
    if len(raw) != 66:
        raise ValueError("Expected one ABI uint256 word")
    return int(raw, 16)


def abi_symbol(value: Any) -> str:
    data = bytes.fromhex(hex_data(value)[2:])
    if len(data) < 64 or int.from_bytes(data[:32], "big") != 32:
        raise ValueError("Expected ABI string metadata")
    size = int.from_bytes(data[32:64], "big")
    if not 1 <= size <= 32 or len(data) != 64 + ((size + 31) // 32) * 32:
        raise ValueError("Invalid token symbol length")
    if any(data[64 + size:]):
        raise ValueError("Invalid ABI padding")
    return data[64:64 + size].decode("ascii")


def payment_transaction(payment: dict, sender: str, token: dict) -> dict:
    sender, recipient = validate_address(sender), validate_address(payment["recipient"])
    amount = int(amount_to_units(payment["amount"], token["decimals"]))
    if amount <= 0:
        raise ValueError("The payment amount must be positive")
    if recipient == sender:
        raise ValueError("Sender and recipient must differ")
    contract = token.get("address")
    if contract and recipient == contract:
        raise ValueError("Do not send tokens to their own token contract")
    return {
        "from": sender, "to": contract or recipient,
        "value": "0x0" if contract else hex(amount),
        "data": ("0xa9059cbb" + recipient[2:].rjust(64, "0") + f"{amount:064x}"
                 if contract else "0x"),
    }


def collect_chain_state(client, payment: dict, sender: str, token: dict) -> dict:
    """Pinned state and eth_call; nonce pending and fee quotes explicitly use live state."""
    tx = payment_transaction(payment, sender, token)
    sender = tx["from"]
    chain = client.call("get_chain_info")
    if (type(chain.get("chainId")) is not int or chain["chainId"] != 1672
            or chain.get("chainIdMatches") is not True or chain.get("nativeSymbol") != "PROS"):
        raise ValueError("Chain MCP is not serving Pacific Mainnet (1672 / PROS)")
    block = quantity(client.call("get_block_number")["blockNumber"], "block")
    tag = hex(block)
    header = client.call("get_block", {"block": tag})
    if (not isinstance(header, dict) or quantity(header.get("number"), "block") != block
            or not re.fullmatch(r"0x[0-9a-fA-F]{64}", str(header.get("hash", "")))):
        raise ValueError("Chain MCP returned a different or invalid block")
    state = {
        "block_number": block, "block_hash": header["hash"],
        "block_timestamp": quantity(header.get("timestamp"), "block timestamp"),
        "native_balance": quantity(client.call("get_balance", {
            "address": sender, "block": tag,
        })["balanceWei"], "native balance"),
        "sender_code": hex_data(client.call("get_code", {"address": sender, "block": tag})["code"]),
        "recipient_code": hex_data(client.call("get_code", {
            "address": payment["recipient"], "block": tag,
        })["code"]),
        "nonce": quantity(client.call("get_transaction_count", {
            "address": sender, "block": "pending",
        })["nonce"], "pending nonce"),
        "transaction": tx, "token_verified": True,
    }
    contract = token.get("address")
    if contract:
        code = hex_data(client.call("get_code", {"address": contract, "block": tag})["code"])
        def call(data):
            return client.call("eth_call", {
                "transaction": {"to": contract, "data": data}, "block": tag,
            })["result"]
        if not code or code == "0x":
            raise ValueError("Official token address has no contract code")
        symbol = abi_symbol(call("0x95d89b41"))
        decimals = abi_uint(call("0x313ce567"))
        if symbol != token["symbol"] or decimals != token["decimals"]:
            raise ValueError("On-chain token metadata disagrees with the official registry")
        state["token_balance"] = abi_uint(call("0x70a08231" + sender[2:].rjust(64, "0")))
    else:
        state["token_balance"] = state["native_balance"]
    state["simulation_ok"], state["simulation_error"] = False, ""
    try:
        result = hex_data(client.call("eth_call", {"transaction": tx, "block": tag})["result"])
        if contract and abi_uint(result) != 1:
            raise ValueError("Token transfer simulation did not return true")
        state["simulation_ok"] = True
    except (McpToolError, ValueError) as exc:
        state["simulation_error"] = str(exc)[:300]
    state["gas"], state["gas_price"], state["fee_error"] = None, None, ""
    try:
        estimated = quantity(client.call("estimate_gas", {"transaction": tx})["gas"], "gas")
        price = quantity(client.call("get_gas_price")["gasPriceWei"], "gas price")
        gas = (estimated * 12 + 9) // 10
        if estimated < 21000 or gas > 30_000_000 or price <= 0 or gas * price >= 2 ** 256:
            raise ValueError("Invalid network fee quote")
        state.update(gas=gas, gas_price=price, gas_estimate=estimated)
    except (McpToolError, ValueError) as exc:
        state["fee_error"] = str(exc)[:300]
    return state


def decide_mainnet_payment(payment: dict, state: dict, token: dict, policy: dict, now: datetime) -> dict:
    """The skill's pure decision consumes live MCP state, not parallel informational reads."""
    amount = int(amount_to_units(payment["amount"], token["decimals"]))
    limit = int(amount_to_units(policy["max_single_payment"], token["decimals"]))
    time_ok, time_details = evaluate_time_window(now, policy["allowed_time_windows"])
    fee = (state["gas"] * state["gas_price"]
           if state["gas"] is not None and state["gas_price"] is not None else None)
    required_native = (amount if token["type"] == "native" else 0) + (fee or 0)
    checks = [
        {"name": "amount_limit", "passed": 0 < amount <= limit,
         "details": f"{payment['amount']} {token['symbol']} / limit {policy['max_single_payment']}"},
        {"name": "token_allowlist", "passed": token["symbol"] in policy["allowed_tokens"],
         "details": token["symbol"]},
        {"name": "recipient_trust", "passed": payment["recipient"].lower() in
         [value.lower() for value in policy["trusted_recipients"]],
         "details": payment["recipient"]},
        {"name": "time_window", "passed": time_ok, "details": time_details},
        {"name": "sender_type", "passed": state["sender_code"] == "0x",
         "details": "Only ordinary EOA senders are supported; read-only address is not ownership proof"},
        {"name": "token_balance", "passed": state["token_balance"] >= amount,
         "details": f"{units_to_amount(state['token_balance'], token['decimals'])} {token['symbol']} available"},
        {"name": "native_fee_balance", "passed": fee is not None and state["native_balance"] >= required_native,
         "details": (f"{units_to_amount(required_native, 18)} PROS required including the gas limit"
                     if fee is not None else "Native fee requirement unavailable; no draft can be created")},
        {"name": "simulation", "passed": state["simulation_ok"],
         "details": state["simulation_error"] or f"eth_call succeeded at block {state['block_number']}"},
        {"name": "fee_quote", "passed": fee is not None,
         "details": state["fee_error"] or "Current estimate × 1.2 (rounded up); not pinned to the review block"},
    ]
    return policy_result(checks, policy, now)


def make_mainnet_review(text: str, answers: dict | None = None, *, sender: str = "",
                        policy_path: Path = MAINNET_POLICY, client=None,
                        now: datetime | None = None) -> dict:
    fixed_now = now
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Review time must be timezone-aware")
    registry, policy = load_mainnet_registry(), load_policy(policy_path)
    contacts = load_contacts()
    parsed = parse_mainnet_request(text, answers, contacts)
    result = {
        "id": uuid4().hex, "record_type": "payment_review", "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(), "mode": "mcp",
        "request_text": text, "answers": deepcopy(answers or {}),
        "status": parsed["status"], "field": parsed.get("field", ""),
        "question": parsed.get("question", ""), "reason": parsed.get("reason", ""),
        "requires_rewrite": parsed.get("requires_rewrite", False),
        "message": parsed.get("message", ""), "payment": parsed.get("payment"),
        "network": {"name": "Pharos Pacific Mainnet", "chain_id": 1672, "source": "chain_mcp"},
        "wallet": {"address": sender, "source": "configured_readonly_address"},
        "checks": [], "balance": None, "fee": {"status": "not_checked"},
        "policy_decision": None, "draft_allowed": False, "draft_label": "Create unsigned transaction",
        "configuration_hash": "0x" + sha256_json({"registry": registry, "policy": policy,
                                                 "sender": sender, "contacts": contacts}),
        "recorded": False,
    }
    if parsed["status"] == "complete":
        token = next((t for t in registry["tokens"] if t["symbol"] == parsed["payment"]["token"]), None)
        try:
            if token is None:
                raise ValueError("Official MCP supports PROS, USDC and WPROS on chain 1672; PHRS is testnet")
            if not sender:
                result["status"] = "data_unavailable"
                raise ValueError("Set PHAROS_MCP_READ_ADDRESS to the lowercase public address to inspect")
            payment_transaction(parsed["payment"], sender, token)
            result["token"] = deepcopy(token)
        except ValueError as exc:
            result["status"] = "data_unavailable" if not sender else "blocked"
            result["message"] = str(exc)
            result["checks"].append({"name": "request", "label": "Request",
                                    "passed": False, "detail": str(exc), "source": "request"})
        else:
            own_client = client is None
            if own_client:
                client = PharosMcpClient()
            try:
                state = collect_chain_state(client, parsed["payment"], sender, token)
                # Recheck time-window policy after I/O, not at the start of a slow call.
                now = fixed_now or datetime.now(timezone.utc)
                head_time = datetime.fromtimestamp(state["block_timestamp"], timezone.utc)
                if not -30 <= (now - head_time).total_seconds() <= 600:
                    raise ValueError("Chain MCP block is stale or future-dated")
                decision = decide_mainnet_payment(parsed["payment"], state, token, policy, now)
                fee = state["gas"] * state["gas_price"] if state["gas"] is not None else None
                result.update(
                    status="ready" if decision["outcome"] == "allow" else "blocked",
                    draft_allowed=decision["outcome"] == "allow", policy_decision=decision,
                    balance={"available": units_to_amount(state["token_balance"], token["decimals"]),
                             "required": parsed["payment"]["amount"], "source": "chain_mcp"},
                    fee={"status": "estimated" if fee is not None else "unavailable",
                         "amount": units_to_amount(fee or 0, 18), "symbol": "PROS",
                         "gas": state["gas"], "gas_price": state["gas_price"], "source": "chain_mcp",
                         "native_available": units_to_amount(state["native_balance"], 18)},
                )
                result["network"].update(block_number=state["block_number"], block_hash=state["block_hash"],
                                         fetched_at=now.isoformat())
                result["checks"] = [
                    {"name": c["name"], "label": c["name"].replace("_", " ").title(),
                     "passed": c["passed"], "detail": c["details"], "source": "chain_mcp_skill"}
                    for c in decision["checks"]
                ]
                result["checks"].insert(0, {
                    "name": "token_contract", "label": "Official asset", "passed": True,
                    "detail": (f"Code, symbol and decimals checked at {token['address']}" if token.get("address")
                               else "Native PROS, no token contract; chain ID and native symbol verified"),
                    "source": "chain_mcp",
                })
                if state["recipient_code"] != "0x":
                    result["message"] = "Recipient has contract code; verify it independently. Simulation is not a guarantee."
                if result["draft_allowed"]:
                    result["prepared_transaction"] = {
                        "chainId": 1672, **state["transaction"], "nonce": hex(state["nonce"]),
                        "gas": hex(state["gas"]), "gasPrice": hex(state["gas_price"]),
                    }
                result["proof"] = {
                    "schema": "pharos-payment-proof/1",
                    "transport": "Streamable HTTP", "endpoint": DEFAULT_MCP_URL,
                    "server": deepcopy(getattr(client, "server_info", {})),
                    "skill": {"name": "pharos-payment-review", "upstream_commit": SKILL_COMMIT,
                              "handler": "payment_chain.decide_mainnet_payment",
                              "fee_rule": "ceil(estimate_gas * 1.2)"},
                    "input": {"payment": deepcopy(parsed["payment"]), "sender": sender},
                    "policy": deepcopy(policy), "token": deepcopy(token),
                    "evaluated_at": now.isoformat(), "state": deepcopy(state),
                    "trace": deepcopy(client.trace), "decision": deepcopy(decision),
                    "transaction": deepcopy(result.get("prepared_transaction")),
                    "boundary": "Evidence replay is not authenticated chain provenance or wallet authorization.",
                    "unpinned": ["get_transaction_count: pending", "estimate_gas: current", "get_gas_price: current"],
                }
                result["proof"]["digest"] = "0x" + sha256_json(result["proof"])
            except (ValueError, OSError, KeyError, TypeError, OverflowError) as exc:
                result.pop("prepared_transaction", None)
                result.pop("proof", None)
                result.update(status="data_unavailable", draft_allowed=False,
                              message=f"Official Chain MCP checks failed: {str(exc)[:240]}. No demo fallback.")
            finally:
                if own_client:
                    client.close()
    result["integrity_hash"] = review_fingerprint(result)
    return result


def mainnet_draft(review: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if (review.get("mode") != "mcp" or review.get("status") != "ready"
            or review.get("integrity_hash") != review_fingerprint(review)):
        raise ValueError("Current verified MCP review required")
    created = datetime.fromisoformat(review["created_at"])
    expires = datetime.fromisoformat(review["expires_at"])
    if not 0 <= (now - created).total_seconds() <= 60 or now >= expires:
        raise ValueError("MCP review is stale")
    tx = deepcopy(review["prepared_transaction"])
    payment, token = review["payment"], review["token"]
    raw = int(amount_to_units(payment["amount"], token["decimals"]))
    uri = (f"ethereum:{token['address']}@1672/transfer?address={payment['recipient']}&uint256={raw}"
           if token.get("address") else f"ethereum:{payment['recipient']}@1672?value={raw}")
    draft = {
        "id": uuid4().hex, "record_type": "payment_draft", "review_id": review["id"],
        "created_at": now.isoformat(), "expires_at": review["expires_at"],
        "mode": "mcp", "status": "draft_created", "kind": "unsigned_transaction",
        "wallet_ready": True, "payment": deepcopy(payment), "network": deepcopy(review["network"]),
        "checks": deepcopy(review["checks"]), "fee": deepcopy(review["fee"]),
        "balance": deepcopy(review["balance"]), "transaction": tx, "wallet_uri": uri,
        "proof": deepcopy(review["proof"]), "signed": False, "broadcast": False, "recorded": False,
        "message": "Unsigned mainnet request only. Verify the account, chain, token, recipient, nonce and fees in your wallet. Nothing signed or sent.",
    }
    draft["integrity_hash"] = review_fingerprint(draft)
    return draft
