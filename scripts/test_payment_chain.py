"""Deterministic Chain MCP, payment grammar, proof and lifecycle regressions."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from payment_chain import make_mainnet_review, mainnet_draft, validate_address, abi_symbol
from payment_language import parse_mainnet_request
from payment_proof import ReplayMismatch, verify_proof
from payment_preview import review_fingerprint
from pharos_mcp import McpToolError, PharosMcpClient, quantity
from pharos_voice_action_gateway import sha256_json
from test_payment_preview_http import server
from test_payment_actions import (
    INPUT, action_request, new_action, credentials, reviewed_action, confirm, draft,
)


NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
SENDER, RECIPIENT = "0x" + "3" * 40, "0x" + "1" * 40


def abi_text(text):
    raw = text.encode("ascii")
    return "0x" + f"{32:064x}{len(raw):064x}" + raw.hex().ljust(64, "0")


class FakeChain:
    server_info = {"name": "pharos-mcp-server", "version": "fixture"}

    def __init__(self, now=NOW, **overrides):
        self.now, self.trace = now, []
        self.options = dict(chain=1672, symbol="USDC", decimals=6, native=10**18,
                            tokens=1_000_000, gas=21000, price=1, nonce=3,
                            sender_code="0x", contract_code="0x6000",
                            block=100, simulation=None, fee_error=False)
        self.options.update(overrides)

    def call(self, tool, arguments=None):
        args, o = arguments or {}, self.options
        result = None
        error = None
        if tool == "get_chain_info":
            result = {"chainId": o["chain"], "chainIdMatches": True, "nativeSymbol": "PROS"}
        elif tool == "get_block_number":
            result = {"blockNumber": str(o["block"])}
        elif tool == "get_block":
            result = {"number": hex(o["block"]), "hash": "0x" + "a" * 64,
                      "timestamp": hex(int(self.now.timestamp()))}
        elif tool == "get_balance":
            result = {"balanceWei": str(o["native"])}
        elif tool == "get_transaction_count":
            result = {"nonce": str(o["nonce"])}
        elif tool == "get_code":
            result = {"code": o["sender_code"] if args["address"] == SENDER else
                      "0x" if args["address"] == RECIPIENT else o["contract_code"]}
        elif tool == "eth_call":
            data = args["transaction"]["data"]
            if data == "0x95d89b41":
                result = {"result": abi_text(o["symbol"])}
            elif data == "0x313ce567":
                result = {"result": "0x" + f"{o['decimals']:064x}"}
            elif data.startswith("0x70a08231"):
                result = {"result": "0x" + f"{o['tokens']:064x}"}
            elif o["simulation"] == "revert":
                error = {"error": {"message": "execution reverted"}}
            else:
                result = {"result": "0x" if data == "0x" else "0x" + f"{0 if o['simulation'] == 'false' else 1:064x}"}
        elif tool == "estimate_gas":
            if o["fee_error"]:
                error = {"error": {"message": "gas unavailable"}}
            else:
                result = {"gas": str(o["gas"])}
        elif tool == "get_gas_price":
            result = {"gasPriceWei": str(o["price"])}
        else:
            raise AssertionError(f"Unexpected tool {tool}")
        entry = {"tool": tool, "arguments": deepcopy(args)}
        entry["error" if error else "result"] = error or result
        self.trace.append(deepcopy(entry))
        if error:
            raise McpToolError(error["error"]["message"])
        return deepcopy(result)

    def close(self):
        pass


def review(token="USDC", **opts):
    fake = FakeChain(**opts)
    return make_mainnet_review(f"Send 0.01 {token} to {RECIPIENT}",
                               sender=SENDER, client=fake, now=NOW)


def redigest(proof):
    proof.pop("digest", None)
    proof["digest"] = "0x" + sha256_json(proof)


@pytest.mark.parametrize("token,options", [
    ("PROS", {}), ("USDC", {}), ("WPROS", {"symbol": "WPROS", "decimals": 18, "tokens": 10**18}),
])
def test_official_assets_review_draft_and_replay(token, options):
    result = review(token, **options)
    assert result["status"] == "ready"
    prepared = mainnet_draft(result, NOW)
    assert prepared["signed"] is prepared["broadcast"] is False
    assert prepared["transaction"]["chainId"] == 1672
    assert prepared["transaction"]["nonce"] == "0x3"
    assert prepared["transaction"]["gas"] == hex(25200)
    assert prepared["wallet_uri"].startswith("ethereum:")
    assert verify_proof(prepared["proof"])["verified"] is True
    if token != "PROS":
        assert prepared["transaction"]["data"].startswith("0xa9059cbb")
        assert prepared["transaction"]["to"] == result["token"]["address"]
        assert prepared["transaction"]["value"] == "0x0"
    for entry in result["proof"]["trace"]:
        tool = entry["tool"]
        if tool in {"get_balance", "get_code", "eth_call", "get_block"}:
            assert entry["arguments"]["block"] == "0x64"
        if tool == "get_transaction_count":
            assert entry["arguments"]["block"] == "pending"
    assert result["fee"]["symbol"] == "PROS"


@pytest.mark.parametrize("options,status", [
    ({"chain": 1}, "data_unavailable"), ({"symbol": "WRONG"}, "data_unavailable"),
    ({"decimals": 18}, "data_unavailable"), ({"contract_code": "0x"}, "data_unavailable"),
    ({"native": 0}, "blocked"), ({"tokens": 0}, "blocked"),
    ({"simulation": "revert"}, "blocked"), ({"simulation": "false"}, "blocked"),
    ({"fee_error": True}, "blocked"), ({"gas": 1}, "blocked"), ({"price": 0}, "blocked"),
    ({"sender_code": "0xef0100"}, "blocked"),
])
def test_failure_is_closed_and_never_produces_draft(options, status):
    result = review(**options)
    assert result["status"] == status
    assert not result["draft_allowed"] and "prepared_transaction" not in result
    with pytest.raises(ValueError):
        mainnet_draft(result, NOW)
    if "proof" in result:
        assert verify_proof(result["proof"])["decision"] == "deny"


@pytest.mark.parametrize("value", [True, -1, 2**256, "0x", "1e3", "f", "9"*79, None])
def test_quantities_are_bounded_unsigned_integers(value):
    with pytest.raises(ValueError):
        quantity(value, "test")


def test_decimal_quantity_with_leading_zero_and_hex():
    assert quantity("001", "test") == quantity("0x1", "test") == 1


@pytest.mark.parametrize("text,answers", [
    ("Pay alice 0.01 USDC", {}),
    ("Could you send 0.01 USDC to alice", {}),
    ("Send 0.01 or 0.02 USDC to alice", {"amount": "0.01"}),
    ("Send $0.01 to alice", {"token": "USDC"}),
    ("Send 0.01 USDC to alice.eth", {"recipient": RECIPIENT}),
    ("Send", {"amount": "0.01", "token": "USDC", "recipient": RECIPIENT}),
])
def test_clarified_language_preserves_exact_fields(text, answers):
    result = parse_mainnet_request(text, answers)
    assert result == {"status": "complete", "payment": {"amount": "0.01", "token": "USDC", "recipient": RECIPIENT}}


@pytest.mark.parametrize("text,field", [
    ("Send 1 or 2 USDC to alice", "amount"),
    ("Send $1 to alice", "token"),
    ("Send 1 USDC to alice.eth", "recipient"),
    ("Send 1 USDC to unknown", "recipient"),
    ("Send USDC to alice", "amount"),
])
def test_ambiguity_asks_instead_of_guessing(text, field):
    result = parse_mainnet_request(text)
    assert result["status"] == "needs_details" and result["field"] == field


@pytest.mark.parametrize("text,answers", [
    ("Do not send 1 USDC to alice", {}),
    ("Send 1 USDC to alice tomorrow", {}),
    ("Send 1 USDC to alice and send 2 USDC to bob", {}),
    ("Send 1 USDC to alice", {"amount": "2"}),
    ("Send 1 USDC to alice", {"recipient": SENDER}),
    ("Send 1 or 2 USDC to alice", {"amount": "9"}),
    ("Send 1 USDC to alice.eth", {"recipient": "yes"}),
])
def test_unsupported_or_conflicting_instructions_never_complete(text, answers):
    assert parse_mainnet_request(text, answers)["status"] != "complete"


def test_negation_before_contact_resolution_never_becomes_a_payment():
    result = parse_mainnet_request("Do not send 1 USDC to alice")
    assert result["status"] == "invalid"
    assert "Negated" in result["message"]


def test_precision_and_cross_network_tokens_rejected():
    for text in [f"Send 0.0000001 USDC to {RECIPIENT}", f"Send 1 PHRS to {RECIPIENT}"]:
        result = make_mainnet_review(text, sender=SENDER, client=FakeChain(), now=NOW)
        assert result["status"] == "blocked"


@pytest.mark.parametrize("address", ["0x123", "0x"+"0"*40, "0x"+"0"*36+"dead", "0x"+"a"*39+"B"])
def test_unsafe_or_unchecked_checksum_addresses_rejected(address):
    with pytest.raises(ValueError):
        validate_address(address)


def test_bad_abi_string_rejected():
    for value in ["0x", "0x" + "f"*128, abi_text("USDC")[:-2] + "01"]:
        with pytest.raises(ValueError):
            abi_symbol(value)


@pytest.mark.parametrize("field", ["trace", "state", "decision", "transaction", "token", "unpinned"])
def test_proof_rejects_tampering_even_after_rehash(field):
    proof = deepcopy(review()["proof"])
    if field == "trace":
        proof[field][3]["arguments"]["block"] = "latest"
    elif field == "state":
        proof[field]["nonce"] += 1
    elif field == "decision":
        proof[field]["outcome"] = "deny"
    elif field == "transaction":
        proof[field]["to"] = SENDER
    elif field == "token":
        proof[field]["decimals"] = 18
    else:
        proof[field] = []
    redigest(proof)
    with pytest.raises((ValueError, ReplayMismatch)):
        verify_proof(proof)


def test_digest_and_expiry_are_checked():
    result = review()
    result["proof"]["state"]["nonce"] = 99
    with pytest.raises(ValueError, match="digest"):
        verify_proof(result["proof"])
    with pytest.raises(ValueError):
        mainnet_draft(result, NOW)
    result = review()
    with pytest.raises(ValueError, match="stale"):
        mainnet_draft(result, NOW + timedelta(seconds=61))


@pytest.fixture
def mcp_context(monkeypatch):
    options = {}
    monkeypatch.setenv("PHAROS_MCP_READ_ADDRESS", SENDER)
    monkeypatch.setattr("payment_chain.PharosMcpClient",
                        lambda: FakeChain(datetime.now(timezone.utc), **options))
    return options


def test_mcp_action_preserves_confirmation_and_idempotency(server, mcp_context):
    session, result = reviewed_action(server, {**INPUT, "text": "Pay alice 0.01 USDC", "mode": "mcp"})
    assert result["status"] == "ready"
    assert draft(server, session, result)[0] == 409
    assert confirm(server, session, result)[0] == 200
    status, created = draft(server, session, result)
    assert status == 200 and verify_proof(created["proof"])["verified"]
    assert draft(server, session, result) == (200, created)
    assert session["token"] not in json.dumps(created)


@pytest.mark.parametrize("field,value", [("nonce", 4), ("price", 2), ("native", 2*10**18), ("tokens", 2_000_000)])
def test_changed_mcp_data_invalidates_confirmation(server, mcp_context, field, value):
    session, result = reviewed_action(server, {**INPUT, "text": "Pay alice 0.01 USDC", "mode": "mcp"})
    assert confirm(server, session, result)[0] == 200
    mcp_context[field] = value
    assert draft(server, session, result)[0] == 409


def test_mcp_correction_retains_token_and_recipient(server, mcp_context):
    session, result = reviewed_action(server, {**INPUT, "text": "Pay alice 0.01 USDC", "mode": "mcp"})
    assert confirm(server, session, result)[0] == 200
    status, changed = action_request(server, "/api/payment/action/correct",
                                    {**credentials(session), "text": "改成0.02 USDC"}, session["token"])
    assert status == 200
    assert changed["input"]["text"] == f"Send 0.02 USDC to {RECIPIENT}"
    assert draft(server, session, result)[0] == 409


@pytest.mark.parametrize("operation", ["review", "draft"])
def test_cancel_during_mcp_io_never_publishes_late_results(server, mcp_context, monkeypatch, operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import policy_gatekeeper_server as http

    session = new_action(server, {**INPUT, "text": "Pay alice 0.01 USDC", "mode": "mcp"})
    result = None
    if operation == "draft":
        status, result = action_request(server, "/api/payment/review", credentials(session), session["token"])
        assert status == 200 and confirm(server, session, result)[0] == 200
    entered, resume = Event(), Event()
    original = http.make_mainnet_review

    def slow(*args, **kwargs):
        entered.set()
        assert resume.wait(4)
        return original(*args, **kwargs)

    monkeypatch.setattr(http, "make_mainnet_review", slow)
    payload = credentials(session, **({"review_id": result["id"]} if result else {}))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(action_request, server, f"/api/payment/{operation}", payload, session["token"])
        try:
            assert entered.wait(2)
            status, stopped = action_request(server, "/api/payment/action/cancel",
                                             {"action_id": session["action_id"]}, session["token"])
            assert status == 200 and stopped["status"] == "cancelled"
        finally:
            resume.set()
        assert pending.result()[0] == 409
    records = list(server.payment_report_dir.glob("*.json"))
    assert len(records) == (1 if operation == "draft" else 0)


def test_expired_mcp_review_cannot_be_drafted_even_with_rehashed_expiry():
    result = review()
    result["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
    result["integrity_hash"] = review_fingerprint(result)
    with pytest.raises(ValueError, match="stale"):
        mainnet_draft(result, NOW)
