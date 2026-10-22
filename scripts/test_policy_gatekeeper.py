from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from duplex_voice_gateway import load_events, process_events
from pharos_voice_action_gateway import (
    ConfirmationDecision,
    amount_to_units,
    evaluate_policy,
    extract_voice_intent,
    load_policy,
    parse_evaluation_time,
    prepare_action,
    run,
    submit_or_simulate,
)
from policy_gatekeeper_server import AuditPersistenceError, evaluate_request, save_report


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "policy_gatekeeper.json"
ALLOWED_TIME = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
DENIED_TIME = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)


def checks_by_name(result):
    return {
        check["name"]: check
        for check in result.prepared_action["policy_decision"]["checks"]
    }


def test_policy_gatekeeper_allows_request_inside_all_rules():
    result = run(
        ROOT / "demo" / "pharos_payment_confirmed.jsonl",
        policy_config=load_policy(POLICY_PATH),
        evaluated_at=ALLOWED_TIME,
    )

    decision = result.prepared_action["policy_decision"]
    assert decision["outcome"] == "allow"
    assert decision["deciding_rule"] == "all_rules_passed"
    assert decision["policy_passed"] is True
    assert result.submission["status"] == "simulated"


@pytest.mark.parametrize(
    ("fixture", "rule"),
    [
        ("pharos_policy_amount_denied.jsonl", "amount_limit"),
        ("pharos_policy_allowlist_denied.jsonl", "recipient_trust"),
    ],
)
def test_policy_gatekeeper_denies_each_static_rule(fixture, rule):
    result = run(
        ROOT / "demo" / fixture,
        policy_config=load_policy(POLICY_PATH),
        evaluated_at=ALLOWED_TIME,
    )

    decision = result.prepared_action["policy_decision"]
    assert decision["outcome"] == "deny"
    assert decision["deciding_rule"] == rule
    assert checks_by_name(result)[rule]["passed"] is False
    assert result.submission["status"] == "blocked_by_policy"
    assert result.submission["receipt"]["execution_attempted"] is False


def test_policy_gatekeeper_denies_time_window():
    result = run(
        ROOT / "demo" / "pharos_policy_time_denied.jsonl",
        policy_config=load_policy(POLICY_PATH),
        evaluated_at=DENIED_TIME,
    )

    decision = result.prepared_action["policy_decision"]
    assert decision["outcome"] == "deny"
    assert decision["deciding_rule"] == "time_window"
    assert checks_by_name(result)["time_window"]["passed"] is False
    assert result.submission["status"] == "blocked_by_policy"


@pytest.mark.parametrize(
    ("evaluated_at", "outcome"),
    [
        (datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc), "allow"),
        (datetime(2026, 10, 20, 16, 59, tzinfo=timezone.utc), "allow"),
        (datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc), "deny"),
    ],
)
def test_time_window_uses_start_inclusive_end_exclusive(evaluated_at, outcome):
    result = run(
        ROOT / "demo" / "pharos_payment_confirmed.jsonl",
        policy_config=load_policy(POLICY_PATH),
        evaluated_at=evaluated_at,
    )

    assert result.prepared_action["policy_decision"]["outcome"] == outcome


def test_invalid_policy_config_fails_closed():
    state = process_events(load_events(ROOT / "demo" / "pharos_payment_confirmed.jsonl"))
    intent = extract_voice_intent(state)
    prepared = prepare_action(state, intent, "mock")
    invalid_policy = {**prepared.policy, "allowed_time_windows": [{"days": ["TUE"]}]}

    decision = evaluate_policy(intent, prepared.mandate, invalid_policy, ALLOWED_TIME)

    assert decision["outcome"] == "deny"
    assert decision["deciding_rule"] == "policy_config"
    assert decision["blocking_reasons"] == ["policy_config"]


def test_execution_rechecks_time_window():
    state = process_events(load_events(ROOT / "demo" / "pharos_payment_confirmed.jsonl"))
    intent = extract_voice_intent(state)
    prepared = prepare_action(
        state,
        intent,
        "mock",
        load_policy(POLICY_PATH),
        ALLOWED_TIME,
    )

    result = submit_or_simulate(
        prepared,
        ConfirmationDecision("confirmed", True, "explicit approval"),
        DENIED_TIME,
    )

    assert result.status == "blocked_by_policy"
    assert result.receipt["deciding_rule"] == "time_window"
    assert result.receipt["execution_attempted"] is False


def test_invalid_amount_is_denied_without_exception():
    state = process_events(load_events(ROOT / "demo" / "pharos_payment_confirmed.jsonl"))
    intent = extract_voice_intent(state)
    intent.params["amount"] = "NaN"
    prepared = prepare_action(
        state,
        intent,
        "mock",
        load_policy(POLICY_PATH),
        ALLOWED_TIME,
    )

    assert prepared.policy_decision["outcome"] == "deny"
    assert prepared.policy_decision["deciding_rule"] == "amount_limit"


