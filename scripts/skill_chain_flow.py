#!/usr/bin/env python3
"""Installed Pharos Skill + official Chain MCP, read-only end-to-end evidence.

The official skill is an installable Markdown skill, not a separate hosted VM.
This runner follows its balance-query command template, uses the official MCP
for the payment snapshot, and gates the local Skill decision on their agreement.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from payment_chain import make_mainnet_review, SKILL_COMMIT
from payment_proof import verify_proof
from pharos_voice_action_gateway import sha256_json

OFFICIAL_SOURCE = "https://github.com/PharosNetwork/pharos-skill-engine"
RPC = "https://rpc.pharos.xyz"


def installed_contract(upstream: Path, local: Path) -> dict:
    """Read actual installed skill assets, rejecting a different network/template."""
    paths = ["SKILL.md", "assets/networks.json", "references/query.md",
             "references/transaction.md"]
    files = {p: (upstream / p).read_bytes() for p in paths}
    source = files["SKILL.md"].decode()
    query = files["references/query.md"].decode()
    transaction = files["references/transaction.md"].decode()
    if ("name: pharos-skill-engine" not in source or "cast balance" not in query
            or "ceil(estimatedGas × 1.2)" not in transaction):
        raise ValueError("Installed official Skill contract is not supported")
    networks = json.loads(files["assets/networks.json"])["networks"]
    network = next((v for v in networks if v.get("name") == "mainnet"), {})
    if (network.get("chainId"), network.get("rpcUrl"), network.get("nativeToken")) != (1672, RPC, "PROS"):
        raise ValueError("Installed Skill mainnet config differs from the reviewed registry")
    local_bytes = (local / "SKILL.md").read_bytes()
    if b"name: pharos-payment-review" not in local_bytes:
        raise ValueError("Installed payment Skill is missing")
    return {
        "installation": "skills CLI project install, agent=codex, copy mode",
        "official": {"name": "pharos-skill-engine", "source": OFFICIAL_SOURCE,
                     "reviewed_commit": SKILL_COMMIT,
                     "files": {p: hashlib.sha256(v).hexdigest() for p, v in files.items()}},
        "local": {"name": "pharos-payment-review",
                  "skill_md_sha256": hashlib.sha256(local_bytes).hexdigest()},
        "network": network,
    }


def combine(review: dict, skill: dict, cast_result: dict) -> dict:
    proof = review.get("proof")
    if proof is None:
        raise ValueError(review.get("message") or "Official Chain MCP produced no proof")
    verify_proof(proof)
    balance = cast_result["balance_wei"]
    if type(balance) is not int or not 0 <= balance < 2**256:
        raise ValueError("Invalid official Skill balance")
    agrees = balance == proof["state"]["native_balance"]
    allowed = agrees and proof["decision"]["outcome"] == "allow"
    flow = {
        "schema": "pharos-installed-skill-flow/1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "request": review["request_text"],
        "installed_skills": skill,
        "steps": [
            {"id": "official_chain_mcp", "calls": len(proof["trace"]),
             "block": proof["state"]["block_number"], "proof": proof},
            {"id": "installed_official_skill_balance", **cast_result},
            {"id": "payment_skill_decision", "mcp_decision": proof["decision"]["outcome"],
             "same_block_balance_agrees": agrees,
             "decision": "allow_review" if allowed else "deny",
             "deciding_rule": ("balance_sources_disagree" if not agrees else proof["decision"]["deciding_rule"])},
        ],
        "unsigned_candidate": deepcopy(proof["transaction"]) if allowed else None,
        "signed": False, "broadcast": False,
        "boundaries": [
            "Agent executes installed Markdown skill instructions locally; not a hosted ai.pharos.xyz run.",
            "Chain MCP remains the primary payment data source; cast is a read-only same-block gate, not fallback.",
            "Public inspected sender is not a connected wallet or proof of ownership.",
            "Offline replay checks self-consistency, not origin authenticity or payment authorization.",
            "Human approval and a fresh wallet-side review remain required before any real signature.",
        ],
    }
    flow["digest"] = "0x" + sha256_json(flow)
    return flow


def verify_flow(flow: dict) -> dict:
    material = deepcopy(flow)
    digest = material.pop("digest", None)
    if flow.get("schema") != "pharos-installed-skill-flow/1" or digest != "0x" + sha256_json(material):
        raise ValueError("Installed skill flow digest/schema mismatch")
    if flow.get("signed") is not False or flow.get("broadcast") is not False:
        raise ValueError("Unexpected signing or broadcasting")
    skill = flow["installed_skills"]
    if (skill["official"]["source"] != OFFICIAL_SOURCE
            or skill["official"]["reviewed_commit"] != SKILL_COMMIT
            or skill["local"]["name"] != "pharos-payment-review"):
        raise ValueError("Unexpected Skill definition")
    first, second, third = flow["steps"]
    proof = first["proof"]
    verified = verify_proof(proof)
    expected = ["cast", "balance", proof["input"]["sender"], "--block",
                str(proof["state"]["block_number"]), "--rpc-url", RPC]
    if second["command"] != expected or second["exit_code"] != 0:
        raise ValueError("Official Skill command does not query the same balance and block")
    if not re.fullmatch(r"\d+", second["stdout"].strip()):
        raise ValueError("Invalid balance output")
    if int(second["stdout"].strip()) != second["balance_wei"]:
        raise ValueError("Balance differs from recorded official Skill output")
    rebuilt = combine({"proof": proof, "request_text": flow["request"]}, skill,
                      {k: v for k, v in second.items() if k != "id"})
    if (rebuilt["steps"] != flow["steps"]
            or rebuilt["unsigned_candidate"] != flow["unsigned_candidate"]):
        raise ValueError("Installed Skill decision does not replay")
    return {"verified": True, "decision": third["decision"], "block": verified["block"],
            "mcp_calls": verified["calls"], "skill_command": "cast balance",
            "scope": "self-consistency only", "signed": False, "broadcast": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--request")
    parser.add_argument("--sender")
    parser.add_argument("--installed-upstream", type=Path)
    parser.add_argument("--installed-local", type=Path)
    parser.add_argument("--cast", default="cast")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.verify:
            if args.verify.stat().st_size > 2_000_000:
                raise ValueError("Flow evidence exceeds 2 MB")
            print(json.dumps(verify_flow(json.loads(args.verify.read_text())), indent=2))
            return 0
        if not all([args.request, args.sender, args.installed_upstream, args.installed_local, args.output]):
            parser.error("Live mode requires request, sender, both installed Skill paths and output")
        contract = installed_contract(args.installed_upstream, args.installed_local)
        started = time.monotonic()
        review = make_mainnet_review(args.request, sender=args.sender)
        if not review.get("proof"):
            raise ValueError(review.get("message") or "MCP data unavailable; no RPC fallback")
        proof = review["proof"]
        command = [args.cast, "balance", args.sender, "--block", str(proof["state"]["block_number"]),
                   "--rpc-url", RPC]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20)
        if completed.returncode or len(completed.stdout) > 4096 or not completed.stdout.strip().isdigit():
            raise ValueError("Official Skill balance command failed; no unsigned candidate")
        if time.monotonic() - started > 65:
            raise ValueError("Composed flow exceeded its freshness time budget")
        version = subprocess.check_output([args.cast, "--version"], text=True, timeout=5).splitlines()[0]
        cast_result = {"command": ["cast", *command[1:]], "version": version,
                       "stdout": completed.stdout.strip(), "exit_code": completed.returncode,
                       "balance_wei": int(completed.stdout.strip())}
        flow = combine(review, contract, cast_result)
        result = verify_flow(flow)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            json.dump(flow, stream, ensure_ascii=False, indent=2)
        print(json.dumps(result, indent=2))
        print(f"Read-only installed Skill flow recorded: {args.output}")
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"FAILED: {str(exc)[:500]}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
