import pytest

from payment_language import parse_mainnet_request, spoken_amount

ALICE = "0x" + "1" * 40


@pytest.mark.parametrize("text", [
    "I want to send one USDC to Alice.",
    "I would like to send 1 USDC to alice",
    "I'd like to transfer 1 USDC to alice!",
    "Could you please send one USDC to alice?",
    "Please send 1 USDC to alice.",
    "Pay Alice one USDC",
    "Send Alice one USDC",
    "Send 1 USDC to alice, please.",
    "请给alice转1 USDC。",
])
def test_common_payment_forms_preserve_exact_fields(text):
    assert parse_mainnet_request(text) == {
        "status": "complete",
        "payment": {"amount": "1", "token": "USDC", "recipient": ALICE},
    }


@pytest.mark.parametrize("text,amount", [
    ("twenty five", "25"), ("one hundred and five", "105"),
    ("one thousand two hundred thirty-four", "1234"),
    ("zero point zero zero one", "0.001"), ("point five", "0.5"),
    ("1,234.50", "1234.50"), ("one million", "1000000"),
])
def test_exact_spoken_numbers(text, amount):
    assert spoken_amount(text) == amount


@pytest.mark.parametrize("text", [
    "one two", "a few", "half", "one hundred and", "one thousand thousand",
    "zero thousand", "twenty zero", "1,23", "1e3", "one point twenty five",
])
def test_never_guess_approximate_or_malformed_amounts(text):
    with pytest.raises(ValueError):
        spoken_amount(text)


def test_spoken_ambiguity_is_answered_explicitly():
    text = "I want to send one or two USDC to alice."
    result = parse_mainnet_request(text)
    assert result["status"] == "needs_details" and result["field"] == "amount"
    assert parse_mainnet_request(text, {"amount": "two"})["payment"]["amount"] == "2"


@pytest.mark.parametrize("text", [
    "I do not want to send 1 USDC to alice.",
    "Please don't send one USDC to Alice.",
    "Send 1 USDC to alice and transfer 2 USDC to bob.",
    "Send one USDC to alice tomorrow.",
    "Send 1 USDC to alice except change the recipient to bob.",
    "不要给alice转1 USDC",
])
def test_language_improvements_do_not_discard_unsafe_words(text):
    assert parse_mainnet_request(text)["status"] != "complete"


def test_unknown_contact_keeps_follow_up():
    value = parse_mainnet_request("I would like to send one USDC to alice.eth.")
    assert value["status"] == "needs_details" and value["field"] == "recipient"
