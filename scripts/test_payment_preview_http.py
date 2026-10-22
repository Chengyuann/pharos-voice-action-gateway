"""Loopback HTTP checks for the Payment Preview product."""

import json
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from payment_preview import POLICY_PATH
from policy_gatekeeper_server import Handler


RECIPIENT = "0x1111111111111111111111111111111111111111"


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.delenv("PHAROS_RPC_URL", raising=False)
    monkeypatch.delenv("PHAROS_READ_ADDRESS", raising=False)
    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    instance.report_dir = tmp_path / "policy"
    instance.payment_report_dir = tmp_path / "payment"
    instance.policy_path = POLICY_PATH
    instance.allow_legacy_payment_api = True
    thread = Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


def request(server, method, path, payload=None):
    client = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        body = None if payload is None else json.dumps(payload)
        headers = {} if body is None else {"Content-Type": "application/json"}
        client.request(method, path, body=body, headers=headers)
        response = client.getresponse()
        data = response.read()
        content_type = dict(response.getheaders()).get("Content-Type", "")
        parsed = json.loads(data) if "application/json" in content_type else data
        return response.status, dict(response.getheaders()), parsed
    finally:
        client.close()


def test_root_redirects_to_product_and_page_loads(server):
    status, headers, _ = request(server, "GET", "/")
    assert status == 302
    assert headers["Location"] == "/payment-preview"
    status, _, page = request(server, "GET", "/payment-preview")
    assert status == 200
    assert b"Pharos Voice Action Gateway" in page
    assert b"MIC NOT CONNECTED" in page
    assert b"Committed transcript or typed fallback" in page
    assert b"This product does not sign or send transactions." in page


def test_config_labels_fixture_data_and_live_unavailable(server):
    status, _, config = request(server, "GET", "/api/payment/config")
    assert status == 200
    assert config["wallet"]["network"]["source"] == "demo_config"
    assert config["wallet"]["wallet"]["source"] == "demo_wallet_snapshot"
    assert config["registry"]["source"] == "demo_token_registry"
    assert config["live_available"] is False
    assert "PHAROS_RPC_URL" in config["live_hint"]


def test_voice_payment_demo_exposes_real_gateway_decisions_without_claiming_mic_capture(server):
    status, _, demo = request(server, "GET", "/api/voice/demo?scenario=payment")
    assert status == 200
    assert demo["source_type"] == "local_jsonl_fixture_replay"
    assert demo["microphone_capture"] is False
    assert demo["audio_playback"] is False
    assert demo["raw_audio_stored"] is False
    assert demo["intent"]["action"] == "send_payment"
    assert demo["confirmation"]["status"] == "confirmed"
    assert demo["payment_command"].startswith("send 0.02 PHRS")
    assert demo["evidence"]["voice_hash"].startswith("0x")
    assert demo["evidence"]["mandate_hash"].startswith("0x")
    assert demo["evidence"]["scope"] == "fixture_only_not_payment_draft"
    assert demo["confirmation_reusable"] is False
    assert demo["execution_attempted"] is False
    assert demo["recorded"] is False
    assert not server.payment_report_dir.exists()