def test_web_demo_rejects_unknown_command_and_records_audit(tmp_path):
    report = evaluate_request(
        {"text": "summarize my wallet", "clock": "server", "confirmed": False},
        report_dir=tmp_path,
    )

    assert report["policy_decision"]["outcome"] == "deny"
    assert report["policy_decision"]["deciding_rule"] == "request_validation"
    assert report["submission"]["receipt"]["mock_executor_called"] is False
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_web_demo_requires_explicit_mock_confirmation(tmp_path):
    report = evaluate_request(
        {
            "text": "send 0.02 PHRS to 0x1111111111111111111111111111111111111111",
            "clock": "replay",
            "evaluated_at": "2026-10-20T10:00:00Z",
            "confirmed": False,
        },
        report_dir=tmp_path,
    )

    assert report["policy_decision"]["outcome"] == "allow"
    assert report["submission"]["status"] == "blocked_by_confirmation_gate"
    assert report["submission"]["receipt"]["execution_attempted"] is False
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_web_demo_confirmed_allow_calls_only_mock_executor(tmp_path):
    report = evaluate_request(
        {
            "text": "send 0.02 PHRS to 0x1111111111111111111111111111111111111111",
            "clock": "replay",
            "evaluated_at": "2026-10-20T10:00:00Z",
            "confirmed": True,
        },
        report_dir=tmp_path,
    )

    assert report["policy_decision"]["outcome"] == "allow"
    assert report["submission"]["status"] == "simulated"
    assert report["submission"]["receipt"]["mock_executor_called"] is True
    assert report["submission"]["receipt"]["execution_attempted"] is False
    assert report["submission"]["explorer_url"].startswith("pharos://tx/")
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_web_demo_writes_unique_records_for_identical_requests(tmp_path):
    request = {
        "text": "send 0.06 PHRS to 0x1111111111111111111111111111111111111111",
        "clock": "replay",
        "evaluated_at": "2026-10-20T10:00:00Z",
        "confirmed": True,
    }

    first = evaluate_request(request, report_dir=tmp_path)
    second = evaluate_request(request, report_dir=tmp_path)

    assert first["id"] != second["id"]
    assert len(list(tmp_path.glob("*.json"))) == 4


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "0", "1e3", "0.050001"])
def test_web_rejects_invalid_or_excessive_amounts(amount, tmp_path):
    report = evaluate_request({
        "text": f"send {amount} PHRS to 0x1111111111111111111111111111111111111111",
        "clock": "replay", "evaluated_at": "2026-10-20T10:00:00Z", "confirmed": True,
    }, report_dir=tmp_path)
    assert report["policy_decision"]["deciding_rule"] == "amount_limit"
    assert report["submission"]["receipt"]["mock_executor_called"] is False
    assert report["submission"]["tx_hash"] == ""


@pytest.mark.parametrize("time", ["2026-10-24T10:00:00Z", "2026-10-20T08:59:59Z"])
def test_weekends_and_preopening_are_denied(time, tmp_path):
    report = evaluate_request({
        "text": "send 0.02 PHRS to 0x1111111111111111111111111111111111111111",
        "clock": "replay", "evaluated_at": time, "confirmed": True,
    }, report_dir=tmp_path)
    assert report["policy_decision"]["blocking_reasons"] == ["time_window"]
    assert report["submission"]["receipt"]["mock_executor_called"] is False


@pytest.mark.parametrize(("field", "rule"), [
    ("trusted_recipients", "recipient_trust"),
    ("allowed_tokens", "token_allowlist"),
    ("allowed_time_windows", "time_window"),
])
def test_empty_policy_lists_deny_all(field, rule):
    policy = load_policy(POLICY_PATH)
    policy[field] = []
    result = run(ROOT / "demo/pharos_payment_confirmed.jsonl",
                 policy_config=policy, evaluated_at=ALLOWED_TIME)
    assert result.prepared_action["policy_decision"]["blocking_reasons"] == [rule]
    assert result.submission["receipt"]["mock_executor_called"] is False


def test_audit_storage_failure_prevents_executor_call(tmp_path):
    with patch("policy_gatekeeper_server.save_report", side_effect=OSError("disk full")):
        with patch("policy_gatekeeper_server.submit_or_simulate") as executor:
            with pytest.raises(AuditPersistenceError) as error:
                evaluate_request({
                    "text": "send 0.02 PHRS to 0x1111111111111111111111111111111111111111",
                    "clock": "replay", "evaluated_at": "2026-10-20T10:00:00Z", "confirmed": True,
                }, report_dir=tmp_path)
            assert error.value.report["recorded"] is False
            assert error.value.report["submission"]["receipt"]["mock_executor_called"] is False
            assert str(error.value.__cause__) == "disk full"
            executor.assert_not_called()


