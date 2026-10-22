"""RPC protocol checks using a loopback fixture; no public network."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from payment_preview import RpcReader


@pytest.fixture
def rpc():
    state = {"calls": [], "override": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"].append(request)
            result = {"eth_chainId": "0xa8231", "eth_blockNumber": "0x20",
                      "eth_getBalance": "0xde0b6b3a7640000", "eth_estimateGas": "0x5208",
                      "eth_gasPrice": "0x1"}[request["method"]]
            payload = {"jsonrpc": "2.0", "id": request["id"], "result": result}
            if state["override"]:
                payload = state["override"](request, payload)
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield RpcReader(f"http://127.0.0.1:{server.server_port}"), state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_snapshot_uses_only_read_methods_and_a_pinned_balance_block(rpc):
    reader, state = rpc
    result = reader.snapshot("0x" + "3" * 40, "0x" + "1" * 40, 2 * 10 ** 16)
    assert result["chain_id"] == 688689
    assert result["fee_units"] == 21000
    assert result["balance_units"] == 10 ** 18
    assert [x["method"] for x in state["calls"]] == [
        "eth_chainId", "eth_blockNumber", "eth_getBalance", "eth_estimateGas", "eth_gasPrice",
    ]
    assert state["calls"][2]["params"][1] == "0x20"


@pytest.mark.parametrize("result", ["-1", "0x-1", "0x00", "0x", 100, None, "0x" + "f" * 65])
def test_bad_quantities_are_rejected(rpc, result):
    reader, state = rpc
    state["override"] = lambda req, payload: {**payload, "result": result}
    with pytest.raises(ValueError, match="quantity"):
        reader.quantity("eth_chainId", [])


@pytest.mark.parametrize("override", [
    lambda req, payload: {**payload, "id": True},
    lambda req, payload: {**payload, "id": 500},
    lambda req, payload: {**payload, "jsonrpc": "1.0"},
    lambda req, payload: {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -1}},
    lambda req, payload: b" " * 65537,
])
def test_malformed_envelopes_are_rejected(rpc, override):
    reader, state = rpc
    state["override"] = override
    with pytest.raises(ValueError):
        reader.call("eth_chainId", [])


def test_fee_failure_keeps_actual_balance_but_no_fee(rpc):
    reader, state = rpc
    state["override"] = lambda req, payload: (
        {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -1}}
        if req["method"] == "eth_estimateGas" else payload)
    result = reader.snapshot("0x" + "3" * 40, "0x" + "1" * 40, 2 * 10 ** 16)
    assert result["balance_units"] == 10 ** 18
    assert result["fee_units"] is None


def test_write_rpc_is_impossible_through_reader(rpc):
    reader, state = rpc
    with pytest.raises(ValueError, match="read-only"):
        reader.call("eth_sendRawTransaction", ["0xdead"])
    assert state["calls"] == []
