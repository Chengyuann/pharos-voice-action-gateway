import pytest
from evm_address import keccak256, checksum_address, validate_address
from payment_chain import payment_transaction, load_mainnet_registry

# Published EIP-55 vectors; no Ethereum SHA3 substitution.
VECTORS = [
    "0x52908400098527886E0F7030069857D2E4169EE7",
    "0x8617E340B3D01FA5F11F306F4090FD50E238070D",
    "0xde709f2102306220921060314715629080e2fb77",
    "0x27b1fdb04752bbc536007a920d24acb045561c26",
    "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
    "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
    "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
]


def test_keccak_vectors():
    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert keccak256(b"abc").hex() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"


@pytest.mark.parametrize("address", VECTORS)
def test_eip55(address):
    assert checksum_address(address.lower()) == address
    assert validate_address(address) == address.lower()


@pytest.mark.parametrize("address", [None, "", "0x123", "0x"+"0"*40, "0x"+"0"*36+"dEaD",
                                   "0x5aaeb6053F3E94C9b9A09f33669435E7Ef1BeAed"])
def test_invalid(address):
    with pytest.raises(ValueError):
        validate_address(address)


def test_invalid_mixed_case_clarification_cannot_be_silently_lowercased():
    from payment_language import parse_mainnet_request
    invalid = "0x5aaeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    result = parse_mainnet_request("Send", {"amount":"1", "token":"USDC", "recipient":invalid})
    assert result["status"] == "invalid"
    assert "checksum" in result["message"]


@pytest.mark.parametrize("token", load_mainnet_registry()["tokens"])
def test_known_contract_recipient_is_blocked_for_every_asset(token):
    with pytest.raises(ValueError):
        payment_transaction({"amount": "0.01", "recipient": "0xc879c018db60520f4355c26ed1a6d572cdac1815"},
                            "0x"+"3"*40, token)
