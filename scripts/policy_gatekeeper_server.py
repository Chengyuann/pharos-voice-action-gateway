#!/usr/bin/env python3
"""Local Payment Preview workspace and Policy Gatekeeper inspector."""

from __future__ import annotations

import json
import argparse
import re
import hashlib
import os
import socket
import sqlite3
import tempfile
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from payment_actions import ActionStore, ActionConflict, ActionAccessDenied
from payment_chain import (
    MAINNET_POLICY, mainnet_sender, load_mainnet_registry, make_mainnet_review, mainnet_draft,
)
from pharos_voice_action_gateway import (
    ADDRESS_RE,
    amount_to_units,
    build_voice_mandate,
    ConfirmationDecision,
    VoiceIntent,
    decide_confirmation,
    extract_voice_intent,
    load_policy,
    parse_evaluation_time,
    policy_result,
    prepare_action,
    sha256_json,
    strict_json_loads,
    submit_or_simulate,
)
from duplex_voice_gateway import GatewayState, load_events, process_events
from payment_preview import (
    create_transaction_draft,
    load_demo_wallet,
    load_token_registry,
    parse_payment_request,
    review_payment,
    rpc_from_environment,
    token_map,
    review_fingerprint,
    wallet_for_mode,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "policy_gatekeeper.json"
REPORT_DIR = ROOT / "reports" / "policy_gatekeeper"
PAYMENT_REPORT_DIR = ROOT / "reports" / "payment_preview"
MAX_BODY_BYTES = 8192
PAYMENT_RE = re.compile(
    r"(?:send|pay|transfer)\s+(?P<amount>\S{1,100})\s+"
    r"(?P<token>[A-Za-z][A-Za-z0-9]{0,11})\s+to\s+(?P<to>\S{1,100})",
    re.IGNORECASE,
)
VOICE_DEMOS = {
    "payment": ("Voice payment", ROOT / "demo" / "pharos_payment_confirmed.jsonl"),
    "barge-in": ("Barge-in correction", ROOT / "demo" / "duplex_conversation.jsonl"),
    "short-pause": ("Short pause", ROOT / "demo" / "short_pause_continuation.jsonl"),
}


def voice_demo_payload(scenario: str) -> dict[str, Any]:
    try:
        label, path = VOICE_DEMOS[scenario]
    except KeyError as exc:
        raise ValueError("unknown voice demo scenario") from exc
    input_events = load_events(path)
    state = process_events(input_events)
    intent = extract_voice_intent(state)
    confirmation = decide_confirmation(state, intent)
    payment_command = next(
        (turn for turn in state.committed_turns if re.match(r"^(?:send|pay|transfer)\b", turn, re.I)),
        "",
    )
    # Read-only inspection of the existing event processor. No RPC, execution,
    # transaction simulation, persistence, microphone or audio playback.
    voice_material = {
        "committed_turns": state.committed_turns,
        "interrupts": state.interrupts,
        "events": [asdict(event) for event in state.output_events],
    }
    voice_hash = sha256_json(voice_material)
    evidence = {
        "voice_hash": "0x" + voice_hash,
        "intent_hash": intent.intent_hash,
        "mandate_hash": "",
        "raw_audio_stored": False,
        "scope": "fixture_only_not_payment_draft",
    }
    if scenario == "payment":
        mandate = build_voice_mandate(state, intent, voice_hash)
        evidence.update({
            "mandate_hash": mandate["mandate_hash"],
            "legacy_chain_id": mandate["scope"]["chain_id"],
        })
    return {
        "scenario": scenario,
        "label": label,
        "source": str(path.relative_to(ROOT)),
        "source_type": "local_jsonl_fixture_replay",
        "microphone_capture": False,
        "audio_playback": False,
        "raw_audio_stored": False,
        "input_events": [asdict(event) for event in input_events],
        "output_events": [asdict(event) for event in state.output_events],
        "committed_turns": state.committed_turns,
        "interrupts": state.interrupts,
        "intent": asdict(intent),
        "confirmation": asdict(confirmation),
        "payment_command": payment_command,
        "confirmation_reusable": False,
        "execution_attempted": False,
        "recorded": False,
        "evidence": evidence,
        "boundary": "Fixture replay only. The browser microphone and speech model adapters are not connected.",
    }


def save_report(report: dict, report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    # Publish only a complete file, without replacing a prior audit record.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=report_dir,
                                     prefix=".audit-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
            os.link(temporary, report_dir / f"{report['id']}.json")
        finally:
            temporary.unlink(missing_ok=True)


class AuditPersistenceError(OSError):
    def __init__(self, report: dict):
        super().__init__("audit persistence failed")
        self.report = report


class PaymentNetworkError(RuntimeError):
    """A read-only network failure, not a request or storage failure."""


def persist_report(report: dict, report_dir: Path) -> None:
    report["recorded"] = True
    try:
        save_report(report, report_dir)
    except (OSError, TypeError, ValueError) as exc:
        report["recorded"] = False
        raise AuditPersistenceError(report) from exc


def evaluate_request(payload: dict, report_dir: Path = REPORT_DIR,
                     policy_path: Path = POLICY_PATH, raw_body_sha256: str = "") -> dict:
    received_at = datetime.now(timezone.utc)
    report = {
        "id": uuid4().hex,
        "received_at": received_at.isoformat(),
        "mode": "mock",
        "clock": "server",
        "input_source": "typed command, not captured audio",
        "raw_body_sha256": raw_body_sha256,
        "submission": {
            "status": "blocked_by_policy",
            "tx_hash": "",
            "receipt": {"execution_attempted": False, "mock_executor_called": False},
        },
    }
    rule = "request_validation"
    try:
        if not isinstance(payload, dict) or set(payload) - {"text", "clock", "evaluated_at", "confirmed"}:
            raise ValueError("expected a request object with text, clock, evaluated_at and confirmed only")
        text = payload.get("text")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
            raise ValueError("command must contain 1-500 characters")
        report["request_hash"] = "0x" + sha256_json(payload)
        if type(payload.get("confirmed", False)) is not bool:
            raise ValueError("confirmed must be a boolean")
        clock = payload.get("clock", "server")
        if clock not in {"server", "replay"}:
            raise ValueError("clock must be server or replay")
        report["clock"] = clock
        if clock == "server" and "evaluated_at" in payload:
            raise ValueError("server clock does not accept a supplied time")
        if clock == "replay":
            if not isinstance(payload.get("evaluated_at"), str) or not payload["evaluated_at"]:
                raise ValueError("replay requires an ISO-8601 timestamp with offset")
            evaluated_at = parse_evaluation_time(payload["evaluated_at"])
        else:
            evaluated_at = None
        match = PAYMENT_RE.fullmatch(text.strip())
        if not match:
            raise ValueError("expected one command: send <amount> <token> to <EVM address>")
        params = match.groupdict()
        params["token"] = params["token"].upper()
        intent = VoiceIntent(
            action="send_payment", confidence=1.0,
            summary="Structured payment command for policy evaluation.",
            raw_text="", params=params, risk="high", requires_confirmation=True,
            intent_hash="0x" + sha256_json(params),
        )
        rule = "policy_config"
        policy = load_policy(policy_path)
        report["policy"] = policy
        prepared = prepare_action(GatewayState(), intent, "mock", policy, evaluated_at)
        confirmed = payload.get("confirmed", False)
        confirmation = ConfirmationDecision(
            "confirmed" if confirmed else "pending_confirmation", confirmed,
            "explicit mock approval" if confirmed else "mock execution was not approved",
        )
        prepared.audit_timeline = [
            {"step": "typed_command_parsed", "result": intent.action},
            {"step": "policy_evaluated", "result": prepared.policy_decision["outcome"]},
            {"step": "confirmation_checked", "result": confirmation.status},
        ]
        report.update({
            "intent": asdict(intent),
            "confirmation": asdict(confirmation),
            "transaction_preview": prepared.transaction_preview,
            "policy_decision": prepared.policy_decision,
            "audit_timeline": prepared.audit_timeline,
        })
    except (ValueError, TypeError, OSError) as exc:
        details = "policy file could not be read" if isinstance(exc, OSError) else str(exc)
        report["policy_decision"] = policy_result(
            [{"name": rule, "passed": False, "details": details}], {}, received_at,
        )
        persist_report(report, report_dir)
        return report

    # Persist intent and decision before the mock executor can be reached.
    report["submission"]["status"] = "pending_policy_recheck"
    persist_report(report, report_dir)
    try:
        submission = submit_or_simulate(prepared, confirmation, evaluated_at)
        report["submission"] = asdict(submission)
        report["policy_decision"] = prepared.policy_decision
    except Exception:
        # A failed executor may have started: don't claim it definitely did not.
        report["submission"] = {
            "status": "executor_error", "tx_hash": "",
            "receipt": {"execution_attempted": False, "mock_executor_called": None},
        }
        report["audit_timeline"].append({"step": "executor_error", "result": "unknown mock outcome"})
    report["evaluation_record_id"] = report["id"]
    report["id"] = uuid4().hex
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    persist_report(report, report_dir)
    return report


def read_report(report_dir: Path, record_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{32}", record_id):
        raise ValueError("invalid record id")
    try:
        raw = (report_dir / f"{record_id}.json").read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError("payment review was not found") from exc
    value = strict_json_loads(raw)
    if not isinstance(value, dict):
        raise ValueError("record is invalid")
    return value


class Handler(BaseHTTPRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def valid_request_origin(self) -> bool:
        hosts = self.headers.get_all("Host", [])
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origins = self.headers.get_all("Origin", [])
        if len(hosts) != 1 or hosts[0] not in allowed:
            self.send_json({"error": "Host is not a loopback endpoint"}, status=403)
            return False
        if origins and (len(origins) != 1 or origins[0] != f"http://{hosts[0]}"):
            self.send_json({"error": "cross-origin requests are not allowed"}, status=403)
            return False
        return True

    def do_GET(self) -> None:
        if not self.valid_request_origin():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_response(302)
            self.send_header("Location", "/payment-preview")
            self.end_headers()
            return
        if parsed.path == "/policy-gatekeeper":
            self.send_file(ROOT / "web" / "policy-gatekeeper.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/payment-preview":
            self.send_file(ROOT / "web" / "payment-preview.html", "text/html; charset=utf-8")
            return
        if parsed.path in {"/assets/payment-preview.css", "/assets/payment-preview.js", "/assets/voice-console.js"}:
            filename = parsed.path.rsplit("/", 1)[-1]
            content_type = "text/css" if filename.endswith(".css") else "text/javascript"
            self.send_file(ROOT / "web" / filename, content_type + "; charset=utf-8")
            return
        font_match = re.fullmatch(r"/assets/fonts/([a-z0-9-]+\.ttf)", parsed.path)
        if font_match:
            filename = font_match.group(1)
            allowed_fonts = {
                "barlow-condensed-700.ttf", "barlow-condensed-600-italic.ttf",
                "manrope-400.ttf", "manrope-700.ttf", "ibm-plex-mono-400.ttf",
            }
            path = ROOT / "web" / "fonts" / filename
            if filename not in allowed_fonts or not path.is_file():
                self.send_error(404)
                return
            self.send_file(path, "font/ttf")
            return
        if parsed.path == "/assets/logo.png":
            self.send_file(ROOT / "assets" / "buidl-logo.png", "image/png")
            return
        if parsed.path == "/api/policy":
            try:
                self.send_json(load_policy(self.server.policy_path))
            except (ValueError, OSError):
                self.send_json({"error": "policy configuration is unavailable"}, status=503)
            return
        if parsed.path.startswith("/api/audit/"):
            record_id = parsed.path.removeprefix("/api/audit/")
            if not re.fullmatch(r"[0-9a-f]{32}", record_id):
                self.send_error(404)
                return
            report_path = self.server.report_dir / f"{record_id}.json"
            try:
                data = report_path.read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="policy-gatekeeper-{record_id}.json"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/api/scenarios":
            scenarios = [
                {
                    "id": "allow",
                    "label": "Allow",
                    "text": "send 0.02 PHRS to 0x1111111111111111111111111111111111111111",
                    "evaluated_at": "2026-10-20T10:00:00Z",
                },
                {
                    "id": "amount",
                    "label": "Deny amount",
                    "text": "send 0.06 PHRS to 0x1111111111111111111111111111111111111111",
                    "evaluated_at": "2026-10-20T10:00:00Z",
                },
                {
                    "id": "recipient",
                    "label": "Deny recipient",
                    "text": "send 0.02 PHRS to 0x9999999999999999999999999999999999999999",
                    "evaluated_at": "2026-10-20T10:00:00Z",
                },
                {
                    "id": "time",
                    "label": "Deny time",
                    "text": "send 0.02 PHRS to 0x1111111111111111111111111111111111111111",
                    "evaluated_at": "2026-10-20T18:00:00Z",
                },
            ]
            self.send_json({"scenarios": scenarios})
            return
        if parsed.path == "/api/payment/config":
            try:
                wallet = load_demo_wallet()
                registry = load_token_registry()
                policy = load_policy(self.server.policy_path)
                if wallet["network"]["chain_id"] != registry["network_chain_id"]:
                    raise ValueError("network mismatch")
                live_wallet, live_available = None, False
                try:
                    live_wallet = wallet_for_mode(wallet, "live")["wallet"]
                    live_available = rpc_from_environment() is not None
                except ValueError:
                    pass
                self.send_json({
                    "wallet": wallet, "registry": registry, "policy": policy,
                    "live_available": live_available, "live_wallet": live_wallet,
                    "live_hint": "Set PHAROS_RPC_URL and PHAROS_READ_ADDRESS for live read-only checks.",
                    "mcp_registry": load_mainnet_registry(),
                    "mcp_policy": load_policy(getattr(self.server, "mainnet_policy_path", MAINNET_POLICY)),
                    "mcp_wallet": {"address": mainnet_sender(), "source": "configured_readonly_address"},
                    "mcp_hint": "Official Pacific Mainnet MCP. Set PHAROS_MCP_READ_ADDRESS; no keys.",
                })
            except (ValueError, OSError):
                self.send_json({"error": "Payment settings could not be loaded. Check the local configuration."}, status=503)
            return
        if parsed.path == "/api/voice/demo":
            scenario = parse_qs(parsed.query).get("scenario", ["payment"])
            if len(scenario) != 1:
                self.send_json({"error": "expected one voice demo scenario"}, status=400)
                return
            try:
                self.send_json(voice_demo_payload(scenario[0]))
            except (ValueError, OSError):
                self.send_json({"error": "Voice demo could not be loaded."}, status=400)
            return
        if parsed.path == "/api/payment/recent":
            try:
                self.send_json({"records": self.payment_recent_records()})
            except OSError:
                self.send_json({"error": "Saved records could not be read."}, status=503)
            return
        if parsed.path.startswith("/api/payment/audit/"):
            record_id = parsed.path.removeprefix("/api/payment/audit/")
            if not re.fullmatch(r"[0-9a-f]{32}", record_id):
                self.send_error(404)
                return
            try:
                data = (self.payment_report_dir() / f"{record_id}.json").read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="payment-preview-{record_id}.json"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if not self.valid_request_origin():
            return
        if self.path not in {
            "/api/evaluate", "/api/payment/review", "/api/payment/draft",
            "/api/payment/action", "/api/payment/action/update", "/api/payment/action/correct",
            "/api/payment/action/cancel", "/api/payment/action/confirm",
            "/api/payment/action/invalidate",
        }:
            self.send_error(404)
            return
        try:
            if self.headers.get("Transfer-Encoding"):
                self.send_json({"error": "chunked requests are unsupported"}, status=400)
                return
            if self.headers.get_content_type() != "application/json":
                self.send_json({"error": "Content-Type must be application/json"}, status=415)
                return
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0]):
                self.send_json({"error": "exactly one numeric Content-Length is required"}, status=400)
                return
            length = int(lengths[0])
            if not 0 < length <= MAX_BODY_BYTES:
                self.send_json({"error": "request must contain 1-8192 bytes"}, status=413)
                return
            self.connection.settimeout(getattr(self.server, "body_timeout", 5))
            raw = self.rfile.read(length)
            if len(raw) != length:
                self.send_json({"error": "incomplete request body"}, status=400)
                return
            payload = strict_json_loads(raw.decode("utf-8"))
            if self.path.startswith("/api/payment/action"):
                self.send_json(self.action_request(payload))
                return
            if self.path == "/api/payment/review":
                if isinstance(payload, dict) and "action_id" in payload:
                    self.send_json(self.review_action(payload))
                    return
                if not getattr(self.server, "allow_legacy_payment_api", False):
                    raise ActionAccessDenied("A payment action session is required.")
                report = self.evaluate_payment(payload)
                persist_report(report, self.payment_report_dir())
                self.send_json(report)
                return
            if self.path == "/api/payment/draft":
                if isinstance(payload, dict) and "action_id" in payload:
                    self.send_json(self.draft_action(payload))
                    return
                if not getattr(self.server, "allow_legacy_payment_api", False):
                    raise ActionAccessDenied("A payment action session is required.")
                if isinstance(payload, dict) and isinstance(payload.get("review_id"), str):
                    saved = read_report(self.payment_report_dir(), payload["review_id"])
                    if "action_id" in saved:
                        raise ActionAccessDenied("This review requires its action session.")
                report = self.create_payment_draft(payload)
                persist_report(report, self.payment_report_dir())
                self.send_json(report)
                return
            report = evaluate_request(
                payload, self.server.report_dir, self.server.policy_path,
                hashlib.sha256(raw).hexdigest(),
            )
            self.send_json(report, status=500 if report["submission"]["status"] == "executor_error" else 200)
        except ActionAccessDenied as exc:
            self.send_json({"error": str(exc)}, status=403)
        except ActionConflict as exc:
            self.send_json({"error": str(exc)}, status=409)
        except sqlite3.Error:
            self.send_json({"error": "Action storage is unavailable. No success can be reported."}, status=503)
        except AuditPersistenceError as exc:
            self.send_json({
                "error": ("The result could not be saved. No payment was sent. Check local storage before retrying."
                          if self.path.startswith("/api/payment/") else
                          "audit storage unavailable; inspect report for the actual mock outcome"),
                "report": exc.report,
            }, status=503)
        except PaymentNetworkError:
            self.send_json({
                "error": "Live network checks failed. No draft was created. Retry the payment review.",
            }, status=503)
        except socket.timeout:
            self.send_json({"error": "request body timed out"}, status=408)
        except (ValueError, UnicodeError) as exc:
            message = str(exc) if self.path.startswith("/api/payment/") else "invalid JSON request"
            self.send_json({"error": message}, status=400)
        except TypeError:
            self.send_json({"error": "Request fields have invalid types."}, status=400)
        except OSError:
            self.send_json({"error": "audit storage unavailable; no success can be reported"}, status=503)

    def send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def payment_report_dir(self) -> Path:
        return getattr(self.server, "payment_report_dir", self.server.report_dir.parent / "payment_preview")

    def action_store(self) -> ActionStore:
        return ActionStore(self.payment_report_dir() / "_actions.sqlite3")

    def action_token(self) -> str:
        values = self.headers.get_all("X-Action-Token", [])
        if len(values) != 1:
            raise ActionAccessDenied("An action session token is required.")
        return values[0]

    @staticmethod
    def action_fields(payload, fields):
        if not isinstance(payload, dict) or set(payload) != set(fields):
            raise ValueError("expected " + ", ".join(fields))
        if "revision" in payload and (type(payload["revision"]) is not int or payload["revision"] < 0):
            raise ValueError("revision must be a non-negative integer")

    def action_request(self, payload):
        store = self.action_store()
        if self.path == "/api/payment/action":
            self.action_fields(payload, [])
            return store.create()
        if self.path == "/api/payment/action/cancel":
            self.action_fields(payload, ["action_id"])
            return store.cancel(payload["action_id"], self.action_token())
        if self.path == "/api/payment/action/update":
            self.action_fields(payload, ["action_id", "revision", "input"])
            return store.update(payload["action_id"], self.action_token(), payload["revision"], payload["input"])
        if self.path == "/api/payment/action/invalidate":
            self.action_fields(payload, ["action_id", "revision"])
            return store.invalidate(payload["action_id"], self.action_token(), payload["revision"])
        if self.path == "/api/payment/action/correct":
            self.action_fields(payload, ["action_id", "revision", "text"])
            text = payload["text"]
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 100:
                raise ValueError("correction must contain 1-100 characters")
            if re.fullmatch(r"(?:stop|cancel|abort|取消|停止|暂停|先别转|先别执行)[。.!]?", text.strip(), re.I):
                return {**store.cancel(payload["action_id"], self.action_token()), "message": "Action cancelled."}
            return store.correct(payload["action_id"], self.action_token(), payload["revision"], text)
        self.action_fields(payload, ["action_id", "revision", "review_id"])

        def validate():
            saved = read_report(self.payment_report_dir(), payload["review_id"])
            if (saved.get("action_id") != payload["action_id"]
                    or saved.get("action_revision") != payload["revision"]
                    or saved.get("integrity_hash") != review_fingerprint(saved)
                    or not saved.get("recorded") or saved.get("status") != "ready"
                    or datetime.now(timezone.utc) >= parse_evaluation_time(saved["expires_at"])):
                raise ActionConflict("The review is invalid or expired. Review again.")
        return store.confirm(payload["action_id"], self.action_token(), payload["revision"], payload["review_id"], validate)

    def review_action(self, payload):
        self.action_fields(payload, ["action_id", "revision"])
        store, token = self.action_store(), self.action_token()
        action_id, revision = payload["action_id"], payload["revision"]
        current = store.begin_review(action_id, token, revision)
        try:
            report = self.evaluate_payment(current)
            report.update(action_id=action_id, action_revision=revision)
            report["integrity_hash"] = review_fingerprint(report)
            store.finish_review(action_id, token, revision, report,
                                lambda value: persist_report(value, self.payment_report_dir()))
            return report
        except Exception:
            store.fail(action_id, token, revision, "reviewing")
            raise

    def draft_action(self, payload):
        self.action_fields(payload, ["action_id", "revision", "review_id"])
        store, token = self.action_store(), self.action_token()
        action_id, revision = payload["action_id"], payload["revision"]
        cached_id = store.begin_draft(action_id, token, revision, payload["review_id"])
        if cached_id:
            cached = read_report(self.payment_report_dir(), cached_id)
            if (cached.get("integrity_hash") != review_fingerprint(cached)
                    or datetime.now(timezone.utc) >= parse_evaluation_time(cached["expires_at"])):
                raise ActionConflict("Saved draft is invalid or expired. Start a new review.")
            return cached
        try:
            report, rechecked = self.create_payment_draft(
                {"review_id": payload["review_id"]}, defer_live_recheck=True,
            )
            report.update(action_id=action_id, action_revision=revision,
                          confirmed_review_id=payload["review_id"])
            report["integrity_hash"] = review_fingerprint(report)

            def save(value):
                if rechecked is not None:
                    persist_report(rechecked, self.payment_report_dir())
                persist_report(value, self.payment_report_dir())
            store.finish_draft(action_id, token, revision, report, save)
            return report
        except Exception:
            store.fail(action_id, token, revision, "drafting")
            raise

    def payment_recent_records(self) -> list[dict[str, Any]]:
        directory = self.payment_report_dir()
        if not directory.exists():
            return []
        records = []
        for path in sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                record = strict_json_loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (not isinstance(record, dict) or record.get("record_type") not in {"payment_review", "payment_draft"}
                    or record.get("id") != path.stem or not re.fullmatch(r"[0-9a-f]{32}", path.stem)):
                continue
            payment = record.get("payment") or {}
            if not isinstance(payment, dict):
                continue
            records.append({
                "id": record.get("id"),
                "record_type": record.get("record_type"),
                "created_at": record.get("created_at"),
                "status": record.get("status"),
                "mode": record.get("mode"),
                "amount": payment.get("amount"),
                "token": payment.get("token"),
                "recipient": payment.get("recipient"),
            })
            if len(records) == 100:
                break
        return records

    def evaluate_payment(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) - {"text", "answers", "mode"}:
            raise ValueError("expected text, answers and mode")
        text = payload.get("text")
        if not isinstance(text, str):
            raise ValueError("text is required")
        answers = payload.get("answers", None)
        if answers is not None and not isinstance(answers, dict):
            raise ValueError("answers must be an object")
        if answers == {}:
            answers = None
        mode = payload.get("mode", "demo")
        if mode not in {"demo", "live", "mcp"}:
            raise ValueError("mode must be demo, live or mcp")
        if mode == "mcp":
            return make_mainnet_review(text, answers, sender=mainnet_sender(),
                                       policy_path=getattr(self.server, "mainnet_policy_path", MAINNET_POLICY))
        wallet = load_demo_wallet()
        registry = load_token_registry()
        policy = load_policy(self.server.policy_path)
        snapshot = None
        live_error = ""
        if mode == "live":
            try:
                wallet = wallet_for_mode(wallet, mode)
                reader = rpc_from_environment()
                if reader is None:
                    raise ValueError("Set PHAROS_RPC_URL and PHAROS_READ_ADDRESS to use live network data.")
                if parse_payment_request(text, answers)["status"] == "complete":
                    snapshot = self.live_snapshot_for_review(text, answers, wallet, registry, reader)
            except (KeyError, TypeError, ValueError, OSError) as exc:
                live_error = (
                    "Set PHAROS_RPC_URL and PHAROS_READ_ADDRESS to use live network data."
                    if not os.environ.get("PHAROS_RPC_URL") or not os.environ.get("PHAROS_READ_ADDRESS")
                    else "Live network checks failed. Check the RPC connection and try again. No demo data was used."
                )
                if wallet["wallet"]["source"] == "demo_wallet_snapshot":
                    wallet = deepcopy(wallet)
                    wallet["wallet"] = {"address": "", "source": "not_configured"}
        report = review_payment(text, answers, mode, wallet, registry, policy, snapshot)
        if live_error and report["status"] == "data_unavailable":
            report["message"] = live_error
        report["integrity_hash"] = review_fingerprint(report)
        report["recorded"] = False
        return report

    def live_snapshot_for_review(self, text: str, answers: dict[str, Any] | None,
                                 wallet: dict[str, Any], registry: dict[str, Any],
                                 reader: Any) -> dict[str, Any] | None:
        parsed = parse_payment_request(text, answers)
        if parsed["status"] != "complete":
            return None
        payment = parsed["payment"]
        token = token_map(registry).get(payment["token"])
        if (token is None or token["type"] != "native"
                or not ADDRESS_RE.fullmatch(payment["recipient"]) or int(payment["recipient"], 16) == 0):
            return None
        amount_units = int(amount_to_units(payment["amount"], token["decimals"]))
        if amount_units <= 0:
            return None
        return reader.snapshot(wallet["wallet"]["address"], payment["recipient"], amount_units)

    def create_payment_draft(self, payload: dict, defer_live_recheck: bool = False):
        if not isinstance(payload, dict) or set(payload) != {"review_id"}:
            raise ValueError("expected review_id")
        review_id = payload.get("review_id")
        if not isinstance(review_id, str):
            raise ValueError("review_id must be a string")
        review = read_report(self.payment_report_dir(), review_id)
        if review.get("record_type") != "payment_review":
            raise ValueError("review_id does not reference a payment review")
        if review.get("integrity_hash") != review_fingerprint(review):
            raise ValueError("payment review is stale; review the payment again")
        if review.get("status") != "ready" or not review.get("recorded"):
            raise ValueError("payment review is not ready")
        if datetime.now(timezone.utc) >= parse_evaluation_time(review["expires_at"]):
            raise ValueError("payment review expired")
        mode = review.get("mode")
        if mode == "mcp":
            fresh = make_mainnet_review(
                review["request_text"], review.get("answers"), sender=mainnet_sender(),
                policy_path=getattr(self.server, "mainnet_policy_path", MAINNET_POLICY),
            )
            if fresh["status"] == "data_unavailable":
                raise PaymentNetworkError()
            if fresh["status"] != "ready":
                raise ActionConflict("Current Chain MCP checks no longer pass. Review again.")
            for key in ("configuration_hash", "payment", "prepared_transaction", "fee", "balance"):
                if fresh.get(key) != review.get(key):
                    raise ActionConflict("Payment, account, nonce, fees, balance or policy changed. Review again.")
            fresh["previous_review_id"] = review["id"]
            for key in ("action_id", "action_revision"):
                if key in review:
                    fresh[key] = review[key]
            fresh["integrity_hash"] = review_fingerprint(fresh)
            draft = mainnet_draft(fresh)
            draft["original_review_id"] = review["id"]
            draft["integrity_hash"] = review_fingerprint(draft)
            if not defer_live_recheck:
                persist_report(fresh, self.payment_report_dir())
            return (draft, fresh) if defer_live_recheck else draft
        wallet = wallet_for_mode(load_demo_wallet(), mode)
        registry = load_token_registry()
        policy = load_policy(self.server.policy_path)
        if mode == "demo":
            fresh = review_payment(review["request_text"], review.get("answers"), "demo", wallet, registry, policy)
            comparable = dict(review)
            fresh_comparable = dict(fresh)
            for value in (comparable, fresh_comparable):
                for key in ("id", "created_at", "expires_at", "recorded", "integrity_hash",
                            "action_id", "action_revision"):
                    value.pop(key, None)
            if comparable != fresh_comparable or review.get("integrity_hash") != review_fingerprint(review):
                raise ValueError("payment review is stale; review the payment again")
            source = review
        elif mode == "live":
            try:
                reader = rpc_from_environment()
                if reader is None:
                    raise ValueError("live network is not configured")
                snapshot = self.live_snapshot_for_review(
                    review["request_text"], review.get("answers"), wallet, registry, reader,
                )
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise PaymentNetworkError() from exc
            fresh = review_payment(
                review["request_text"], review.get("answers"), "live",
                wallet, registry, policy, snapshot,
            )
            if fresh["status"] != "ready":
                raise ValueError("live checks changed; review the payment again")
            if (fresh["configuration_hash"] != review["configuration_hash"]
                    or fresh["payment"] != review["payment"]
                    or fresh["fee"] != review["fee"]
                    or fresh["balance"] != review["balance"]):
                raise ValueError("Balance, fees or settings changed. Review the payment again.")
            fresh["previous_review_id"] = review["id"]
            if "action_id" in review:
                fresh.update(action_id=review["action_id"], action_revision=review["action_revision"])
            fresh["integrity_hash"] = review_fingerprint(fresh)
            if not defer_live_recheck:
                persist_report(fresh, self.payment_report_dir())
            source = fresh
        else:
            raise ValueError("payment review mode is invalid")
        draft = create_transaction_draft(source)
        draft["original_review_id"] = review["id"]
        draft["integrity_hash"] = review_fingerprint(draft)
        draft["recorded"] = False
        return (draft, fresh if mode == "live" else None) if defer_live_recheck else draft

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self'; "
                         "frame-ancestors 'none'; form-action 'none'; base-uri 'none'")
        super().end_headers()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", choices=["127.0.0.1", "localhost"], default="127.0.0.1")
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--reports", type=Path, default=REPORT_DIR)
    args = parser.parse_args()
    load_policy(args.policy)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.report_dir, server.policy_path = args.reports, args.policy
    server.payment_report_dir = args.reports.parent / "payment_preview"
    server.allow_legacy_payment_api = False
    print(f"Policy Gatekeeper demo: http://{args.host}:{server.server_port}/policy-gatekeeper", flush=True)
    print(f"Payment Preview demo: http://{args.host}:{server.server_port}/payment-preview", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
