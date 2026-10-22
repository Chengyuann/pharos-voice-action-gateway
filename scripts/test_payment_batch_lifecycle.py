"""Batch lifecycle regressions; all chain responses are deterministic fixtures."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from threading import Event

import pytest

from payment_actions import ActionStore, validate_input
from payment_batch import make_batch_review, batch_draft
from payment_ledger import PaymentLedger, BudgetDenied, load_budget_policy, draft_charges
from payment_preview import review_fingerprint
from payment_proof import verify_proof
from policy_gatekeeper_server import Handler
from test_payment_actions import action_request, credentials, reviewed_action, confirm, draft
from test_payment_chain import FakeChain, SENDER, RECIPIENT, NOW
from test_payment_preview_http import server, request

BATCH = {"kind": "batch", "mode": "mcp", "answers": {},
         "text": 'Pay alice 0.1 USDC memo "Invoice; 24"; Pay alice 0.2 USDC'}


@pytest.fixture
def chain(monkeypatch):
    settings = {}
    monkeypatch.setenv("PHAROS_MCP_READ_ADDRESS", SENDER)
    monkeypatch.delenv("PHAROS_SECONDARY_RPC_URL", raising=False)
    monkeypatch.setattr("payment_chain.PharosMcpClient",
                        lambda: FakeChain(datetime.now(timezone.utc), **settings))
    return settings


def entries(server):
    return PaymentLedger(server.payment_report_dir / "_actions.sqlite3").export()["entries"]


def test_same_block_collector_keeps_each_proof_independently_replayable():
    fake = FakeChain()
    review = make_batch_review(BATCH["text"], sender=SENDER, client=fake, now=NOW)
    assert review["status"] == "ready"
    first, second = [item["proof"] for item in review["items"]]
    assert "snapshot_block" not in first and second["snapshot_block"] == 100
    assert all(verify_proof(proof)["verified"] for proof in (first, second))
    assert sum(entry["tool"] == "get_block_number" for entry in fake.trace) == 1
    output = batch_draft(review, NOW)
    assert [item["transaction"]["nonce"] for item in output["items"]] == ["0x3", "0x4"]
    assert output["items"][0]["memo"] == "Invoice; 24"
    assert all(item["transaction"]["data"].startswith("0xa9059cbb") for item in output["items"])
    assert all("wallet_uri" not in item for item in output["items"])
    assert output["sequential_simulation"] is output["atomic_execution"] is output["signing_ready"] is False
    assert "transaction" not in output


def test_full_batch_requires_confirmation_and_retry_is_idempotent(server, chain):
    session, review = reviewed_action(server, BATCH)
    assert review["status"] == "ready" and review["nonce_sequence"] == ["3", "4"]
    assert draft(server, session, review)[0] == 409
    assert confirm(server, session, review)[0] == 200
    status, output = draft(server, session, review)
    assert status == 200 and output["kind"] == "unsigned_batch"
    assert output["integrity_hash"] == review_fingerprint(output)
    assert draft(server, session, review) == (200, output)
    charge = {item["asset"]: item["units"] for item in output["rolling_budget"]["charges"]}
    assert charge == {"USDC": "300000", "PROS": "50400"}
    assert [item["nonce"] for item in output["rolling_budget"]["nonce_reservations"]] == ["3", "4"]
    assert session["token"] not in json.dumps(output)
    assert sum(e["kind"] == "draft_budget_reserved" for e in entries(server)) == 1
    status, _, recent = request(server, "GET", "/api/payment/recent")
    assert status == 200 and any(item["payment_count"] == 2 for item in recent["records"])


@pytest.mark.parametrize("changes", [
    {"nonce": 4}, {"price": 2}, {"tokens": 2_000_000}, {"native": 2 * 10**18},
    {"simulation": "false"},
])
def test_chain_change_after_confirmation_blocks_entire_export(server, chain, changes):
    session, review = reviewed_action(server, BATCH)
    confirm(server, session, review)
    chain.update(changes)
    assert draft(server, session, review)[0] == 409
    assert not any(e["kind"] == "draft_budget_reserved" for e in entries(server))


def test_batch_and_single_compete_for_the_same_nonce(server, chain):
    batch = reviewed_action(server, BATCH)
    single = reviewed_action(server, {"text": "Pay alice 0.01 USDC", "answers": {}, "mode": "mcp"})
    for pair in (batch, single):
        assert confirm(server, *pair)[0] == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: draft(server, *pair), (batch, single)))
    assert sorted(status for status, _ in results) == [200, 409]
    failed = next(result for status, result in results if status == 409)
    assert "nonce" in failed["error"]
    assert sum(e["kind"] == "draft_budget_reserved" for e in entries(server)) == 1


def test_budget_rejection_is_all_or_nothing(server, chain):
    policy = load_budget_policy()
    policy["limits"]["1672"]["USDC"] = "0.2"
    path = server.report_dir.parent / "batch-budget.json"
    path.write_text(json.dumps(policy))
    server.budget_policy_path = path
    session, review = reviewed_action(server, BATCH)
    assert review["status"] == "ready"
    confirm(server, session, review)
    status, result = draft(server, session, review)
    assert status == 409 and "budget" in result["error"]
    assert not any(e["kind"] == "draft_budget_reserved" for e in entries(server))
    single, review = reviewed_action(server, {"text": "Pay alice 0.01 USDC", "answers": {}, "mode": "mcp"})
    confirm(server, single, review)
    assert draft(server, single, review)[0] == 200  # Failed batch reserved no nonce or asset.


def test_aggregate_balance_failure_and_contract_recipient_block():
    result = make_batch_review("Pay alice 0.6 USDC; Pay alice 0.7 USDC",
                               sender=SENDER, client=FakeChain(), now=NOW)
    assert result["status"] == "blocked"
    with pytest.raises(ValueError):
        batch_draft(result, NOW)
    contract = make_batch_review("Pay bob 0.1 USDC; Pay alice 0.2 USDC",
                                 sender=SENDER, client=FakeChain(), now=NOW)
    assert contract["status"] == "blocked"
    assert not next(check for check in contract["checks"]
                    if check["name"] == "ordinary_recipient_accounts")["passed"]


def test_clarification_does_not_create_partial_review(server, chain):
    payload = {**BATCH, "text": "Pay alice USDC; Pay alice 0.2 USDC"}
    session, result = reviewed_action(server, payload)
    assert result["status"] == "needs_details" and result["items"] == result["payments"] == []
    assert confirm(server, session, result)[0] == 409
    status, updated = action_request(server, "/api/payment/action/update", {
        **credentials(session), "input": {**payload, "answers": {"0": {"amount": "0.1"}}},
    }, session["token"])
    assert status == 200
    session.update(updated)
    status, reviewed = action_request(server, "/api/payment/review", credentials(session), session["token"])
    assert status == 200 and reviewed["status"] == "ready"


def test_memo_edit_invalidates_the_whole_confirmation(server, chain):
    session, review = reviewed_action(server, BATCH)
    confirm(server, session, review)
    status, updated = action_request(server, "/api/payment/action/update", {
        **credentials(session), "input": {**BATCH, "text": BATCH["text"].replace("24", "25")},
    }, session["token"])
    assert status == 200
    assert draft(server, session, review)[0] == 409
    session.update(updated)
    assert draft(server, session, review)[0] == 409


@pytest.mark.parametrize("operation", ["review", "draft"])
def test_cancel_during_batch_work_rejects_late_output(server, chain, monkeypatch, operation):
    session, review = reviewed_action(server, BATCH)
    confirm(server, session, review)
    entered, resume = Event(), Event()
    name = "evaluate_payment" if operation == "review" else "create_payment_draft"
    original = getattr(Handler, name)
    def slow(self, payload, **kwargs):
        entered.set()
        assert resume.wait(4)
        return original(self, payload, **kwargs)
    monkeypatch.setattr(Handler, name, slow)
    payload = credentials(session, **({"review_id": review["id"]} if operation == "draft" else {}))
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(action_request, server, f"/api/payment/{operation}", payload, session["token"])
        try:
            assert entered.wait(2)
            assert action_request(server, "/api/payment/action/cancel",
                                  {"action_id": session["action_id"]}, session["token"])[0] == 200
        finally:
            resume.set()
        assert job.result()[0] == 409
    assert not any(e["kind"] == "draft_budget_reserved" for e in entries(server))


def test_failed_export_reserves_budget_and_nonces_across_restart(server, chain, monkeypatch):
    import policy_gatekeeper_server as module
    session, review = reviewed_action(server, BATCH)
    confirm(server, session, review)
    original = module.persist_report
    def fail(report, directory):
        if report.get("kind") == "unsigned_batch":
            raise OSError("full disk")
        return original(report, directory)
    with monkeypatch.context() as patch:
        patch.setattr(module, "persist_report", fail)
        assert draft(server, session, review)[0] == 503
    monkeypatch.setattr(Handler, "action_store", lambda self: ActionStore(
        self.payment_report_dir() / "_actions.sqlite3", epoch="restart"))
    other, current = reviewed_action(server, BATCH)
    confirm(server, other, current)
    status, result = draft(server, other, current)
    assert status == 409 and "nonce" in result["error"]
    assert sum(e["kind"] == "draft_budget_reserved" for e in entries(server)) == 1
    assert not any(e["kind"] == "draft_published" for e in entries(server))


def test_nonce_reservations_do_not_expire_with_issuance_window(tmp_path):
    review = make_batch_review(BATCH["text"], sender=SENDER, client=FakeChain(), now=NOW)
    output = batch_draft(review, NOW)
    ledger = PaymentLedger(tmp_path / "ledger.sqlite3")
    kwargs = {"draft": output, "action_id": "a"*32, "charges": draft_charges(output, load_budget_policy()),
              "window_seconds": 60, "now": NOW}
    ledger.authorize(**kwargs)
    later = deepcopy(output)
    later["id"] = "b"*32
    with pytest.raises(BudgetDenied, match="nonce"):
        ledger.authorize(**{**kwargs, "draft": later, "now": NOW + timedelta(days=1)})


def test_new_pending_range_can_be_reserved_after_chain_progress(server, chain):
    session, review = reviewed_action(server, BATCH)
    confirm(server, session, review)
    assert draft(server, session, review)[0] == 200
    chain["nonce"] = 5
    session, review = reviewed_action(server, BATCH)
    confirm(server, session, review)
    status, output = draft(server, session, review)
    assert status == 200 and output["nonce_sequence"] == ["5", "6"]


@pytest.mark.parametrize("payload", [
    {**BATCH, "mode": "demo"}, {**BATCH, "kind": "unknown"},
    {**BATCH, "answers": {"5": {"amount": "1"}}},
    {**BATCH, "answers": {"0": {"amount": True}}},
])
def test_batch_input_schema_is_strict(payload):
    with pytest.raises(ValueError):
        validate_input(payload)


def test_partial_chain_failure_never_exports_subset(monkeypatch):
    calls = 0
    def factory():
        nonlocal calls
        calls += 1
        return FakeChain(NOW, simulation="false" if calls == 2 else None)
    monkeypatch.setattr("payment_chain.PharosMcpClient", factory)
    result = make_batch_review(BATCH["text"], sender=SENDER, now=NOW)
    assert result["status"] == "blocked" and not result["draft_allowed"]
    assert len(result["items"]) == 2
    assert "nonce_sequence" not in result
    with pytest.raises(ValueError):
        batch_draft(result, NOW)


def test_large_nonce_stays_exact_for_browser_display():
    nonce = 2**60 + 3
    result = make_batch_review(BATCH["text"], sender=SENDER, client=FakeChain(nonce=nonce), now=NOW)
    assert result["nonce_sequence"] == [str(nonce), str(nonce+1)]
    output = batch_draft(result, NOW)
    assert output["nonce_sequence"] == [str(nonce), str(nonce+1)]
    assert [int(item["transaction"]["nonce"], 16) for item in output["items"]] == [nonce, nonce+1]


def test_legacy_issuance_without_nonce_metadata_blocks_same_sender(tmp_path):
    from pharos_voice_action_gateway import sha256_json
    ledger = PaymentLedger(tmp_path / "ledger.sqlite3")
    output = batch_draft(make_batch_review(BATCH["text"], sender=SENDER, client=FakeChain(), now=NOW), NOW)
    charges = draft_charges(output, load_budget_policy())
    legacy_id, action_id = "a"*32, "b"*32
    legacy = {"draft_id": legacy_id, "action_id": action_id, "charges": charges,
              "material_hash": sha256_json({}), "window_seconds": 60,
              "counting_basis": "unsigned_draft_issued"}
    with ledger.transaction() as db:
        db.execute("INSERT INTO p1_issuances VALUES(?,?,?,?,?)",
                   (legacy_id, action_id, legacy["material_hash"], NOW.isoformat(), json.dumps(charges)))
        PaymentLedger.append_to(db, "draft_budget_reserved", legacy, NOW)
    with pytest.raises(BudgetDenied, match="Legacy"):
        ledger.authorize(draft=output, action_id=action_id, charges=charges, window_seconds=60, now=NOW)


def test_secondary_failure_blocks_whole_batch(monkeypatch):
    import payment_crosscheck
    monkeypatch.setenv("PHAROS_SECONDARY_RPC_URL", "https://rpc.example.test")
    monkeypatch.setattr(payment_crosscheck, "crosscheck",
                        lambda proof, reader: {"status": "mismatch"})
    result = make_batch_review(BATCH["text"], sender=SENDER, client=FakeChain(), now=NOW)
    assert result["status"] == "data_unavailable" and not result["draft_allowed"]
    assert "nonce_sequence" not in result


def test_policy_change_after_confirmation_blocks_batch(server, chain):
    session, review = reviewed_action(server, BATCH)
    confirm(server, session, review)
    policy = load_budget_policy()
    policy["limits"]["1672"]["USDC"] = "1001"
    path = server.report_dir.parent / "changed-policy.json"
    path.write_text(json.dumps(policy))
    server.budget_policy_path = path
    assert draft(server, session, review)[0] == 409
    assert not any(e["kind"] == "draft_budget_reserved" for e in entries(server))


@pytest.mark.parametrize("mutation", ["nonce", "memo", "expiry", "row"])
def test_corrupted_or_stale_batch_cannot_produce_draft(mutation):
    result = make_batch_review(BATCH["text"], sender=SENDER, client=FakeChain(), now=NOW)
    if mutation == "nonce":
        result["nonce_sequence"] = ["3", "5"]
        result["integrity_hash"] = review_fingerprint(result)
    elif mutation == "memo":
        result["payments"][0]["memo"] = "different memo"
    elif mutation == "expiry":
        result["expires_at"] = NOW.isoformat()
        result["integrity_hash"] = review_fingerprint(result)
    else:
        result["items"][1]["proof"] = deepcopy(result["items"][0]["proof"])
        result["integrity_hash"] = review_fingerprint(result)
    with pytest.raises(ValueError):
        batch_draft(result, NOW)