def test_audit_records_link_and_preserve_every_outcome(tmp_path):
    report = evaluate_request({
        "text": "send 0.02 PHRS to 0x1111111111111111111111111111111111111111",
        "clock": "replay", "evaluated_at": "2026-10-20T10:00:00Z", "confirmed": True,
    }, report_dir=tmp_path)
    initial = json.loads((tmp_path / f"{report['evaluation_record_id']}.json").read_text())
    final = json.loads((tmp_path / f"{report['id']}.json").read_text())
    assert initial["submission"]["status"] == "pending_policy_recheck"
    assert final["submission"]["status"] == "simulated"
    assert final["audit_timeline"][-1]["step"] == "policy_rechecked"


def test_replay_requires_explicit_timezone_and_is_not_allowed_in_wallet_mode():
    assert parse_evaluation_time("2026-10-20T18:00:00+08:00") == ALLOWED_TIME
    with pytest.raises(ValueError, match="UTC offset"):
        parse_evaluation_time("2026-10-20T10:00:00")
    with pytest.raises(ValueError, match="mock mode"):
        run(ROOT / "demo/pharos_payment_confirmed.jsonl", mode="wallet", evaluated_at=ALLOWED_TIME)


def test_server_mode_uses_its_clock_not_client_timestamp(tmp_path):
    report = evaluate_request({
        "text": "send 0.02 PHRS to 0x1111111111111111111111111111111111111111",
        "clock": "server", "evaluated_at": "2026-10-20T10:00:00Z", "confirmed": True,
    }, report_dir=tmp_path)
    assert report["policy_decision"]["deciding_rule"] == "request_validation"
    assert report["submission"]["receipt"]["mock_executor_called"] is False


def test_simulation_hash_is_stable_across_allowed_times():
    policy = load_policy(POLICY_PATH)
    first = run(ROOT / "demo/pharos_payment_confirmed.jsonl",
                policy_config=policy, evaluated_at=ALLOWED_TIME)
    second = run(ROOT / "demo/pharos_payment_confirmed.jsonl",
                 policy_config=policy, evaluated_at=datetime(2026, 10, 20, 11, tzinfo=timezone.utc))
    assert first.submission["tx_hash"] == second.submission["tx_hash"]
    assert first.prepared_action["policy_decision"]["evaluated_at"] != second.prepared_action["policy_decision"]["evaluated_at"]


@pytest.mark.parametrize(("attribute", "path", "value"), [
    ("transaction_preview", ["amount"], "9999"),
    ("transaction_preview", ["to"], "0x" + "9" * 40),
    ("mandate", ["scope", "max_amount"], "500"),
    ("eip712", ["typed_data", "message", "amount"], "500"),
    ("policy", ["max_single_payment"], "9999"),
    ("chain", ["chain_id"], 1),
    ("policy_decision", ["outcome"], "deny"),
    ("registry_call", ["args", "signature"], "0x1234"),
    ("proof_payload", ["action"], "contract_call"),
])
def test_prepared_changes_are_blocked_before_mock(attribute, path, value):
    state = process_events(load_events(ROOT / "demo/pharos_payment_confirmed.jsonl"))
    prepared = prepare_action(state, extract_voice_intent(state), "mock",
                              load_policy(POLICY_PATH), ALLOWED_TIME)
    target = getattr(prepared, attribute)
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = submit_or_simulate(prepared, ConfirmationDecision("confirmed", True, "mock"), ALLOWED_TIME)
    assert result.status == "blocked_by_integrity"
    assert result.receipt["mock_executor_called"] is False
    assert result.tx_hash == ""


def test_prepare_detaches_intent_from_callers_mutable_object():
    state = process_events(load_events(ROOT / "demo/pharos_payment_confirmed.jsonl"))
    intent = extract_voice_intent(state)
    prepared = prepare_action(state, intent, "mock", load_policy(POLICY_PATH), ALLOWED_TIME)
    intent.action = "check_balance"
    intent.params["amount"] = "9999"
    assert prepared.intent.action == "send_payment"
    assert prepared.intent.params["amount"] == "0.02"
    assert submit_or_simulate(prepared, ConfirmationDecision("confirmed", True, "mock"), ALLOWED_TIME).status == "simulated"


