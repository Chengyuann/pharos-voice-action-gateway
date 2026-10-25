#!/usr/bin/env python3
"""Batch parsing, common-block reviews and ordered unsigned payment requests."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unicodedata
import time
from uuid import uuid4

from evm_address import validate_address
from payment_language import parse_mainnet_request, load_contacts
from payment_chain import load_mainnet_registry, make_mainnet_review, mainnet_draft, MAINNET_POLICY
from payment_preview import review_fingerprint, units_to_amount
from payment_proof import verify_proof, ReplayMismatch
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
                    state["native_balance"], state["nonce"], state["gas_price"], state["block_timestamp"])
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
              {"name": "ordinary_recipient_accounts", "passed":
               all(proof["state"]["recipient_code"] == "0x" for proof in proofs)},
              {"name": "aggregate_native_plus_fees", "passed": native_required <= reference[3]}]
    for symbol, amount in totals.items():
        checks.append({"name": f"aggregate_{symbol}_balance", "passed": amount <= available[symbol]})
    return {"schema": "pharos-batch-aggregate/1",
            "status": "candidate_plan" if all(c["passed"] for c in checks) else "blocked",
            "totals_base_units": {k: str(v) for k, v in totals.items()},
            "native_fee_budget_wei": str(native_fee), "checks": checks,
            "block": reference[1], "signing_ready": False, "signed": False, "broadcast": False,
            "boundary": "Aggregate evidence only. Independent simulations, not sequential or atomic execution. "
                        "This aggregate object allocates no nonce and reserves no issuance allowance."}


BATCH_BOUNDARY = (
    "Ordered unsigned requests, not an atomic transaction. Each transfer was simulated independently "
    "at one block, not against the results of preceding transfers. Recheck each request in your wallet. "
    "Memos remain local. Nothing is signed or sent."
)


def make_batch_review(text, answers=None, *, sender="", policy_path=MAINNET_POLICY, client=None, now=None):
    started = now or datetime.now(timezone.utc)
    deadline = time.monotonic() + 60
    if started.tzinfo is None:
        raise ValueError("Review time must be timezone-aware")
    plan = parse_batch(text, answers, contacts=load_contacts())
    result = {
        "id": uuid4().hex, "record_type": "payment_review", "kind": "batch",
        "created_at": started.isoformat(), "expires_at": (started + timedelta(minutes=10)).isoformat(),
        "mode": "mcp", "request_text": text, "answers": deepcopy(answers or {}),
        "status": plan["status"], "message": plan.get("message", ""), "plan": plan,
        "questions": plan.get("questions", []), "payments": plan.get("payments", []),
        "payment": None, "items": [], "checks": [], "draft_allowed": False,
        "draft_label": "Create unsigned batch", "wallet": {"address": sender, "source": "configured_readonly_address"},
        "network": {"name": "Pharos Pacific Mainnet", "chain_id": 1672, "source": "chain_mcp"},
        "fee": {"status": "not_checked"}, "recorded": False, "boundary": BATCH_BOUNDARY,
    }
    if plan["status"] == "planned":
        block = None
        try:
            for payment in plan["payments"]:
                if time.monotonic() >= deadline:
                    raise ValueError("Batch collection exceeded 60 seconds; retry the review")
                row = make_mainnet_review(
                    f"Send {payment['amount']} {payment['token']} to {payment['recipient']}",
                    sender=sender, policy_path=policy_path, client=client, now=now,
                    block_number=block, deadline=deadline,
                )
                result["items"].append(row)
                if row["status"] != "ready":
                    result.update(status=row["status"], message=f"Payment {len(result['items'])}: {row['message'] or 'Checks did not pass.'}")
                    break
                block = row["network"]["block_number"]
            else:
                configs = {item["configuration_hash"] for item in result["items"]}
                if len(configs) != 1:
                    raise ValueError("Configuration changed during batch collection")
                aggregate = aggregate_proofs(plan, [row["proof"] for row in result["items"]])
                finished = now or datetime.now(timezone.utc)
                if time.monotonic() >= deadline or not 0 <= (finished - started).total_seconds() <= 60:
                    raise ValueError("Batch collection exceeded 60 seconds; retry the review")
                first = result["items"][0]
                base_nonce = first["proof"]["state"]["nonce"]
                if base_nonce + len(result["items"]) > 2**64 - 1:
                    raise ValueError("Batch nonce range exceeds EVM account limits")
                ready = aggregate["status"] == "candidate_plan"
                result.update(
                    status="ready" if ready else "blocked", draft_allowed=ready,
                    configuration_hash=first["configuration_hash"], aggregate=aggregate,
                    network=deepcopy(first["network"]), pending_nonce=str(base_nonce),
                    nonce_sequence=[str(base_nonce + i) for i in range(len(result["items"]))],
                    fee={"status": "estimated", "amount": units_to_amount(int(aggregate["native_fee_budget_wei"]), 18),
                         "symbol": "PROS"},
                    checks=[{"name": check["name"], "label": check["name"].replace("_", " ").title(),
                             "passed": check["passed"], "detail": "Whole-batch check", "source": "chain_mcp_skill"}
                            for check in aggregate["checks"]],
                    message=BATCH_BOUNDARY,
                )
        except (ValueError, ReplayMismatch, KeyError, TypeError, OSError, OverflowError) as exc:
            result.update(status="data_unavailable", draft_allowed=False,
                          message=f"Batch checks failed: {str(exc)[:240]}")
    result["integrity_hash"] = review_fingerprint(result)
    return result


def batch_comparison(review):
    """Only a fresh snapshot may differ; authorization-relevant fields may not."""
    return {
        key: review.get(key) for key in ("configuration_hash", "plan", "pending_nonce", "nonce_sequence")
    } | {
        "items": [{key: item.get(key) for key in
                   ("configuration_hash", "payment", "prepared_transaction", "fee", "balance")}
                  for item in review["items"]]
    }


def batch_draft(review, now=None):
    now = now or datetime.now(timezone.utc)
    if (review.get("kind") != "batch" or review.get("status") != "ready"
            or review.get("draft_allowed") is not True
            or review.get("integrity_hash") != review_fingerprint(review)
            or not 0 <= (now - datetime.fromisoformat(review["created_at"])).total_seconds() <= 60
            or now >= datetime.fromisoformat(review["expires_at"])):
        raise ValueError("A fresh, intact and allowed batch review is required")
    aggregate = aggregate_proofs(review["plan"], [item["proof"] for item in review["items"]])
    if aggregate != review["aggregate"] or aggregate["status"] != "candidate_plan":
        raise ValueError("Batch aggregate changed or failed")
    if review["payments"] != review["plan"]["payments"]:
        raise ValueError("Batch rows or memos disagree with the reviewed plan")
    base_nonce = review["items"][0]["proof"]["state"]["nonce"]
    sequence = [str(base_nonce + i) for i in range(len(review["items"]))]
    if (review.get("nonce_sequence") != sequence or review.get("pending_nonce") != str(base_nonce)
            or int(sequence[-1]) >= 2**64 - 1):
        raise ValueError("Invalid batch nonce sequence")
    items = []
    for index, (source, payment) in enumerate(zip(review["items"], review["payments"])):
        if source.get("prepared_transaction") != source["proof"]["transaction"]:
            raise ValueError("Prepared row transaction disagrees with its MCP proof")
        item = mainnet_draft(source, now)
        item["transaction"]["nonce"] = hex(int(sequence[index]))
        item.update(order=index + 1, memo=payment["memo"], wallet_ready=False,
                    simulation_nonce=str(base_nonce), nonce_scope="ordered_request_not_sequential_simulation")
        item.pop("wallet_uri", None)
        item["integrity_hash"] = review_fingerprint(item)
        items.append(item)
    result = {
        "id": uuid4().hex, "record_type": "payment_draft", "kind": "unsigned_batch", "mode": "mcp",
        "review_id": review["id"], "created_at": now.isoformat(), "expires_at": review["expires_at"],
        "status": "draft_created", "items": items, "payments": deepcopy(review["payments"]),
        "network": deepcopy(review["network"]), "fee": deepcopy(review["fee"]),
        "plan": deepcopy(review["plan"]), "aggregate": aggregate, "nonce_sequence": sequence,
        "transaction_count": len(items), "wallet_ready": False, "signing_ready": False,
        "atomic_execution": False, "sequential_simulation": False,
        "signed": False, "broadcast": False, "recorded": False, "message": BATCH_BOUNDARY,
    }
    result["integrity_hash"] = review_fingerprint(result)
    return result


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
