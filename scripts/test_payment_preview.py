from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from payment_preview import (
    DEMO_WALLET_PATH,
    POLICY_PATH,
    TOKEN_REGISTRY_PATH,
    RpcReader,
    create_transaction_draft,
    load_demo_wallet,
    load_policy,
    load_token_registry,
    parse_payment_request,
    review_payment,
    units_to_amount,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
RECIPIENT = "0x1111111111111111111111111111111111111111"


@pytest.fixture
def context():
    return {
        "wallet": load_demo_wallet(DEMO_WALLET_PATH),
        "registry": load_token_registry(TOKEN_REGISTRY_PATH),
        "policy": load_policy(POLICY_PATH),
    }


def review(text, context, **kwargs):
    return review_payment(
        text,
        kwargs.pop("answers", None),
        kwargs.pop("mode", "demo"),
        context["wallet"],
        context["registry"],
        context["policy"],
        kwargs.pop("live_snapshot", None),
        kwargs.pop("now", NOW),
    )


def test_complete_demo_request_is_ready_and_creates_example_draft(context):
    result = review(f"Send 0.02 PHRS to {RECIPIENT}", context)

    assert result["status"] == "ready"
    assert result["draft_allowed"] is True
    assert result["draft_label"] == "Create example draft"
    assert [(check["name"], check["passed"]) for check in result["checks"]] == [
        ("recipient_format", True),
        ("token_registry", True),
        ("amount", True),
        ("balance", True),
        ("payment_policy", True),
    ]
    draft = create_transaction_draft(result, NOW)
    assert draft["kind"] == "example_transaction_draft"
    assert draft["wallet_ready"] is False
    assert draft["signed"] is False
    assert draft["broadcast"] is False
    assert draft["transaction"] == {
        "chainId": 688689,
        "from": "0x3333333333333333333333333333333333333333",
        "to": RECIPIENT,
        "value": "0x470de4df820000",
        "data": "0x",
    }


@pytest.mark.parametrize(("text", "field", "question"), [
    (f"Send PHRS to {RECIPIENT}", "amount", "How much would you like to send?"),
    ("Send 0.02 PHRS", "recipient", "Enter the recipient's full address."),
    (f"Send 0.02 to {RECIPIENT}", "token", "Which token should be used?"),
])
def test_missing_details_ask_one_clear_question(text, field, question, context):
    result = review(text, context)
    assert result["status"] == "needs_details"
    assert result["field"] == field
    assert result["question"] == question
    with pytest.raises(ValueError, match="not ready"):
        create_transaction_draft(result, NOW)


def test_answers_can_complete_missing_fields(context):
    result = review("Send 0.02 PHRS", context, answers={"recipient": RECIPIENT})
    assert result["status"] == "ready"
    assert result["payment"]["recipient"] == RECIPIENT


def test_unsupported_token_blocks_without_policy_noise(context):
    result = review(f"Send 2 ABC to {RECIPIENT}", context)
    assert result["status"] == "blocked"
    assert [(check["name"], check["passed"]) for check in result["checks"]] == [
        ("recipient_format", True),
        ("token_registry", False),
    ]
    assert result["policy_decision"] is None


def test_insufficient_balance_and_policy_denial_are_separate(context):
    insufficient = review(f"Send 0.40 PHRS to {RECIPIENT}", context)
    assert insufficient["status"] == "blocked"
    assert {check["name"]: check["passed"] for check in insufficient["checks"]}["balance"] is False
    assert {check["name"]: check["passed"] for check in insufficient["checks"]}["payment_policy"] is False

    blocked_address = review("Send 0.02 PHRS to 0x9999999999999999999999999999999999999999", context)
    by_name = {check["name"]: check for check in blocked_address["checks"]}
    assert by_name["recipient_format"]["passed"] is True
    assert by_name["balance"]["passed"] is True
    assert by_name["payment_policy"]["passed"] is False
    assert blocked_address["policy_decision"]["deciding_rule"] == "recipient_trust"


@pytest.mark.parametrize("text", [
    "",
    "please prepare a payment",
    f"Send 0.02 PHRS to {RECIPIENT} and 0x2222222222222222222222222222222222222222",
    f"Send 0.02 PHRS to {RECIPIENT}",
])
def test_parser_never_invents_missing_or_ambiguous_data(text, context):
    result = parse_payment_request(text)
    if " and " in text:
        assert result["status"] == "needs_details"
        assert result["field"] == "recipient"
    elif text.startswith("Send"):
        assert result["status"] == "complete"
    else:
        assert result["status"] == "invalid"


@pytest.mark.parametrize("text", [
    f"Do not send 0.02 PHRS to {RECIPIENT}",
    f"Send 0.02 PHRS to {RECIPIENT} tomorrow",
    f"Send 0.02 PHRS to {RECIPIENT} every week",
    f"Send 0.02 PHRS to {RECIPIENT} and then send 0.01 PHRS to {RECIPIENT}",
    f"Send 0.02 PHRS memo coffee to {RECIPIENT}",
])
def test_unsupported_or_extra_instructions_are_invalid(text):
    assert parse_payment_request(text)["status"] == "invalid"


def test_answers_cannot_override_existing_request_fields():
    assert parse_payment_request(f"Send 0.02 PHRS to {RECIPIENT}",
                                 {"amount": "0.03"})["status"] == "invalid"
    assert parse_payment_request(f"Send 0.02 PHRS to {RECIPIENT}",
                                 {"recipient": "0x2222222222222222222222222222222222222222"})["status"] == "invalid"


def test_draft_is_bound_to_review_and_expires(context):
    result = review(f"Send 0.02 PHRS to {RECIPIENT}", context)
    changed = {**result, "payment": {**result["payment"], "amount": "0.03"}}
    with pytest.raises(ValueError, match="changed"):
        create_transaction_draft(changed, NOW)
    with pytest.raises(ValueError, match="expired"):
        create_transaction_draft(result, NOW + timedelta(seconds=601))


def test_live_mode_never_uses_demo_balance_as_fallback(context):
    result = review(f"Send 0.02 PHRS to {RECIPIENT}", context, mode="live")
    assert result["status"] == "data_unavailable"
    assert result["message"] == "Live network data is unavailable."
    assert result["checks"][0]["name"] == "recipient_format"
    with pytest.raises(ValueError, match="not ready"):
        create_transaction_draft(result, NOW)


def test_live_fee_unavailable_blocks_draft(context):
    snapshot = {
        "chain_id": 688689,
        "balance_units": int("1000000000000000000"),
        "gas_units": None,
        "gas_price_units": None,
        "fee_units": None,
        "fee_error": "The network could not estimate the fee. No transaction draft will be created.",
        "source": "live_rpc",
        "block_number": 7,
        "fetched_at": NOW.isoformat(),
    }
    result = review(f"Send 0.02 PHRS to {RECIPIENT}", context, mode="live",
                    live_snapshot=snapshot)
    assert result["status"] == "data_unavailable"
    assert result["fee"]["status"] == "unavailable"
    assert {check["name"]: check["passed"] for check in result["checks"]}["network_fee"] is False


def test_live_snapshot_can_create_wallet_ready_unsigned_transaction(context):
    snapshot = {
        "chain_id": 688689,
        "balance_units": int("1000000000000000000"),
        "gas_units": 21000,
        "gas_price_units": 1,
        "fee_units": 21000,
        "source": "live_rpc",
        "block_number": 9,
        "fetched_at": NOW.isoformat(),
    }
    result = review(f"Send 0.02 PHRS to {RECIPIENT}", context, mode="live",
                    live_snapshot=snapshot)
    assert result["status"] == "ready"
    assert result["balance"]["source"] == "live_rpc"
    assert result["network"]["block_number"] == 9
    assert result["fee"]["status"] == "estimated"
    draft = create_transaction_draft(result, NOW)
    assert draft["kind"] == "unsigned_transaction"
    assert draft["wallet_ready"] is True
    assert draft["transaction"]["gas"] == "0x5208"
    assert draft["transaction"]["gasPrice"] == "0x1"


def test_live_snapshot_wrong_chain_is_data_unavailable(context):
    snapshot = {
        "chain_id": 1,
        "balance_units": int("1000000000000000000"),
        "gas_units": 21000,
        "gas_price_units": 1,
        "fee_units": 21000,
        "source": "live_rpc",
        "fetched_at": NOW.isoformat(),
    }
    result = review(f"Send 0.02 PHRS to {RECIPIENT}", context, mode="live",
                    live_snapshot=snapshot)
    assert result["status"] == "data_unavailable"
    assert result["message"] == "The RPC is connected to a different network."


def test_units_to_amount_formats_without_floating_point():
    assert units_to_amount(20000000000000000, 18) == "0.02"
    assert units_to_amount(1000000000000000000, 18) == "1"
    assert units_to_amount(1, 0) == "1"


def test_rpc_reader_rejects_unsafe_urls():
    with pytest.raises(ValueError, match="HTTP"):
        RpcReader("file:///tmp/socket")
    with pytest.raises(ValueError, match="credentials"):
        RpcReader("https://user@example.com")


@pytest.mark.parametrize("amount", ["-1", "+1", "NaN", "Infinity", "1e3", "0", "1.0000000000000000001"])
def test_invalid_amounts_never_create_drafts(context, amount):
    result = review(f"Send {amount} PHRS to {RECIPIENT}", context)
    assert result["status"] != "ready"
    assert result["draft_allowed"] is False


@pytest.mark.parametrize("address", ["0x" + "0" * 40, "0x" + "1" * 41, "0x123", "alice.eth"])
def test_recipient_is_not_truncated_or_resolved(context, address):
    result = review(f"Send 0.02 PHRS to {address}", context)
    assert result["status"] == "blocked"
    assert result["payment"]["recipient"] == address


def test_multiple_missing_answers_accumulate(context):
    assert review("Send", context)["field"] == "amount"
    assert review("Send", context, answers={"amount": "0.02"})["field"] == "token"
    assert review("Send", context, answers={"amount": "0.02", "token": "PHRS"})["field"] == "recipient"
    result = review("Send", context, answers={"amount": "0.02", "token": "PHRS", "recipient": RECIPIENT})
    assert result["status"] == "ready"


@pytest.mark.parametrize("balance", ["NaN", "-1", "1e400", 0.1])
def test_bad_fixture_balance_rejected(tmp_path, context, balance):
    import json
    context["wallet"]["balances"]["PHRS"] = balance
    path = tmp_path / "wallet.json"
    path.write_text(json.dumps(context["wallet"]))
    with pytest.raises(ValueError):
        load_demo_wallet(path)


def test_registry_does_not_claim_erc20_support(tmp_path, context):
    import json
    context["registry"]["tokens"][0]["type"] = "erc20"
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps(context["registry"]))
    with pytest.raises(ValueError, match="native token"):
        load_token_registry(path)


@pytest.mark.parametrize("age", [-10, 61])
def test_stale_or_future_live_snapshot_is_not_ready(context, age):
    snapshot = {
        "chain_id": 688689, "balance_units": 10 ** 18, "gas_units": 21000,
        "gas_price_units": 1, "fee_units": 21000, "source": "live_rpc",
        "fetched_at": (NOW - timedelta(seconds=age)).isoformat(),
    }
    result = review(f"Send 0.02 PHRS to {RECIPIENT}", context, mode="live", live_snapshot=snapshot)
    assert result["status"] == "data_unavailable"


def test_expiry_is_exclusive(context):
    result = review(f"Send 0.02 PHRS to {RECIPIENT}", context)
    with pytest.raises(ValueError, match="expired"):
        create_transaction_draft(result, NOW + timedelta(seconds=600))
