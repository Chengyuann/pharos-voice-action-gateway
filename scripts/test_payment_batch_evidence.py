from copy import deepcopy
from datetime import timedelta
import json

import pytest

from payment_batch import make_batch_review, batch_draft
from payment_batch_evidence import envelope, verify_evidence, load_evidence
from payment_preview import review_fingerprint
from pharos_voice_action_gateway import sha256_json
from test_payment_chain import FakeChain, NOW, SENDER

TEXT = 'Pay alice 0.1 USDC memo "Invoice 24"; Pay alice 0.2 USDC'


def evidence(text=TEXT):
    review = make_batch_review(text, sender=SENDER, client=FakeChain(), now=NOW)
    value = envelope(review, 1)
    value["captured_at"] = (NOW + timedelta(seconds=1)).isoformat()
    rehash(value)
    return value


def rehash(value):
    for row in value["review"]["items"]:
        row["integrity_hash"] = review_fingerprint(row)
    value["review"]["integrity_hash"] = review_fingerprint(value["review"])
    value["digest"] = "0x" + sha256_json({key: item for key, item in value.items() if key != "digest"})


def test_batch_capture_replays_without_network():
    result = verify_evidence(evidence())
    assert result["verified"] and result["rows"] == 2 and result["calls"] == 27
    assert result["status"] == "ready" and result["nonce_sequence"] == ["3", "4"]
    assert not result["authorization_verified"] and not result["chain_authenticity_verified"]


def test_aggregate_denial_is_verifiable_evidence():
    result = verify_evidence(evidence("Pay alice 0.6 USDC; Pay alice 0.7 USDC"))
    assert result["verified"] and result["status"] == "blocked"


@pytest.mark.parametrize("field", ["transaction", "fee", "balance", "nonce", "memo", "status", "checks", "network", "secondary"])
def test_rehashed_misleading_evidence_fails_replay(field):
    value = evidence()
    review, row = value["review"], value["review"]["items"][0]
    if field == "transaction":
        row["prepared_transaction"]["nonce"] = "0x9"
    elif field == "fee":
        row["fee"]["amount"] = "0"
    elif field == "balance":
        row["balance"]["available"] = "9999"
    elif field == "nonce":
        review["nonce_sequence"] = ["3", "5"]
    elif field == "memo":
        review["payments"] = deepcopy(review["payments"])
        review["payments"][0]["memo"] = "Wrong invoice"
    elif field == "status":
        review["draft_allowed"] = False
    elif field == "checks":
        review["checks"][0]["passed"] = False
    elif field == "network":
        row["network"]["block_number"] = 101
    else:
        row["secondary_check"] = {"status": "mismatch"}
    rehash(value)
    with pytest.raises(ValueError):
        verify_evidence(value)


def test_failed_capture_is_not_a_complete_verifiable_batch():
    review = make_batch_review(TEXT, sender=SENDER, client=FakeChain(simulation="false"), now=NOW)
    value = envelope(review, 1)
    value["captured_at"] = (NOW + timedelta(seconds=1)).isoformat()
    rehash(value)
    with pytest.raises((ValueError, KeyError)):
        verify_evidence(value)


def test_evidence_loader_rejects_symlink_and_oversize(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence()))
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        load_evidence(link)
    assert verify_evidence(load_evidence(path))["verified"]
    path.write_text(" " * 2_000_001)
    with pytest.raises(ValueError):
        load_evidence(path)


def test_draft_rejects_transaction_mutation_even_after_rehash():
    review = evidence()["review"]
    review["items"][1]["prepared_transaction"]["to"] = SENDER
    for row in review["items"]:
        row["integrity_hash"] = review_fingerprint(row)
    review["integrity_hash"] = review_fingerprint(review)
    with pytest.raises(ValueError, match="disagrees"):
        batch_draft(review, NOW)


def test_all_rows_share_one_monotonic_deadline(monkeypatch):
    import payment_batch
    original = payment_batch.make_mainnet_review
    seen, clock = [], [100.0]
    monkeypatch.setattr(payment_batch.time, "monotonic", lambda: clock[0])
    def slow(*args, **kwargs):
        seen.append(kwargs["deadline"])
        result = original(*args, **kwargs)
        clock[0] += 61
        return result
    monkeypatch.setattr(payment_batch, "make_mainnet_review", slow)
    result = make_batch_review(TEXT, sender=SENDER, client=FakeChain(), now=NOW)
    assert seen == [160.0]  # No second network row after the shared deadline.
    assert result["status"] == "data_unavailable" and not result["draft_allowed"]


def test_mcp_close_does_not_exceed_an_expired_deadline(monkeypatch):
    from pharos_mcp import PharosMcpClient
    client = PharosMcpClient()
    client.session_id = "example"
    client.deadline = 0
    monkeypatch.setattr(client.opener, "open", lambda *args, **kwargs: pytest.fail("Unexpected request"))
    client.close()
    assert client.session_id == ""
