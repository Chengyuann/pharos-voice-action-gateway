import json
import pytest
from payment_skill_mcp import handle


def call(method, params=None):
    return handle({"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}})


def test_protocol_and_capabilities():
    assert call("initialize")["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert {tool["name"] for tool in call("tools/list")["result"]["tools"]} == {
        "parse_payment", "plan_batch", "review_payment",
    }
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_parser_tool_asks_before_guessing():
    result = call("tools/call", {"name": "parse_payment", "arguments": {
        "text": "I would like to send one or two USDC to alice.",
    }})
    payload = json.loads(result["result"]["content"][0]["text"])
    assert payload["status"] == "needs_details"


@pytest.mark.parametrize("name", ["eth_sendTransaction", "confirm_payment", "sign", "execute", "delete_audit"])
def test_no_state_changing_tool(name):
    assert "error" in call("tools/call", {"name": name, "arguments": {}})


def test_unexpected_tool_arguments_rejected():
    assert "error" in call("tools/call", {"name": "parse_payment",
                                          "arguments": {"text": "Send", "private_key": "no"}})
