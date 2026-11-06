#!/usr/bin/env python3
"""Small stdio MCP interface to the read-only payment Skill. No signer or action tokens."""
from __future__ import annotations

import json
import sys
from payment_language import parse_mainnet_request
from payment_chain import make_mainnet_review
from payment_batch import parse_batch

PROTOCOL = "2025-03-26"
TOOLS = [
    {"name": "parse_payment", "description": "Parse one exact payment or ask a follow-up. Does not read the chain.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"},
                     "answers": {"type": "object"}}, "required": ["text"], "additionalProperties": False}},
    {"name": "plan_batch", "description": "Plan up to five independent payments. No chain reads or export.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"},
                     "answers": {"type": "object"}}, "required": ["text"], "additionalProperties": False}},
    {"name": "review_payment", "description": "Read official Chain MCP and apply policy to a public address. Review evidence only; cannot sign, send or confirm.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"},
                     "sender": {"type": "string"}, "answers": {"type": "object"}},
                     "required": ["text", "sender"], "additionalProperties": False}},
]


def handle(request):
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    if "id" not in request:
        return None
    identity = request["id"]
    if type(identity) not in (int, str):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request id"}}
    method, params = request.get("method"), request.get("params", {})
    try:
        if method == "initialize":
            result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "pharos-payment-review", "version": "0.3.0"},
                      "instructions": "Read-only. Follow-ups and unsigned evidence never authorize signing or payment."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            if not isinstance(params, dict) or set(params) - {"name", "arguments"}:
                raise ValueError("Invalid tool call")
            name, args = params.get("name"), params.get("arguments", {})
            spec = next((tool for tool in TOOLS if tool["name"] == name), None)
            if spec is None or not isinstance(args, dict):
                raise ValueError("Unknown read-only tool")
            schema = spec["inputSchema"]
            if set(args) - set(schema["properties"]) or not set(schema["required"]).issubset(args):
                raise ValueError("Unexpected or missing tool arguments")
            if name == "parse_payment":
                value = parse_mainnet_request(args["text"], args.get("answers"))
            elif name == "plan_batch":
                value = parse_batch(args["text"], args.get("answers"))
            else:
                value = make_mainnet_review(args["text"], args.get("answers"), sender=args["sender"])
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                      "isError": False}
        else:
            return {"jsonrpc": "2.0", "id": identity, "error": {"code": -32601, "message": "Method not found"}}
        return {"jsonrpc": "2.0", "id": identity, "result": result}
    except (ValueError, TypeError, KeyError, OSError):
        return {"jsonrpc": "2.0", "id": identity, "error": {"code": -32602, "message": "Invalid tool arguments or unavailable local configuration"}}


def main():
    while True:
        line = sys.stdin.buffer.readline(8193)
        if not line:
            break
        if len(line) > 8192:
            return 1
        try:
            response = handle(json.loads(line))
        except (ValueError, UnicodeDecodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON"}}
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
