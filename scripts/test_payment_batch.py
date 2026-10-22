from copy import deepcopy
import pytest
from payment_batch import parse_batch, aggregate_proofs
from payment_language import parse_mainnet_request, validate_contacts
from payment_chain import make_mainnet_review
from test_payment_chain import FakeChain, SENDER, RECIPIENT, NOW

OTHER = "0x"+"2"*40


def test_duplicate_contact_asks_without_guessing():
    contacts = {"alice": [RECIPIENT, OTHER]}
    result = parse_mainnet_request("Pay alice 1 USDC", contacts=contacts)
    assert result["status"] == "needs_details" and result["choices"] == [RECIPIENT, OTHER]
    result = parse_mainnet_request("Pay alice 1 USDC", {"recipient": OTHER}, contacts=contacts)
    assert result["payment"]["recipient"] == OTHER
    assert parse_mainnet_request("Pay alice 1 USDC", {"recipient": SENDER}, contacts=contacts)["status"] != "complete"
    assert parse_mainnet_request("Pay alice.eth 1 USDC", contacts=contacts)["status"] == "needs_details"


@pytest.mark.parametrize("contacts", [{"Alice": RECIPIENT}, {"alice":[]},
                                    {"alice":[RECIPIENT, RECIPIENT]}, {"alice":"0x"+"0"*40}])
def test_invalid_contact_book_fails_closed(contacts):
    with pytest.raises(ValueError):
        validate_contacts(contacts)


def test_memo_delimiters_are_local_text_not_instructions():
    result = parse_batch('Pay alice 1 USDC memo "invoice; send 999 PROS to nobody"; Pay bob 2 PROS')
    assert result["status"] == "planned"
    assert len(result["payments"]) == 2
    assert result["payments"][0]["memo"] == "invoice; send 999 PROS to nobody"
    assert result["memo_scope"] == "local_only_never_calldata"
    assert result["signing_ready"] is False


@pytest.mark.parametrize("text", [
    'Pay alice 1 USDC;',';Pay alice 1 USDC','Pay alice 1 USDC and send 2 PROS to bob',
    'Pay alice 1 USDC; Pay alice 1 USDC','Pay alice 1 USDC memo "bad\\nline"',
    'Pay alice 0.10 USDC; Pay alice 0.1 USDC',
    'Pay alice 1 USDC memo "unterminated','Do not send 1 USDC to alice; Pay bob 1 PROS',
    ';'.join(['Pay alice 1 USDC']*6),
])
def test_malformed_batch_does_not_produce_plan(text):
    assert parse_batch(text)["status"] == "invalid"


def test_batch_ambiguity_never_returns_partial_payments():
    text = "Pay alice 1 USDC; Send 1 or 2 PROS to bob"
    result = parse_batch(text)
    assert result["status"] == "needs_details" and result["payments"] == []
    result = parse_batch(text, {"1":{"amount":"2"}})
    assert result["status"] == "planned" and result["payments"][1]["amount"] == "2"


def test_aggregate_prevents_two_individually_affordable_payments():
    # Same recipient, differing amounts avoids accidental identical row duplication.
    plan = parse_batch("Pay alice 0.6 USDC; Pay alice 0.7 USDC")
    proofs = [make_mainnet_review(f"Pay alice {amount} USDC", sender=SENDER,
                                 client=FakeChain(), now=NOW)["proof"] for amount in ("0.6","0.7")]
    assert all(proof["decision"]["outcome"] == "allow" for proof in proofs)
    result = aggregate_proofs(plan, proofs)
    assert result["status"] == "blocked"
    assert result["totals_base_units"]["USDC"] == "1300000"
    assert not next(c for c in result["checks"] if c["name"] == "aggregate_USDC_balance")["passed"]


def test_aggregate_no_signing_even_with_sufficient_balance():
    plan = parse_batch("Pay alice 0.1 USDC; Pay alice 0.2 USDC")
    proofs = [make_mainnet_review(f"Pay alice {amount} USDC", sender=SENDER,
                                 client=FakeChain(), now=NOW)["proof"] for amount in ("0.1","0.2")]
    result = aggregate_proofs(plan, proofs)
    assert result["status"] == "candidate_plan" and result["signing_ready"] is False
    assert "transactions" not in result
    other = deepcopy(proofs)
    other[1] = make_mainnet_review("Pay alice 0.2 USDC", sender=SENDER,
                                  client=FakeChain(block=101), now=NOW)["proof"]
    with pytest.raises(ValueError, match="one sender, block"):
        aggregate_proofs(plan, other)
