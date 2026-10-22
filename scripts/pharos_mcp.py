"""Small Pharos Chain MCP client using only the Python standard library."""

from __future__ import annotations

from http.cookiejar import CookieJar
from copy import deepcopy
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener
import json
import re
import time
from typing import Any

from pharos_voice_action_gateway import strict_json_loads


DEFAULT_MCP_URL = "https://mcp.pharos.xyz/mcp"
PROTOCOL_VERSION = "2025-03-26"
READ_TOOLS = {
    "get_chain_info", "get_block_number", "get_block", "get_balance",
    "get_transaction_count", "get_gas_price", "get_code", "eth_call",
    "estimate_gas",
}


class McpToolError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        raise ValueError("MCP redirects are not followed")


class PharosMcpClient:
    def __init__(self, url: str = DEFAULT_MCP_URL, timeout: float = 8.0):
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
            raise ValueError("MCP URL must be HTTP(S) without embedded credentials")
        self.url, self.timeout = url, timeout
        self.opener = build_opener(NoRedirect(), HTTPCookieProcessor(CookieJar()))
        self.deadline = time.monotonic() + 40
        self.session_id = ""
        self.connected = False
        self.request_id = 0
        self.server_info: dict[str, Any] = {}
        self.trace: list[dict[str, Any]] = []

    @staticmethod
    def _decode_sse(raw: bytes) -> dict[str, Any] | None:
        text = raw.decode("utf-8")
        if text.lstrip().startswith("{"):
            value = strict_json_loads(text)
        else:
            messages = []
            for event in re.split(r"\r?\n\r?\n", text):
                data = "\n".join(line[5:].lstrip() for line in event.splitlines()
                                 if line.startswith("data:"))
                if data:
                    decoded = strict_json_loads(data)
                    if isinstance(decoded, dict) and "id" in decoded:
                        messages.append(decoded)
            if len(messages) != 1:
                raise ValueError("MCP stream requires exactly one response")
            value = messages[0]
        if not isinstance(value, dict):
            raise ValueError("MCP response must be a JSON object")
        return value

    def _request(self, payload: dict[str, Any], method: str = "POST") -> dict[str, Any] | None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        if self.connected:
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        for attempt in range(3):
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Chain MCP review time budget exceeded")
            request = Request(self.url, data=body, headers=headers, method=method)
            try:
                with self.opener.open(request, timeout=min(self.timeout, remaining)) as response:
                    session = response.headers.get("mcp-session-id")
                    if session and not self.session_id:
                        self.session_id = session
                    raw = response.read(1_048_577)
                    if len(raw) > 1_048_576:
                        raise ValueError("MCP response is too large")
                    if not raw:
                        return None
                    return self._decode_sse(raw)
            except HTTPError as exc:
                if exc.code in {429, 502, 503, 504} and attempt < 2:
                    delay = exc.headers.get("Retry-After", str(2 ** attempt))
                    if not delay.isdigit() or int(delay) > 5:
                        raise ValueError("Chain MCP busy; wait before retrying") from exc
                    wait = max(int(delay), 1)
                    if time.monotonic() + wait >= self.deadline:
                        raise ValueError("Chain MCP review time budget exceeded") from exc
                    # Only allowlisted read calls and session setup reach this
                    # path. Keep the same request ID; never retry a write tool.
                    exc.close()
                    time.sleep(wait)
                    continue
                if exc.code == 404 and self.session_id:
                    raise ValueError("MCP session expired; retry the payment review") from exc
                raise ValueError(f"MCP request failed with HTTP {exc.code}") from exc
        raise ValueError("MCP request failed")

    def connect(self) -> None:
        if self.connected:
            return
        result = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "pharos-voice-action-gateway", "version": "0.3.0"},
        })
        if result.get("protocolVersion") != PROTOCOL_VERSION:
            raise ValueError("MCP protocol version mismatch")
        server = result.get("serverInfo")
        if not isinstance(server, dict) or not isinstance(server.get("name"), str):
            raise ValueError("MCP server identity is missing")
        self.server_info = server
        self.connected = True
        try:
            self._request({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.request_id += 1
        envelope = self._request({
            "jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params,
        })
        if (not isinstance(envelope, dict) or envelope.get("jsonrpc") != "2.0"
                or type(envelope.get("id")) is not int or envelope.get("id") != self.request_id):
            raise ValueError("MCP response did not match the request")
        if "error" in envelope:
            raise ValueError("MCP protocol request failed")
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise ValueError("MCP result is invalid")
        return result

    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        if tool not in READ_TOOLS:
            raise ValueError("only approved read-only Chain MCP tools are permitted")
        if arguments is not None and not isinstance(arguments, dict):
            raise ValueError("MCP arguments must be an object")
        if len(self.trace) >= 32:
            raise ValueError("Chain MCP review tool-call budget exceeded")
        self.connect()
        arguments = deepcopy(arguments or {})
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments})
        blocks = result.get("content")
        if not isinstance(blocks, list):
            raise ValueError("MCP tool response has no content")
        text = next(
            (item.get("text") for item in blocks
             if isinstance(item, dict) and item.get("type") == "text"
             and isinstance(item.get("text"), str)),
            None,
        )
        if text is None:
            raise ValueError("MCP tool response has no JSON text")
        value = strict_json_loads(text)
        if not isinstance(value, dict):
            raise ValueError("Chain MCP tool result must be an object")
        if result.get("isError"):
            error = value.get("error") if isinstance(value, dict) else None
            message = error.get("message") if isinstance(error, dict) else None
            self.trace.append({"tool": tool, "arguments": arguments, "error": value})
            raise McpToolError(message or f"{tool} failed")
        self.trace.append({"tool": tool, "arguments": arguments, "result": value})
        return value

    def close(self) -> None:
        self.connected = False
        if not self.session_id:
            return
        try:
            request = Request(
                self.url,
                headers={"Accept": "application/json, text/event-stream",
                         "MCP-Protocol-Version": PROTOCOL_VERSION,
                         "mcp-session-id": self.session_id},
                method="DELETE",
            )
            self.opener.open(request, timeout=min(self.timeout, 5)).close()
        except Exception:
            pass
        finally:
            self.session_id = ""

    def __enter__(self):
        try:
            self.connect()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *_):
        self.close()


def quantity(value: Any, label: str) -> int:
    if type(value) is int and 0 <= value < 2 ** 256:
        return value
    if isinstance(value, str) and re.fullmatch(r"(?:0x[0-9a-fA-F]{1,64}|[0-9]{1,78})", value):
        parsed = int(value, 16 if value.startswith("0x") else 10)
        if parsed < 2 ** 256:
            return parsed
    raise ValueError(f"{label} is not a valid quantity")
