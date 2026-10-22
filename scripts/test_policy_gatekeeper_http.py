"""Local HTTP acceptance checks; no network beyond a loopback test server."""

import json
import hashlib
import socket
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch

import pytest

from policy_gatekeeper_server import Handler, POLICY_PATH, save_report


@pytest.fixture
def server(tmp_path):
    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    instance.report_dir = tmp_path
    instance.policy_path = POLICY_PATH
    thread = Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


def request(server, method, path, body=None, headers=None):
    client = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        client.request(method, path, body=body, headers=headers or {})
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        client.close()


@pytest.mark.parametrize("path", ["/policy-gatekeeper", "/assets/logo.png", "/api/policy", "/api/scenarios"])
def test_read_routes(server, path):
    status, headers, body = request(server, "GET", path)
    assert status == 200
    assert len(body) == int(headers["Content-Length"])
    assert headers["Cache-Control"] == "no-store"


def test_download_matches_saved_audit(server):
    body = json.dumps({
        "text": "send 0.02 PHRS to 0x1111111111111111111111111111111111111111",
        "clock": "replay", "evaluated_at": "2026-10-20T10:00:00Z", "confirmed": True,
    })
    status, _, data = request(server, "POST", "/api/evaluate", body,
                              {"Content-Type": "application/json"})
    assert status == 200
    report = json.loads(data)
    status, headers, downloaded = request(server, "GET", f"/api/audit/{report['id']}")
    assert status == 200
    assert headers["Content-Disposition"].startswith("attachment;")
    assert json.loads(downloaded) == report
    assert report["policy_decision"]["outcome"] == "allow"


@pytest.mark.parametrize(("body", "headers", "expected"), [
    ("{", {"Content-Type": "application/json"}, 400),
    ("{}", {"Content-Type": "text/plain"}, 415),
    (" " * 8193, {"Content-Type": "application/json"}, 413),
    ("{}", {"Content-Type": "application/json", "Origin": "https://example.org"}, 403),
])
def test_bad_requests_never_create_execution_records(server, body, headers, expected):
    status, _, data = request(server, "POST", "/api/evaluate", body, headers)
    assert status == expected
    assert "error" in json.loads(data)
    assert list(server.report_dir.iterdir()) == []


@pytest.mark.parametrize("path", ["/api/audit/../../README.md", "/api/audit/" + "0" * 32, "/README.md"])
def test_unknown_or_private_paths_are_not_exposed(server, path):
    status, _, _ = request(server, "GET", path)
    assert status == 404


@pytest.mark.parametrize("raw", [
    '{"confirmed":false,"confirmed":true}',
    '{"text":{"a":1,"a":2}}',
    '{"confirmed":NaN}', '{"confirmed":Infinity}', '{"confirmed":1e400}',
    "[" * 4000 + "]" * 4000,
])
def test_strict_json_rejects_ambiguity_and_depth(server, raw):
    status, _, body = request(server, "POST", "/api/evaluate", raw,
                              {"Content-Type": "application/json"})
    assert status == 400
    assert "error" in json.loads(body)
    assert list(server.report_dir.iterdir()) == []


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_unknown_host_rejected_even_with_matching_origin(server, method):
    host = f"evil.test:{server.server_port}"
    status, _, _ = request(server, method, "/api/policy" if method == "GET" else "/api/evaluate",
                           "{}" if method == "POST" else None,
                           {"Host": host, "Origin": f"http://{host}", "Content-Type": "application/json"})
    assert status == 403
    assert list(server.report_dir.iterdir()) == []


def test_wrong_scheme_origin_and_valid_local_origin(server):
    for scheme, expected in [("https", 403), ("http", 200)]:
        status, _, _ = request(server, "GET", "/api/policy", headers={
            "Origin": f"{scheme}://127.0.0.1:{server.server_port}",
        })
        assert status == expected


def test_raw_request_digest_is_audited(server):
    raw = b'{ "text" : "send 0.02 PHRS to 0x1111111111111111111111111111111111111111", "confirmed": false }'
    status, _, body = request(server, "POST", "/api/evaluate", raw,
                              {"Content-Type": "application/json"})
    assert status == 200
    assert json.loads(body)["raw_body_sha256"] == hashlib.sha256(raw).hexdigest()


def test_body_timeout_reports_408_not_disk_error(server):
    server.body_timeout = 0.05
    with socket.create_connection(server.server_address, timeout=3) as client:
        client.sendall(
            f"POST /api/evaluate HTTP/1.0\r\nHost: 127.0.0.1:{server.server_port}\r\n"
            "Content-Type: application/json\r\nContent-Length: 50\r\n\r\n{}".encode()
        )
        chunks = []
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    response = b"".join(chunks)
    assert b"408" in response.split(b"\r\n")[0]
    assert b"request body timed out" in response


def test_duplicate_content_length_rejected(server):
    with socket.create_connection(server.server_address, timeout=3) as client:
        client.sendall(
            f"POST /api/evaluate HTTP/1.0\r\nHost: 127.0.0.1:{server.server_port}\r\n"
            "Content-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 3\r\n\r\n{}".encode()
        )
        assert b"400" in client.recv(4096).split(b"\r\n")[0]


def test_audit_write_failure_http_exposes_outcome_not_false_success(server):
    calls = 0

    def save_then_fail(report, directory):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        save_report(report, directory)

    raw = json.dumps({
        "text": "send 0.02 PHRS to 0x" + "1" * 40, "clock": "replay",
        "evaluated_at": "2026-10-20T10:00:00Z", "confirmed": True,
    })
    with patch("policy_gatekeeper_server.save_report", side_effect=save_then_fail):
        status, _, body = request(server, "POST", "/api/evaluate", raw,
                                  {"Content-Type": "application/json"})
    assert status == 503
    report = json.loads(body)["report"]
    assert report["recorded"] is False
    assert report["submission"]["receipt"]["mock_executor_called"] is True
