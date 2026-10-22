"""Loopback protocol tests: no public network, wallet, or key."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from pharos_mcp import PharosMcpClient, McpToolError, PROTOCOL_VERSION


@pytest.fixture
def mcp_server():
    state = {"calls": [], "sse": True, "override": None, "status": 200,
             "statuses": [], "retry_after": "60", "deleted": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_DELETE(self):
            state["deleted"] = True
            assert self.headers["mcp-session-id"] == "test-session"
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"].append(data)
            method = data["method"]
            if method != "initialize":
                assert self.headers["mcp-session-id"] == "test-session"
                assert self.headers["MCP-Protocol-Version"] == PROTOCOL_VERSION
                assert "test=sticky" in self.headers.get("Cookie", "")
            if method == "notifications/initialized":
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            result = ({"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                       "serverInfo": {"name": "pharos-mcp-server", "version": "fixture"}}
                      if method == "initialize" else
                      {"content": [{"type": "text", "text": '{"chainId":1672}'}]})
            body = {"jsonrpc": "2.0", "id": data["id"], "result": result}
            if state["override"]:
                body = state["override"](data, body)
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            if state["sse"]:
                raw = b": keepalive\n\n" + b"event: message\ndata: " + raw + b"\n\n"
            status = state["statuses"].pop(0) if state["statuses"] else state["status"]
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream" if state["sse"] else "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("mcp-session-id", "test-session")
            self.send_header("Set-Cookie", "test=sticky; Path=/")
            self.send_header("Retry-After", state["retry_after"])
            if state["status"] == 307:
                self.send_header("Location", "http://127.0.0.1:1/never")
            self.end_headers()
            self.wfile.write(raw)

    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_port}/mcp", state
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


@pytest.mark.parametrize("sse", [False, True])
def test_handshake_sticky_cookie_and_session_close(mcp_server, sse):
    endpoint, state = mcp_server
    state["sse"] = sse
    with PharosMcpClient(endpoint) as client:
        assert client.call("get_chain_info") == {"chainId": 1672}
        assert client.call("get_chain_info") == {"chainId": 1672}
    assert state["deleted"]
    assert [call["method"] for call in state["calls"]] == [
        "initialize", "notifications/initialized", "tools/call", "tools/call"]


@pytest.mark.parametrize("override", [
    lambda r, p: {**p, "id": True}, lambda r, p: {**p, "id": 900},
    lambda r, p: {**p, "jsonrpc": "1.0"}, lambda r, p: {**p, "error": {}},
    lambda r, p: b" " * 1_048_577,
    lambda r, p: b'{"id":1,"id":2,"jsonrpc":"2.0","result":{}}',
])
def test_malformed_protocol_rejected(mcp_server, override):
    endpoint, state = mcp_server
    state["override"] = override
    client = PharosMcpClient(endpoint)
    try:
        with pytest.raises(ValueError):
            client.call("get_chain_info")
    finally:
        client.close()


def test_tool_error_recorded_not_a_success(mcp_server):
    endpoint, state = mcp_server
    with PharosMcpClient(endpoint) as client:
        state["override"] = lambda r, p: {
            **p, "result": {"isError": True, "content": [{
                "type": "text", "text": '{"error":{"message":"execution reverted"}}',
            }]},
        }
        with pytest.raises(McpToolError, match="reverted"):
            client.call("eth_call", {"transaction": {"data": "0x"}})
        assert "error" in client.trace[0]


def test_non_object_tool_json_is_rejected(mcp_server):
    endpoint, state = mcp_server
    with PharosMcpClient(endpoint) as client:
        state["override"] = lambda r, p: {
            **p, "result": {"content": [{"type": "text", "text": "[1,2,3]"}]},
        }
        with pytest.raises(ValueError, match="object"):
            client.call("get_chain_info")


@pytest.mark.parametrize("status", [404, 429, 500, 502, 503, 504, 307])
def test_http_failure_and_redirect_fail_closed(mcp_server, status):
    endpoint, state = mcp_server
    with PharosMcpClient(endpoint) as client:
        state["status"] = status
        with pytest.raises(ValueError):
            client.call("get_chain_info")


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_transient_read_error_retries_same_request_bounded(mcp_server, monkeypatch, status):
    endpoint, state = mcp_server
    waits = []
    monkeypatch.setattr("pharos_mcp.time.sleep", waits.append)
    with PharosMcpClient(endpoint) as client:
        state["retry_after"] = "1"
        state["statuses"] = [status, 200]
        assert client.call("get_chain_info") == {"chainId": 1672}
        assert state["calls"][-1] == state["calls"][-2]
        assert waits == [1]


def test_service_unavailable_retry_is_bounded(mcp_server, monkeypatch):
    endpoint, state = mcp_server
    waits = []
    monkeypatch.setattr("pharos_mcp.time.sleep", waits.append)
    with PharosMcpClient(endpoint) as client:
        state.update(status=503, retry_after="1")
        before = len(state["calls"])
        with pytest.raises(ValueError, match="HTTP 503"):
            client.call("get_chain_info")
        assert len(state["calls"]) - before == 3
        assert waits == [1, 1]

def test_no_write_methods_or_invalid_arguments_trigger_network(mcp_server):
    endpoint, state = mcp_server
    client = PharosMcpClient(endpoint)
    with pytest.raises(ValueError):
        client.call("send_raw_transaction", {"rawTransaction": "0xdead"})
    with pytest.raises(ValueError):
        client.call("get_chain_info", [])
    assert not state["calls"]


def test_sse_ignores_notification_but_requires_one_response():
    data = (b'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/test"}\n\n'
            b'event: message\ndata: {"jsonrpc":"2.0","id":1,\n'
            b'data: "result":{}}\n\n')
    assert PharosMcpClient._decode_sse(data)["id"] == 1
    with pytest.raises(ValueError):
        PharosMcpClient._decode_sse(data + data)
