"""Secondary pinned RPC evidence. Official Chain MCP remains mandatory and primary."""
from __future__ import annotations

from copy import deepcopy
import json
import re
import time
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

from pharos_voice_action_gateway import strict_json_loads


class RejectRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        raise ValueError("Secondary RPC redirects are not followed")


class SecondaryRpc:
    METHODS = {"eth_chainId", "eth_getBlockByNumber", "eth_getBalance", "eth_getCode", "eth_call"}

    def __init__(self, url: str):
        parsed = urlparse(url)
        if (parsed.username or parsed.password or parsed.fragment or not parsed.hostname
                or not (parsed.scheme == "https" or parsed.scheme == "http"
                        and parsed.hostname in {"127.0.0.1", "localhost", "::1"})):
            raise ValueError("Secondary RPC requires HTTPS, or loopback HTTP, without embedded credentials")
        self.url, self.deadline, self.counter = url, time.monotonic() + 25, 0
        self.opener = build_opener(RejectRedirect())

    def call(self, method, params):
        if method not in self.METHODS or not isinstance(params, list):
            raise ValueError("Only approved read-only secondary RPC methods are allowed")
        self.counter += 1
        remaining = self.deadline - time.monotonic()
        if self.counter > 20 or remaining <= 0:
            raise ValueError("Secondary RPC request budget exceeded")
        body = json.dumps({"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params}).encode()
        with self.opener.open(Request(self.url, data=body, headers={"Content-Type": "application/json"}),
                              timeout=min(8, remaining)) as response:
            raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("Secondary response is too large")
        envelope = strict_json_loads(raw.decode())
        if (not isinstance(envelope, dict) or envelope.get("jsonrpc") != "2.0"
                or type(envelope.get("id")) is not int or envelope["id"] != self.counter
                or "error" in envelope or "result" not in envelope):
            raise ValueError("Invalid secondary RPC response")
        return envelope["result"]


def _quantity(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]{0,63})", value):
        raise ValueError("Secondary RPC quantity is invalid")
    return int(value, 16)


def _data(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", value):
        raise ValueError("Secondary RPC data is invalid")
    return value.lower()


def crosscheck(proof, reader):
    # The mandatory primary proof must replay before any secondary network I/O.
    from payment_proof import verify_proof
    verify_proof(proof)
    result = {"status": "unavailable", "role": "secondary_only_no_fallback",
              "block": proof["state"]["block_number"], "checks": [],
              "boundary": "Independent request path, not proof that provider infrastructure is independent."}
    try:
        chain = _quantity(reader.call("eth_chainId", []))
        result["checks"].append({"name": "chain_id", "matches": chain == 1672})
        if chain != 1672:
            result["status"] = "mismatch"
            return result
        block = reader.call("eth_getBlockByNumber", [hex(result["block"]), False])
        matches = (isinstance(block, dict) and _quantity(block.get("number")) == result["block"]
                   and _data(block.get("hash")) == proof["state"]["block_hash"].lower()
                   and _quantity(block.get("timestamp")) == proof["state"]["block_timestamp"])
        result["checks"].append({"name": "snapshot_block", "matches": matches})
        if not matches:
            result["status"] = "mismatch"
            return result
        for entry in proof["trace"]:
            tool, args = entry["tool"], entry["arguments"]
            if "error" in entry or tool not in {"get_balance", "get_code", "eth_call"}:
                continue
            if args.get("block") != hex(result["block"]):
                raise ValueError("Secondary reads require the primary pinned block")
            expected = entry["result"]
            if tool == "get_balance":
                actual = reader.call("eth_getBalance", [args["address"], args["block"]])
                matches = _quantity(actual) == int(expected["balanceWei"])
            elif tool == "get_code":
                actual = reader.call("eth_getCode", [args["address"], args["block"]])
                matches = _data(actual) == _data(expected["code"])
            else:
                actual = reader.call("eth_call", [deepcopy(args["transaction"]), args["block"]])
                matches = _data(actual) == _data(expected["result"])
            result["checks"].append({"name": tool, "matches": matches})
        result["status"] = "matched" if all(c["matches"] for c in result["checks"]) else "mismatch"
    except (ValueError, OSError, KeyError, TypeError, OverflowError):
        result["status"] = "unavailable"
        result["error"] = "Secondary verification failed; no RPC fallback or candidate."
    return result
