from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
import pytest

from payment_ledger import PaymentLedger, load_budget_policy, verify_audit_chain, verify_report_files
from policy_gatekeeper_server import Handler
from test_payment_preview_http import server, request
from test_payment_actions import INPUT, reviewed_action, confirm, draft, action_request


def set_budget(server, limit):
    path = server.report_dir.parent / "issuance-policy.json"
    policy = load_budget_policy()
    policy["limits"]["688689"]["PHRS"] = limit
    path.write_text(json.dumps(policy))
    server.budget_policy_path = path


def test_two_actions_compete_for_one_rolling_allowance(server):
    set_budget(server, "0.03")
    first = reviewed_action(server)
    second = reviewed_action(server)
    assert all(review["status"] == "ready" for _, review in (first, second))
    for session, review in (first, second):
        assert confirm(server, session, review)[0] == 200
        assert review["issuance_policy"]["limits"]["688689"]["PHRS"] == "0.03"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: draft(server, *pair), (first, second)))
    assert sorted(status for status, _ in results) == [200, 409]
    ledger = PaymentLedger(server.payment_report_dir / "_actions.sqlite3")
    chain = ledger.export()
    assert verify_audit_chain(chain)["verified"]
    assert sum(e["kind"] == "draft_budget_reserved" for e in chain["entries"]) == 1
    assert sum(e["kind"] == "budget_denied" for e in chain["entries"]) == 1
    success = next(body for status, body in results if status == 200)
    assert success["rolling_budget"]["counting_basis"] == "unsigned_draft_issued"
    assert success["signed"] is success["broadcast"] is False


def test_policy_change_requires_new_review(server):
    set_budget(server, "0.1")
    session, review = reviewed_action(server)
    assert confirm(server, session, review)[0] == 200
    set_budget(server, "0.11")  # Even a looser policy must be re-reviewed.
    status, result = draft(server, session, review)
    assert status == 409 and "policy changed" in result["error"]
    assert not list(server.payment_report_dir.glob("*draft*"))
    assert not any(e["kind"] == "draft_budget_reserved" for e in
                   PaymentLedger(server.payment_report_dir / "_actions.sqlite3").export()["entries"])


def test_cancelling_exported_draft_does_not_refund(server):
    set_budget(server, "0.03")
    session, review = reviewed_action(server)
    assert confirm(server, session, review)[0] == 200
    assert draft(server, session, review)[0] == 200
    assert action_request(server, "/api/payment/action/cancel",
                          {"action_id": session["action_id"]}, session["token"])[0] == 200
    session2, review2 = reviewed_action(server)
    assert confirm(server, session2, review2)[0] == 200
    assert draft(server, session2, review2)[0] == 409


def test_report_chain_records_denied_and_allowed_reviews(server):
    reviewed_action(server)
    reviewed_action(server, {**INPUT, "text": INPUT["text"].replace("0.02", "10")})
    chain = PaymentLedger(server.payment_report_dir / "_audit.sqlite3").export()
    assert verify_audit_chain(chain)["verified"]
    assert {e["payload"]["status"] for e in chain["entries"]} == {"ready", "blocked"}
    assert verify_report_files(chain, server.payment_report_dir)["report_files_verified"] == 2
    status, _, downloaded = request(server, "GET", "/api/payment/audit-chain?ledger=reports")
    assert status == 200 and downloaded == chain
    record = next(server.payment_report_dir.glob("*.json"))
    data = json.loads(record.read_text())
    data["status"] = "tampered"
    record.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_report_files(chain, server.payment_report_dir)


def test_audit_export_rejects_unknown_ledger_and_empty_database(server):
    assert request(server, "GET", "/api/payment/audit-chain")[0] == 404
    assert request(server, "GET", "/api/payment/audit-chain?ledger=../../secret")[0] == 400
    assert request(server, "GET", "/api/payment/audit-chain?ledger=actions&ledger=reports")[0] == 400


def test_failed_export_consumes_conservative_reservation(server, monkeypatch):
    import policy_gatekeeper_server as module
    set_budget(server, "0.03")
    session, review = reviewed_action(server)
    confirm(server, session, review)
    original = module.persist_report
    def fail_draft(report, directory):
        if report.get("record_type") == "payment_draft":
            raise OSError("simulated full disk")
        return original(report, directory)
    with monkeypatch.context() as patch:
        patch.setattr(module, "persist_report", fail_draft)
        assert draft(server, session, review)[0] == 503
    session2, review2 = reviewed_action(server)
    confirm(server, session2, review2)
    assert draft(server, session2, review2)[0] == 409
    entries = PaymentLedger(server.payment_report_dir / "_actions.sqlite3").export()["entries"]
    assert sum(e["kind"] == "draft_budget_reserved" for e in entries) == 1
    assert sum(e["kind"] == "draft_published" for e in entries) == 0


def test_native_fee_budget_is_charged_with_erc20(server, monkeypatch):
    from test_payment_chain import FakeChain, SENDER
    from datetime import datetime, timezone
    monkeypatch.setenv("PHAROS_MCP_READ_ADDRESS", SENDER)
    monkeypatch.setattr("payment_chain.PharosMcpClient", lambda: FakeChain(datetime.now(timezone.utc)))
    session, review = reviewed_action(server, {**INPUT, "text": "Pay alice 0.01 USDC", "mode": "mcp"})
    assert confirm(server, session, review)[0] == 200
    status, result = draft(server, session, review)
    assert status == 200
    charges = {c["asset"]: c for c in result["rolling_budget"]["charges"]}
    assert charges["USDC"]["units"] == "10000"
    assert charges["PROS"]["units"] == "25200"


def test_budget_persists_when_server_epoch_changes(server, monkeypatch):
    from payment_actions import ActionStore
    set_budget(server, "0.03")
    session, review = reviewed_action(server)
    confirm(server, session, review)
    assert draft(server, session, review)[0] == 200
    monkeypatch.setattr(Handler, "action_store", lambda self: ActionStore(
        self.payment_report_dir()/"_actions.sqlite3", epoch="restart",
        budget_policy_path=self.server.budget_policy_path))
    second, second_review = reviewed_action(server)
    confirm(server, second, second_review)
    assert draft(server, second, second_review)[0] == 409
