from copy import deepcopy
import json
from pathlib import Path
import pytest
from payment_crosscheck import crosscheck, SecondaryRpc
from payment_proof import verify_proof
from test_payment_chain import FakeChain, SENDER, NOW
from payment_chain import make_mainnet_review

PROOF = json.loads((Path(__file__).resolve().parents[1]/"site/proofs/pros-allow-live.json").read_text())


class RpcFixture:
    def __init__(self, proof=PROOF, **overrides):
        self.proof, self.overrides, self.calls = proof, overrides, []

    def call(self, method, args):
        self.calls.append((method, args))
        if method in self.overrides:
            return self.overrides[method]
        proof = self.proof
        if method == "eth_chainId":
            return "0x688"
        if method == "eth_getBlockByNumber":
            return {"number": hex(proof["state"]["block_number"]), "hash": proof["state"]["block_hash"],
                    "timestamp": hex(proof["state"]["block_timestamp"])}
        tools = {"eth_getBalance": "get_balance", "eth_getCode": "get_code", "eth_call": "eth_call"}
        for item in proof["trace"]:
            if item["tool"] != tools[method] or "result" not in item:
                continue
            arguments = item["arguments"]
            expected = [arguments.get("transaction") if method == "eth_call" else arguments.get("address"), arguments["block"]]
            if args == expected:
                if method == "eth_getBalance":
                    return hex(int(item["result"]["balanceWei"]))
                return item["result"]["code" if method == "eth_getCode" else "result"]
        raise AssertionError((method, args))


def test_only_pinned_calls_and_matching_proof():
    rpc = RpcFixture()
    result = crosscheck(PROOF, rpc)
    assert result["status"] == "matched"
    assert all(c["matches"] for c in result["checks"])
    assert all(args[-1] == hex(PROOF["state"]["block_number"]) for method, args in rpc.calls
               if method in {"eth_getBalance","eth_getCode","eth_call"})


@pytest.mark.parametrize("method,value", [("eth_chainId","0x1"),("eth_getBalance","0x0"),
                                        ("eth_getCode","0x1234"),("eth_call","0x1234")])
def test_mismatch_never_counts_as_success(method, value):
    assert crosscheck(PROOF, RpcFixture(**{method:value}))["status"] == "mismatch"


def test_invalid_primary_never_calls_secondary():
    bad = deepcopy(PROOF);bad["digest"] = "wrong"
    rpc = RpcFixture()
    with pytest.raises(ValueError):
        crosscheck(bad, rpc)
    assert not rpc.calls


def test_write_method_and_unsafe_url_rejected():
    rpc = SecondaryRpc("http://127.0.0.1:1")
    with pytest.raises(ValueError):
        rpc.call("eth_sendRawTransaction", ["0x123"])
    assert rpc.counter == 0
    for url in ["http://example.com", "file:///tmp/test", "https://u:p@example.com", "https://example.com/#secret"]:
        with pytest.raises(ValueError):
            SecondaryRpc(url)


def test_unavailable_secondary_blocks_review_but_keeps_primary_evidence(monkeypatch):
    monkeypatch.setenv("PHAROS_SECONDARY_RPC_URL", "http://127.0.0.1:1")
    result = make_mainnet_review("Pay alice 0.01 PROS", sender=SENDER, client=FakeChain(), now=NOW)
    assert result["status"] == "data_unavailable"
    assert "prepared_transaction" not in result
    assert not result["draft_allowed"]
    assert verify_proof(result["proof"])["verified"]


def test_primary_failure_never_invokes_secondary(monkeypatch):
    monkeypatch.setenv("PHAROS_SECONDARY_RPC_URL", "https://unused.example")
    def forbidden(*_):
        raise AssertionError("Secondary must not replace failed official MCP")
    monkeypatch.setattr("payment_crosscheck.SecondaryRpc", forbidden)
    result = make_mainnet_review("Pay alice 0.01 PROS", sender=SENDER,
                                client=FakeChain(chain=1), now=NOW)
    assert result["status"] == "data_unavailable"
    assert "secondary_check" not in result and "proof" not in result


@pytest.fixture
def rpc_server():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    state = {"override": None, "status": 200, "calls": []}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"].append(value)
            body = {"jsonrpc":"2.0","id":value["id"],"result":"0x688"}
            if state["override"]:
                body = state["override"](body)
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(state["status"])
            if state["status"] == 307:
                self.send_header("Location", "http://127.0.0.1:1/no-redirect")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SecondaryRpc(f"http://127.0.0.1:{server.server_port}"), state
    finally:
        server.shutdown(); server.server_close(); thread.join()


@pytest.mark.parametrize("override", [
    lambda p: {**p,"id":True}, lambda p: {**p,"id":100}, lambda p: {**p,"error":{}},
    lambda p: b'{"jsonrpc":"2.0","id":1,"id":1,"result":"0x688"}',
    lambda p: b" " * 1_048_577,
])
def test_secondary_transport_rejects_invalid_responses(rpc_server, override):
    rpc, state = rpc_server
    state["override"] = override
    with pytest.raises(ValueError):
        rpc.call("eth_chainId", [])


def test_secondary_transport_no_redirect_and_bounded_calls(rpc_server):
    rpc, state = rpc_server
    assert rpc.call("eth_chainId", []) == "0x688"
    state["status"] = 307
    with pytest.raises(ValueError, match="redirect"):
        rpc.call("eth_chainId", [])
    rpc.counter = 20
    with pytest.raises(ValueError, match="budget"):
        rpc.call("eth_chainId", [])