def test_voice_demo_is_read_only_and_hashes_are_deterministic(server, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Voice replay must not invoke payment or execution")
    monkeypatch.setattr("policy_gatekeeper_server.submit_or_simulate", forbidden)
    monkeypatch.setattr("policy_gatekeeper_server.prepare_action", forbidden)
    monkeypatch.setattr("policy_gatekeeper_server.rpc_from_environment", forbidden)
    monkeypatch.setattr("policy_gatekeeper_server.save_report", forbidden)
    status, _, first = request(server, "GET", "/api/voice/demo?scenario=payment")
    again, _, second = request(server, "GET", "/api/voice/demo?scenario=payment")
    assert status == again == 200
    assert first == second
    assert not server.report_dir.exists()


def test_voice_barge_in_and_short_pause_demos_use_duplex_gateway_outputs(server):
    status, _, barge_in = request(server, "GET", "/api/voice/demo?scenario=barge-in")
    assert status == 200
    assert barge_in["interrupts"] == 1
    assert "interrupt_tts" in [event["action"] for event in barge_in["output_events"]]

    status, _, short_pause = request(server, "GET", "/api/voice/demo?scenario=short-pause")
    assert status == 200
    assert "hold" in [event["action"] for event in short_pause["output_events"]]
    assert short_pause["committed_turns"][-1].endswith("然后生成一个待办")


def test_unknown_voice_demo_is_rejected(server):
    status, _, body = request(server, "GET", "/api/voice/demo?scenario=unknown")
    assert status == 400
    assert "Voice demo" in body["error"]


def test_complete_review_and_example_draft_are_persisted(server):
    status, _, review = request(server, "POST", "/api/payment/review", {
        "text": f"Send 0.02 PHRS to {RECIPIENT}",
        "mode": "demo",
        "answers": {},
    })
    assert status == 200
    assert review["status"] == "ready"
    assert review["recorded"] is True
    assert (server.payment_report_dir / f"{review['id']}.json").exists()

    status, _, draft = request(server, "POST", "/api/payment/draft", {
        "review_id": review["id"],
    })
    assert status == 200
    assert draft["kind"] == "example_transaction_draft"
    assert draft["wallet_ready"] is False
    assert draft["signed"] is False
    assert draft["broadcast"] is False
    assert draft["recorded"] is True
    assert (server.payment_report_dir / f"{draft['id']}.json").exists()
    assert draft["transaction"]["chainId"] == 688689

    status, headers, audit = request(server, "GET", f"/api/payment/audit/{draft['id']}")
    assert status == 200
    assert headers["Content-Disposition"].startswith("attachment;")
    assert audit == draft


def test_missing_detail_can_be_completed_without_client_review_object(server):
    status, _, missing = request(server, "POST", "/api/payment/review", {
        "text": f"Send PHRS to {RECIPIENT}",
        "mode": "demo",
    })
    assert status == 200
    assert missing["status"] == "needs_details"
    assert missing["field"] == "amount"

    status, _, complete = request(server, "POST", "/api/payment/review", {
        "text": f"Send PHRS to {RECIPIENT}",
        "answers": {"amount": "0.02"},
        "mode": "demo",
    })
    assert status == 200
    assert complete["status"] == "ready"
    assert complete["payment"]["amount"] == "0.02"


def test_browser_cannot_submit_modified_review_to_draft_endpoint(server):
    status, _, body = request(server, "POST", "/api/payment/draft", {
        "review": {
            "status": "ready",
            "payment": {"amount": "9999", "recipient": RECIPIENT, "token": "PHRS"},
        }
    })
    assert status == 400
    assert body["error"] == "expected review_id"
    assert not server.payment_report_dir.exists()


def test_server_rejects_unknown_missing_blocked_or_expired_reviews(server):
    for review_id in ["0" * 32, "../README.md", "bad"]:
        status, _, body = request(server, "POST", "/api/payment/draft", {
            "review_id": review_id,
        })
        assert status in {400, 503}
        assert "error" in body

    status, _, blocked = request(server, "POST", "/api/payment/review", {
        "text": "Send 0.02 PHRS to 0x9999999999999999999999999999999999999999",
        "mode": "demo",
    })
    assert blocked["status"] == "blocked"
    status, _, body = request(server, "POST", "/api/payment/draft", {
        "review_id": blocked["id"],
    })
    assert status == 400
    assert body["error"] == "payment review is not ready"


def test_recent_records_include_reviews_and_drafts(server):
    status, _, review = request(server, "POST", "/api/payment/review", {
        "text": f"Send 0.02 PHRS to {RECIPIENT}",
        "mode": "demo",
    })
    status, _, draft = request(server, "POST", "/api/payment/draft", {
        "review_id": review["id"],
    })
    status, _, recent = request(server, "GET", "/api/payment/recent")
    assert status == 200
    ids = [record["id"] for record in recent["records"]]
    assert draft["id"] in ids
    assert review["id"] in ids


def test_live_mode_never_falls_back_to_demo_data(server):
    status, _, result = request(server, "POST", "/api/payment/review", {
        "text": f"Send 0.02 PHRS to {RECIPIENT}",
        "mode": "live",
    })
    assert status == 200
    assert result["status"] == "data_unavailable"
    assert result["message"] == "Set PHAROS_RPC_URL and PHAROS_READ_ADDRESS to use live network data."
    assert result["balance"] is None
    assert result["draft_allowed"] is False
    assert result["wallet"]["source"] == "not_configured"


@pytest.mark.parametrize("payload", [
    {"text": f"Send 0.02 PHRS to {RECIPIENT}", "mode": "wallet"},
    {"text": f"Send 0.02 PHRS to {RECIPIENT}", "answers": []},
    {"text": f"Send 0.02 PHRS to {RECIPIENT}", "extra": True},
])
def test_invalid_payment_requests_return_specific_400(server, payload):
    status, _, body = request(server, "POST", "/api/payment/review", payload)
    assert status == 400
    assert body["error"] != "invalid JSON request"


@pytest.fixture
def live_context(server, monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from copy import deepcopy
    from pharos_voice_action_gateway import load_policy
    monkeypatch.setenv("PHAROS_READ_ADDRESS", "0x" + "3" * 40)
    monkeypatch.setenv("PHAROS_RPC_URL", "http://127.0.0.1:1")
    policy = load_policy(POLICY_PATH)
    policy["allowed_time_windows"] = [{
        "days": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
        "start": "00:00", "end": "24:00", "timezone": "UTC",
    }]
    server.policy_path = tmp_path / "test-policy.json"
    server.policy_path.write_text(json.dumps(policy))

    class Reader:
        calls = 0
        fail = False
        balance = 10 ** 18
        price = 1

        def snapshot(self, wallet, recipient, value):
            self.calls += 1
            assert wallet == "0x" + "3" * 40
            assert recipient == RECIPIENT
            assert value == 2 * 10 ** 16
            if self.fail:
                raise OSError("endpoint unavailable")
            return deepcopy({
                "chain_id": 688689, "balance_units": self.balance,
                "gas_units": 21000, "gas_price_units": self.price,
                "fee_units": 21000 * self.price, "source": "live_rpc", "block_number": self.calls,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })
    reader = Reader()
    monkeypatch.setattr("policy_gatekeeper_server.rpc_from_environment", lambda: reader)
    return reader


def test_live_success_rechecks_and_persists_linked_records(server, live_context):
    from payment_preview import review_fingerprint
    status, _, review = request(server, "POST", "/api/payment/review", {
        "text": f"Send 0.02 PHRS to {RECIPIENT}", "mode": "live",
    })
    assert status == 200 and review["status"] == "ready"
    status, _, draft = request(server, "POST", "/api/payment/draft", {"review_id": review["id"]})
    assert status == 200
    assert live_context.calls == 2
    assert draft["wallet_ready"] is True
    assert draft["original_review_id"] == review["id"]
    assert draft["review_id"] != review["id"]
    assert draft["integrity_hash"] == review_fingerprint(draft)
    assert draft["signed"] is False and draft["broadcast"] is False
    rechecked = json.loads((server.payment_report_dir / f"{draft['review_id']}.json").read_text())
    assert rechecked["previous_review_id"] == review["id"]
    assert rechecked["network"]["block_number"] == 2


@pytest.mark.parametrize("changed", ["balance", "price", "fail"])
def test_live_changes_do_not_silently_change_confirmed_payment(server, live_context, changed):
    _, _, review = request(server, "POST", "/api/payment/review", {
        "text": f"Send 0.02 PHRS to {RECIPIENT}", "mode": "live",
    })
    if changed == "balance":
        live_context.balance = 2 * 10 ** 18
    elif changed == "price":
        live_context.price = 2
    else:
        live_context.fail = True
    status, _, body = request(server, "POST", "/api/payment/draft", {"review_id": review["id"]})
    assert status in {400, 503}
    assert "error" in body
    if changed == "fail":
        assert status == 503
        assert "Live network" in body["error"]
        assert "storage" not in body["error"]
    assert not any(json.loads(p.read_text())["record_type"] == "payment_draft"
                   for p in server.payment_report_dir.glob("*.json"))


def test_configured_live_failure_retains_no_demo_balance(server, live_context):
    live_context.fail = True
    status, _, review = request(server, "POST", "/api/payment/review", {
        "text": f"Send 0.02 PHRS to {RECIPIENT}", "mode": "live",
    })
    assert status == 200
    assert review["status"] == "data_unavailable"
    assert review["balance"] is None
    assert "No demo data" in review["message"]


def test_completed_missing_field_review_can_create_draft(server):
    _, _, review = request(server, "POST", "/api/payment/review", {
        "text": "Send", "answers": {"amount": "0.02", "token": "PHRS", "recipient": RECIPIENT},
    })
    status, _, draft = request(server, "POST", "/api/payment/draft", {"review_id": review["id"]})
    assert status == 200
    assert draft["transaction"]["to"] == RECIPIENT


def test_policy_changed_after_review_blocks_draft(server, tmp_path):
    _, _, review = request(server, "POST", "/api/payment/review", {"text": f"Send 0.02 PHRS to {RECIPIENT}"})
    policy = json.loads(POLICY_PATH.read_text())
    policy["max_single_payment"] = "0.01"
    path = tmp_path / "changed-policy.json"
    path.write_text(json.dumps(policy))
    server.policy_path = path
    status, _, body = request(server, "POST", "/api/payment/draft", {"review_id": review["id"]})
    assert status == 400 and "stale" in body["error"]


def test_modified_saved_review_is_rejected(server):
    _, _, review = request(server, "POST", "/api/payment/review", {"text": f"Send 0.02 PHRS to {RECIPIENT}"})
    path = server.payment_report_dir / f"{review['id']}.json"
    review["payment"]["amount"] = "500"
    path.write_text(json.dumps(review))
    status, _, body = request(server, "POST", "/api/payment/draft", {"review_id": review["id"]})
    assert status == 400 and "stale" in body["error"]


def test_storage_failure_never_reports_a_saved_draft(server, monkeypatch):
    def fail(*_):
        raise OSError("full")
    monkeypatch.setattr("policy_gatekeeper_server.save_report", fail)
    status, _, body = request(server, "POST", "/api/payment/review", {"text": f"Send 0.02 PHRS to {RECIPIENT}"})
    assert status == 503
    assert body["report"]["recorded"] is False
    assert "No payment was sent" in body["error"]


@pytest.mark.parametrize("asset", [
    "payment-preview.css", "payment-preview.js", "voice-console.js",
    "fonts/barlow-condensed-700.ttf", "fonts/manrope-400.ttf",
    "fonts/ibm-plex-mono-400.ttf", "fonts/barlow-condensed-600-italic.ttf",
    "fonts/manrope-700.ttf",
])
def test_product_assets_are_served(server, asset):
    status, headers, body = request(server, "GET", "/assets/" + asset)
    assert status == 200
    assert len(body) == int(headers["Content-Length"])
    assert headers["Cache-Control"] == "no-store"
    if asset.endswith(".ttf"):
        assert headers["Content-Type"] == "font/ttf"
        assert body.startswith(b"\x00\x01\x00\x00")


@pytest.mark.parametrize("path", [
    "/assets/fonts/missing.ttf",
    "/assets/fonts/../payment-preview.js",
    "/assets/fonts/%2e%2e/payment-preview.js",
    "/assets/fonts/LICENSE-Manrope.txt",
])
def test_font_route_does_not_expose_other_files(server, path):
    status, _, _ = request(server, "GET", path)
    assert status == 404


def test_corrupt_config_is_a_service_error(server, monkeypatch):
    def fail():
        raise ValueError("bad config")
    monkeypatch.setattr("policy_gatekeeper_server.load_token_registry", fail)
    status, _, body = request(server, "GET", "/api/payment/config")
    assert status == 503 and "settings" in body["error"]
