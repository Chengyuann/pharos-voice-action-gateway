from copy import deepcopy
import json
from pathlib import Path
import pytest
from payment_chain import SKILL_COMMIT
from pharos_voice_action_gateway import sha256_json
from skill_chain_flow import combine, verify_flow, OFFICIAL_SOURCE, RPC

ROOT = Path(__file__).resolve().parents[1]


def sample():
    proof = json.loads((ROOT / "site/proofs/pros-allow-live.json").read_text())
    skill = {"official": {"source": OFFICIAL_SOURCE, "reviewed_commit": SKILL_COMMIT},
             "local": {"name": "pharos-payment-review"}}
    result = {"command": ["cast", "balance", proof["input"]["sender"], "--block",
                          str(proof["state"]["block_number"]), "--rpc-url", RPC],
              "version": "fixture", "stdout": str(proof["state"]["native_balance"]),
              "exit_code": 0, "balance_wei": proof["state"]["native_balance"]}
    return {"proof": proof, "request_text": "Pay alice 0.001 PROS"}, skill, result


def test_same_flow_replays_and_never_signs():
    flow = combine(*sample())
    assert verify_flow(flow)["decision"] == "allow_review"
    assert flow["signed"] is flow["broadcast"] is False
    assert flow["unsigned_candidate"] is not None


def test_disagreement_denies_and_removes_candidate():
    review, skill, result = sample()
    result.update(balance_wei=0, stdout="0")
    flow = combine(review, skill, result)
    assert verify_flow(flow)["decision"] == "deny"
    assert flow["unsigned_candidate"] is None


@pytest.mark.parametrize("mutate", [
    lambda f: f.update(signed=True),
    lambda f: f["steps"][1]["command"].__setitem__(1, "send"),
    lambda f: f["steps"][1].update(stdout="1"),
    lambda f: f["steps"][2].update(decision="deny"),
])
def test_recomputed_digest_does_not_hide_invalid_flow(mutate):
    flow = combine(*sample())
    mutate(flow)
    flow.pop("digest")
    flow["digest"] = "0x" + sha256_json(flow)
    with pytest.raises(ValueError):
        verify_flow(flow)


def test_missing_mcp_never_becomes_rpc_fallback():
    _, skill, result = sample()
    with pytest.raises(ValueError):
        combine({"message": "MCP offline"}, skill, result)
