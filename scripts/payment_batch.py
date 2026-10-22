#!/usr/bin/env python3
"""P1 batch planning only: exact grammar, local memos and aggregate MCP proof checks.

This module never issues a transaction, nonce sequence or signing request.
It does not call the action API or circumvent its rolling issuance budget.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import unicodedata

from evm_address import validate_address
from payment_language import parse_mainnet_request
from payment_chain import load_mainnet_registry
from payment_proof import verify_proof
from pharos_voice_action_gateway import amount_to_units, strict_json_loads, sha256_json


def parse_batch(text, answers=None, contacts=None):
    """Use semicolons outside JSON-quoted memos as explicit item boundaries."""
    if not isinstance(text, str) or not 1 <= len(text) <= 2000:
        return {"status": "invalid", "message": "Batch request must contain 1-2000 characters"}
    if answers is None:
        answers = {}
    if (not isinstance(answers, dict) or any(not isinstance(key, str) or not key.isdigit() for key in answers)):
        return {"status": "invalid", "message": "Clarifications must be keyed by zero-based item index"}
    parts, start, quoted, escaped = [], 0, False, False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if quoted and char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == ";" and not quoted:
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    if quoted or escaped or not 1 <= len(parts) <= 5 or any(not part for part in parts):
        return {"status": "invalid", "message": "Use 1-5 nonempty payments separated by semicolons"}
    if set(answers) - {str(i) for i in range(len(parts))}:
        return {"status": "invalid", "message": "Clarification points to a nonexistent item"}
    payments, questions = [], []
    tokens = {token["symbol"]: token for token in load_mainnet_registry()["tokens"]}
    for index, part in enumerate(parts):
        command, marker, raw_memo = part.partition(' memo ')
        memo = ""
        if marker:
            try:
                memo = strict_json_loads(raw_memo)
                if (not isinstance(memo, str) or not 1 <= len(memo) <= 140
                        or any(unicodedata.category(c).startswith("C") for c in memo)):
                    raise ValueError("Memo must be plain printable text")
            except (ValueError, TypeError):
                return {"status": "invalid", "item": index,
                        "message": 'Use memo "plain text" (1-140 characters, no control characters)'}
        parsed = parse_mainnet_request(command, answers.get(str(index)), contacts=contacts)
        if parsed["status"] == "needs_details":
            questions.append({"item": index, **parsed})
            continue
        if parsed["status"] != "complete":
            return {"status": "invalid", "item": index, "message": parsed.get("message", "Invalid payment")}
        payment = parsed["payment"]
        token = tokens.get(payment["token"])
        try:
            if token is None:
                raise ValueError("Only Pacific Mainnet PROS, USDC and WPROS are supported")
            recipient = validate_address(payment["recipient"])
            if recipient in {t.get("address") for t in tokens.values()}:
                raise ValueError("Known token contracts cannot be recipients")
            if int(amount_to_units(payment["amount"], token["decimals"])) <= 0:
                raise ValueError("Amount must be positive")
        except ValueError as exc:
            return {"status": "invalid", "item": index, "message": str(exc)}
        payments.append({**payment, "recipient": recipient, "memo": memo})
    if questions:
        # Do not publish a partial executable plan when any row remains ambiguous.
        return {"status": "needs_details", "questions": questions, "payments": []}
    identities = [(int(amount_to_units(p["amount"], tokens[p["token"]]["decimals"])),
                   p["token"], p["recipient"]) for p in payments]
    if len(identities) != len(set(identities)):
        return {"status": "invalid", "message": "Duplicate payment rows require explicit request revision"}
    plan = {"schema": "pharos-batch-plan/1", "status": "planned", "payments": payments,
            "memo_scope": "local_only_never_calldata", "signing_ready": False,
            "boundary": "Planning only. Each payment still needs fresh review, aggregate checks and human authorization."}
    plan["digest"] = "0x" + sha256_json(plan)
    return plan


def aggregate_proofs(plan, proofs):
    """Use verified, SAME block/policy/sender MCP evidence; never mix snapshots."""
    material = deepcopy(plan)
    digest = material.pop("digest", None)
    if (material.get("schema") != "pharos-batch-plan/1" or material.get("status") != "planned"
            or digest != "0x" + sha256_json(material) or not isinstance(proofs, list)
            or not isinstance(plan.get("payments"), list)
            or len(proofs) != len(plan["payments"]) or not 1 <= len(proofs) <= 5
            or plan.get("signing_ready") is not False or plan.get("memo_scope") != "local_only_never_calldata"):
        raise ValueError("Expected an intact plan and exactly one primary MCP proof per row")
    totals, available, native_fee, reference, policy = {}, {}, 0, None, None
    times = []
    all_allowed = True
    for payment, proof in zip(plan["payments"], proofs):
        if (not isinstance(payment, dict) or set(payment) != {"amount", "token", "recipient", "memo"}
                or not isinstance(payment["memo"], str) or len(payment["memo"]) > 140
                or any(unicodedata.category(c).startswith("C") for c in payment["memo"])):
            raise ValueError("Invalid planned payment")
        verify_proof(proof)
        times.append(datetime.fromisoformat(proof["evaluated_at"]))
        expected_payment = {k: payment[k] for k in ("amount", "token", "recipient")}
        if proof["input"]["payment"] != expected_payment:
            raise ValueError("Proof does not match its payment row")
        state = proof["state"]
        snapshot = (proof["input"]["sender"], state["block_number"], state["block_hash"],
                    state["native_balance"], state["nonce"], state["gas_price"])
        if reference is not None and (snapshot != reference or proof["policy"] != policy):
            raise ValueError("Batch evidence requires one sender, block, native balance, nonce and policy")
        reference, policy = snapshot, proof["policy"]
        symbol = payment["token"]
        amount = int(amount_to_units(payment["amount"], proof["token"]["decimals"]))
        totals[symbol] = totals.get(symbol, 0) + amount
        if symbol in available and available[symbol] != state["token_balance"]:
            raise ValueError("Inconsistent token balances in one snapshot")
        available[symbol] = state["token_balance"]
        if state["gas"] is None or state["gas_price"] is None:
            all_allowed = False
        else:
            native_fee += state["gas"] * state["gas_price"]
        all_allowed = all_allowed and proof["decision"]["outcome"] == "allow"
    native_required = totals.get("PROS", 0) + native_fee
    if (max(times) - min(times)).total_seconds() > 60:
        raise ValueError("Batch proof collection times span more than 60 seconds")
    if native_required >= 2**256 or any(amount >= 2**256 for amount in totals.values()):
        raise ValueError("Aggregate quantities exceed uint256")
    checks = [{"name": "individual_policy_and_simulation", "passed": all_allowed},
              {"name": "aggregate_native_plus_fees", "passed": native_required <= reference[3]}]
    for symbol, amount in totals.items():
        checks.append({"name": f"aggregate_{symbol}_balance", "passed": amount <= available[symbol]})
    return {"schema": "pharos-batch-aggregate/1",
            "status": "candidate_plan" if all(c["passed"] for c in checks) else "blocked",
            "totals_base_units": {k: str(v) for k, v in totals.items()},
            "native_fee_budget_wei": str(native_fee), "checks": checks,
            "block": reference[1], "signing_ready": False, "signed": False, "broadcast": False,
            "boundary": "Offline planning evidence only. No nonce allocation or sequential-state simulation, "
                        "no reservation against rolling budget and no multi-payment signing/export endpoint."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--proof", type=Path, action="append", default=[])
    args = parser.parse_args()
    try:
        plan = parse_batch(args.request)
        if args.proof:
            values = []
            for path in args.proof:
                if path.stat().st_size > 2_000_000:
                    raise ValueError("Proof exceeds 2 MB")
                values.append(strict_json_loads(path.read_text()))
            result = {"plan": plan, "aggregate": aggregate_proofs(plan, values)}
        else:
            result = plan
        with args.output.open("x") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print(json.dumps({"status": result.get("status", result.get("aggregate", {}).get("status")),
                          "signing_ready": False, "output": str(args.output)}))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