def test_changing_prepared_intent_or_mode_is_blocked():
    state = process_events(load_events(ROOT / "demo/pharos_payment_confirmed.jsonl"))
    for attribute, value in [("mode", "wallet"), ("action_id", "other-action")]:
        prepared = prepare_action(state, extract_voice_intent(state), "mock",
                                  load_policy(POLICY_PATH), ALLOWED_TIME)
        setattr(prepared, attribute, value)
        assert submit_or_simulate(prepared, ConfirmationDecision("confirmed", True, "mock")).status == "blocked_by_integrity"
    prepared = prepare_action(state, extract_voice_intent(state), "mock",
                              load_policy(POLICY_PATH), ALLOWED_TIME)
    prepared.intent.action = "check_balance"
    assert submit_or_simulate(prepared, ConfirmationDecision("confirmed", True, "mock")).status == "blocked_by_integrity"


def test_amount_units_does_not_round_large_decimals():
    assert amount_to_units("12345678901234567890.123456789123456789") == "12345678901234567890123456789123456789"
    assert amount_to_units(str(2 ** 256 - 1), 0) == str(2 ** 256 - 1)
    with pytest.raises(ValueError, match="uint256"):
        amount_to_units(str(2 ** 256), 0)
    with pytest.raises(ValueError, match="precision"):
        amount_to_units("1.0000001", 6)


@pytest.mark.parametrize("timestamp", [
    "9999-12-31T23:59:59-23:59", "0001-01-01T00:00:00+01:00",
])
def test_overflow_timestamps_get_deny_and_audit(timestamp, tmp_path):
    result = evaluate_request({
        "text": "send 0.02 PHRS to 0x" + "1" * 40, "clock": "replay",
        "evaluated_at": timestamp, "confirmed": True,
    }, report_dir=tmp_path)
    assert result["policy_decision"]["deciding_rule"] == "request_validation"
    assert result["submission"]["receipt"]["mock_executor_called"] is False
    assert result["recorded"] is True
    assert len(list(tmp_path.glob("*.json"))) == 1


@pytest.mark.parametrize("timestamp", ["", False, 0, None])
def test_server_rejects_even_falsy_supplied_time(timestamp, tmp_path):
    result = evaluate_request({
        "text": "send 0.02 PHRS to 0x" + "1" * 40,
        "clock": "server", "evaluated_at": timestamp,
    }, report_dir=tmp_path)
    assert result["policy_decision"]["deciding_rule"] == "request_validation"


def test_executor_exception_has_terminal_audit(tmp_path):
    with patch("policy_gatekeeper_server.submit_or_simulate", side_effect=RuntimeError("unexpected")):
        result = evaluate_request({
            "text": "send 0.02 PHRS to 0x" + "1" * 40, "clock": "replay",
            "evaluated_at": ALLOWED_TIME.isoformat(), "confirmed": True,
        }, report_dir=tmp_path)
    assert result["submission"]["status"] == "executor_error"
    assert result["submission"]["receipt"]["mock_executor_called"] is None
    assert result["audit_timeline"][-1]["step"] == "executor_error"
    saved = json.loads((tmp_path / f"{result['id']}.json").read_text())
    assert saved == result


def test_second_save_failure_preserves_actual_mock_outcome(tmp_path):
    calls = 0

    def save_then_fail(report, directory):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full after mock")
        save_report(report, directory)

    with patch("policy_gatekeeper_server.save_report", side_effect=save_then_fail):
        with pytest.raises(AuditPersistenceError) as error:
            evaluate_request({
                "text": "send 0.02 PHRS to 0x" + "1" * 40, "clock": "replay",
                "evaluated_at": ALLOWED_TIME.isoformat(), "confirmed": True,
            }, report_dir=tmp_path)
    result = error.value.report
    assert result["recorded"] is False
    assert result["submission"]["status"] == "simulated"
    assert result["submission"]["receipt"]["mock_executor_called"] is True
    assert (tmp_path / f"{result['evaluation_record_id']}.json").exists()
    assert not (tmp_path / f"{result['id']}.json").exists()


def test_audit_atomic_write_does_not_publish_partial_or_overwrite(tmp_path):
    report = {"id": "a" * 32, "value": "original"}
    save_report(report, tmp_path)
    with pytest.raises(FileExistsError):
        save_report({**report, "value": "replacement"}, tmp_path)
    assert json.loads((tmp_path / f"{report['id']}.json").read_text()) == report
    with patch("policy_gatekeeper_server.json.dump", side_effect=ValueError("serialize failed")):
        with pytest.raises(ValueError):
            save_report({"id": "b" * 32}, tmp_path)
    assert len(list(tmp_path.iterdir())) == 1


def test_policy_rejects_overflowing_json_number(tmp_path):
    raw = POLICY_PATH.read_text().replace(
        '"challenge_word_count": 3', '"challenge_word_count": 1e400',
    )
    path = tmp_path / "policy.json"
    path.write_text(raw)
    with pytest.raises(ValueError, match="non-finite"):
        load_policy(path)
