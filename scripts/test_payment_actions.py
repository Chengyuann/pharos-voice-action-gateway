"""Action lifecycle and loopback regression checks, with no live RPC or audio."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
import stat
from threading import Event

import pytest

from duplex_voice_gateway import InputEvent, process_events
from payment_actions import ActionAccessDenied, ActionConflict, ActionStore, revise_amount
from payment_preview import review_fingerprint
from policy_gatekeeper_server import Handler
from test_payment_preview_http import server, live_context, request, RECIPIENT


INPUT = {"text": f"Send 0.02 PHRS to {RECIPIENT}", "answers": {}, "mode": "demo"}


def action_request(server, path, payload, token=None):
    client = HTTPConnection("127.0.0.1", server.server_port, timeout=8)
    try:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Action-Token"] = token
        client.request("POST", path, body=json.dumps(payload), headers=headers)
        response = client.getresponse()
        return response.status, json.loads(response.read())
    finally:
        client.close()


def new_action(server, payment=INPUT):
    status, session = action_request(server, "/api/payment/action", {})
    assert status == 200
    status, updated = action_request(server, "/api/payment/action/update", {
        "action_id": session["action_id"], "revision": 0, "input": payment,
    }, session["token"])
    assert status == 200
    return {**session, **updated}


def credentials(session, **extra):
    return {"action_id": session["action_id"], "revision": session["revision"], **extra}


def reviewed_action(server, payment=INPUT):
    session = new_action(server, payment)
    status, review = action_request(server, "/api/payment/review", credentials(session), session["token"])
    assert status == 200
    return session, review


def confirm(server, session, review):
    return action_request(server, "/api/payment/action/confirm",
                          credentials(session, review_id=review["id"]), session["token"])


def draft(server, session, review):
    return action_request(server, "/api/payment/draft",
                          credentials(session, review_id=review["id"]), session["token"])


def test_draft_requires_current_confirmation_and_retry_is_idempotent(server):
    session, review = reviewed_action(server)
    assert review["action_revision"] == 1
    assert review["action_id"] == session["action_id"]
    assert session["token"] not in json.dumps(review)
    status, _ = draft(server, session, review)
    assert status == 409
    assert confirm(server, session, review)[0] == 200
    status, first = draft(server, session, review)
    assert status == 200
    assert first["confirmed_review_id"] == review["id"]
    assert first["signed"] is first["broadcast"] is False
    assert first["integrity_hash"] == review_fingerprint(first)
    assert draft(server, session, review) == (200, first)
    files = [json.loads(p.read_text()) for p in server.payment_report_dir.glob("*.json")]
    assert sum(item["record_type"] == "payment_draft" for item in files) == 1
    assert all(session["token"] not in json.dumps(item) for item in files)
    database = (server.payment_report_dir / "_actions.sqlite3").read_bytes()
    assert session["token"].encode() not in database


@pytest.mark.parametrize("instruction", ["改成0.03", "不是，改成0.03 PHRS", "change amount to 0.03"])
def test_correction_invalidates_old_review_and_changes_only_amount(server, instruction):
    session, review = reviewed_action(server)
    assert confirm(server, session, review)[0] == 200
    status, changed = action_request(server, "/api/payment/action/correct", {
        **credentials(session), "text": instruction,
    }, session["token"])
    assert status == 200
    assert changed["change"] == {"field": "amount", "before": "0.02", "after": "0.03"}
    assert changed["input"]["text"] == f"Send 0.03 PHRS to {RECIPIENT}"
    assert changed["revision"] == 2
    assert draft(server, session, review)[0] == 409
    session.update(changed)
    assert draft(server, session, review)[0] == 409
    status, fresh = action_request(server, "/api/payment/review", credentials(session), session["token"])
    assert status == 200
    assert fresh["payment"]["amount"] == "0.03"
    assert fresh["status"] == "ready"
    assert draft(server, session, fresh)[0] == 409
    assert confirm(server, session, fresh)[0] == 200
    assert draft(server, session, fresh)[0] == 200


@pytest.mark.parametrize("instruction", [
    "改成0.02或者0.03", "yes", "change recipient to Alice", "改成-1", "改成0",
    "改成99999999999999999999999999999999999999999999999999999999999999999999999999999999999",
])
def test_unclear_correction_does_not_preserve_prior_approval(server, instruction):
    session, review = reviewed_action(server)
    assert confirm(server, session, review)[0] == 200
    status, corrected = action_request(server, "/api/payment/action/correct", {
        **credentials(session), "text": instruction,
    }, session["token"])
    assert status == 200
    assert corrected["change"] is None
    assert corrected["revision"] == 2
    assert draft(server, session, review)[0] == 409


def test_input_invalidation_blocks_old_review(server):
    session, review = reviewed_action(server)
    assert confirm(server, session, review)[0] == 200
    status, changed = action_request(server, "/api/payment/action/invalidate",
                                     credentials(session), session["token"])
    assert status == 200 and changed["status"] == "editing"
    assert changed["review_id"] == ""
    assert draft(server, session, review)[0] == 409


def test_cancel_is_idempotent_and_never_resumes_same_action(server):
    session, review = reviewed_action(server)
    assert confirm(server, session, review)[0] == 200
    payload = {"action_id": session["action_id"]}
    first = action_request(server, "/api/payment/action/cancel", payload, session["token"])
    assert first[0] == 200 and first[1]["status"] == "cancelled"
    assert action_request(server, "/api/payment/action/cancel", payload, session["token"]) == first
    assert draft(server, session, review)[0] == 409
    session.update(first[1])
    assert action_request(server, "/api/payment/action/update", {
        **credentials(session), "input": INPUT,
    }, session["token"])[0] == 409
    assert action_request(server, "/api/payment/review", credentials(session), session["token"])[0] == 409


@pytest.mark.parametrize("text", ["先别转", "stop", "取消。", "暂停"])
def test_stop_in_correction_path_cancels_without_parsing(server, text):
    session, review = reviewed_action(server)
    status, result = action_request(server, "/api/payment/action/correct",
                                    {**credentials(session), "text": text}, session["token"])
    assert status == 200 and result["status"] == "cancelled"
    assert draft(server, session, review)[0] == 409


def test_action_token_and_revision_are_required_and_not_in_audit(server):
    session, review = reviewed_action(server)
    for token in [None, "wrong", "x" * 43]:
        assert action_request(server, "/api/payment/action/confirm",
                              credentials(session, review_id=review["id"]), token)[0] == 403
    status, _, body = request(server, "POST", "/api/payment/draft", {"review_id": review["id"]})
    assert status == 403 and "session" in body["error"]
    another = new_action(server)
    assert confirm(server, another, review)[0] == 409
    for revision in [True, -1, "1", None]:
        assert action_request(server, "/api/payment/review", {
            "action_id": session["action_id"], "revision": revision,
        }, session["token"])[0] == 400


def test_review_must_be_ready_untampered_and_not_expired(server):
    session, review = reviewed_action(server, {**INPUT, "text": f"Send 1 PHRS to {RECIPIENT}"})
    assert review["status"] == "blocked"
    assert confirm(server, session, review)[0] == 409
    session, review = reviewed_action(server)
    path = server.payment_report_dir / (review["id"] + ".json")
    review["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    review["integrity_hash"] = review_fingerprint(review)
    path.write_text(json.dumps(review))
    assert confirm(server, session, review)[0] == 409
    assert draft(server, session, review)[0] == 409


def test_product_server_rejects_legacy_payment_api(server):
    server.allow_legacy_payment_api = False
    status, _, body = request(server, "POST", "/api/payment/review", INPUT)
    assert status == 403 and body["error"] == "A payment action session is required."
    status, _, body = request(server, "POST", "/api/payment/draft", {"review_id": "0" * 32})
    assert status == 403 and body["error"] == "A payment action session is required."
    session, review = reviewed_action(server)
    assert confirm(server, session, review)[0] == 200
    assert draft(server, session, review)[0] == 200


@pytest.mark.parametrize("operation", ["review", "draft"])
def test_cancel_during_slow_work_prevents_publishing(server, monkeypatch, operation):
    session = new_action(server)
    review = None
    if operation == "draft":
        status, review = action_request(server, "/api/payment/review", credentials(session), session["token"])
        assert status == 200 and confirm(server, session, review)[0] == 200
    entered, resume = Event(), Event()
    name = "evaluate_payment" if operation == "review" else "create_payment_draft"
    original = getattr(Handler, name)

    def slow(self, payload, **kwargs):
        entered.set()
        assert resume.wait(4)
        return original(self, payload, **kwargs)
    monkeypatch.setattr(Handler, name, slow)
    payload = credentials(session, **({"review_id": review["id"]} if review else {}))
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(action_request, server, "/api/payment/" + operation, payload, session["token"])
        try:
            assert entered.wait(2)
            status, cancelled = action_request(server, "/api/payment/action/cancel", {
                "action_id": session["action_id"],
            }, session["token"])
            assert status == 200 and cancelled["status"] == "cancelled"
        finally:
            resume.set()
        assert pending.result()[0] == 409
    records = [json.loads(p.read_text()) for p in server.payment_report_dir.glob("*.json")]
    assert len(records) == (1 if operation == "draft" else 0)
    assert all(record["record_type"] != "payment_draft" for record in records)


def test_duplicate_draft_while_in_flight_is_blocked(server, monkeypatch):
    session, review = reviewed_action(server)
    assert confirm(server, session, review)[0] == 200
    entered, resume = Event(), Event()
    original = Handler.create_payment_draft

    def slow(self, payload, **kwargs):
        entered.set()
        assert resume.wait(4)
        return original(self, payload, **kwargs)
    monkeypatch.setattr(Handler, "create_payment_draft", slow)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(draft, server, session, review)
        try:
            assert entered.wait(2)
            assert draft(server, session, review)[0] == 409
        finally:
            resume.set()
        status, created = pending.result()
        assert status == 200
    assert draft(server, session, review) == (200, created)


def test_live_recheck_record_cannot_bypass_action_confirmation(server, live_context):
    session, review = reviewed_action(server, {**INPUT, "mode": "live"})
    assert review["status"] == "ready"
    assert confirm(server, session, review)[0] == 200
    status, created = draft(server, session, review)
    assert status == 200 and live_context.calls == 2
    assert created["review_id"] != review["id"]
    assert request(server, "POST", "/api/payment/draft", {"review_id": created["review_id"]})[0] == 403


def test_cancel_during_live_recheck_publishes_no_late_record(server, live_context, monkeypatch):
    session, review = reviewed_action(server, {**INPUT, "mode": "live"})
    assert confirm(server, session, review)[0] == 200
    entered, resume = Event(), Event()
    original = live_context.snapshot

    def slow(*args):
        entered.set()
        assert resume.wait(4)
        return original(*args)
    monkeypatch.setattr(live_context, "snapshot", slow)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(draft, server, session, review)
        try:
            assert entered.wait(2)
            status, cancelled = action_request(server, "/api/payment/action/cancel", {
                "action_id": session["action_id"],
            }, session["token"])
            assert status == 200 and cancelled["status"] == "cancelled"
        finally:
            resume.set()
        assert pending.result()[0] == 409
    records = [json.loads(p.read_text()) for p in server.payment_report_dir.glob("*.json")]
    assert [record["id"] for record in records] == [review["id"]]


def test_restart_rejects_old_action_even_with_token_and_saved_review(tmp_path):
    first = ActionStore(tmp_path / "actions.sqlite3", epoch="first")
    session = first.create()
    first.update(session["action_id"], session["token"], 0, INPUT)
    restarted = ActionStore(first.path, epoch="second")
    with pytest.raises(ActionConflict, match="server session ended"):
        restarted.begin_review(session["action_id"], session["token"], 1)
    fresh = restarted.create()
    assert fresh["action_id"] != session["action_id"]


def test_stale_revision_and_invalid_token_fail_without_changing_current_action(tmp_path):
    store = ActionStore(tmp_path / "actions.sqlite3")
    session = store.create()
    store.update(session["action_id"], session["token"], 0, INPUT)
    with pytest.raises(ActionConflict):
        store.update(session["action_id"], session["token"], 0, INPUT)
    with pytest.raises(ActionAccessDenied):
        store.cancel(session["action_id"], "x" * 43)
    assert store.begin_review(session["action_id"], session["token"], 1) == INPUT
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_correction_before_first_input_is_incomplete_not_a_server_error(server):
    status, session = action_request(server, "/api/payment/action", {})
    assert status == 200
    status, result = action_request(server, "/api/payment/action/correct", {
        **credentials(session), "text": "改成0.03",
    }, session["token"])
    assert status == 200 and result["change"] is None
    assert result["revision"] == 1
    session.update(result)
    status, review = action_request(server, "/api/payment/review", credentials(session), session["token"])
    assert status == 200 and review["status"] == "invalid"


def test_new_input_then_correction_preserves_current_recipient(server):
    session, old_review = reviewed_action(server)
    recipient = "0x" + "2" * 40
    status, updated = action_request(server, "/api/payment/action/update", {
        **credentials(session), "input": {**INPUT, "text": f"Send 0.02 PHRS to {recipient}"},
    }, session["token"])
    assert status == 200
    session.update(updated)
    status, result = action_request(server, "/api/payment/action/correct", {
        **credentials(session), "text": "改成0.03",
    }, session["token"])
    assert status == 200
    assert result["input"]["text"] == f"Send 0.03 PHRS to {recipient}"
    assert draft(server, session, old_review)[0] == 409


def test_failed_persistence_cannot_return_success_or_leave_confirmed_state(server, monkeypatch):
    session, review = reviewed_action(server)
    assert confirm(server, session, review)[0] == 200

    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr("policy_gatekeeper_server.save_report", fail)
    status, result = draft(server, session, review)
    assert status == 503 and result["report"]["recorded"] is False
    assert draft(server, session, review)[0] == 409


def test_amount_correction_does_not_change_another_token():
    with pytest.raises(ValueError, match="cannot change the token"):
        revise_amount({**INPUT, "text": f"Send 2 USDC to {RECIPIENT}"}, "改成0.03 PHRS")


def test_short_silence_no_longer_commits_a_long_partial():
    partial = InputEvent(0, "asr_partial", "send 0.02 PHRS")
    state = process_events([partial, InputEvent(.4, "silence")])
    assert [event.action for event in state.output_events] == ["listen", "hold"]
    assert state.committed_turns == []
    state = process_events([partial, InputEvent(.7, "silence")])
    assert state.committed_turns == ["send 0.02 PHRS"]
    assert state.output_events[-1].reason == "silence 700ms crossed EOU threshold"
