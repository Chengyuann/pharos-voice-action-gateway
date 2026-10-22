# P1 implementation checkpoint — October 22, 2026

Work branch: `p1-policy-and-language`. **Not merged, deployed or resubmitted to
Arena.** P0 `main` and the curated public `site/` files are preserved.

## Delivered in this checkpoint

| Area | Implementation |
|---|---|
| Rolling issuance budget | `payment_ledger.py`; SQLite immediate transactions; mode/chain/sender/asset scopes; amount plus native fee budget |
| Race and failure safety | Persist reservation before JSON publication; concurrent competing actions cannot exceed the configured allowance; restart, cancel or export failure does not refund |
| Policy binding | Human review displays policy; changed policy invalidates old review; rechecked before publication |
| Hash-chain audit | Immutable-by-API records, SQL update/delete guards, report-content hashes, issuance projection verification |
| Independent verification | `payment_audit.py export/verify`, optional report-file comparison and externally retained expected tip |
| Recipient checks | EIP-55 mixed-case validation, zero/known-burn/known-token-contract recipient checks |
| Contacts | Exact configured alias, including 1–5 explicitly listed addresses with a user-selected full address; no first-match guessing or ENS resolution |
| Secondary RPC | Optional read-only same-block chain/header/balance/code/call comparison, after mandatory valid official MCP evidence |
| Batch foundation | 1–5 semicolon-separated planning rows, per-row clarification, local JSON-quoted memo, duplicate-row rejection and aggregate same-snapshot balance checks |

## Boundaries that must remain explicit

- **Issuance is not spend.** This counts exports through the local action API,
  including conservative reservations that may never be published. It cannot
  stop a wallet or another process from constructing or signing a transaction.
  Review JSON, proof replay and CLI evidence are not wallet authorizations.
- **No refund on cancellation.** An exported unsigned draft cannot be recalled.
  Conservative reservations age out of the rolling window; no admin “reset
  budget” endpoint is exposed.
- **Audit integrity is not authenticity.** Hashes detect inconsistent edits.
  An externally retained trusted tip is needed to detect truncation or
  whole-history replacement. SQL triggers do not defend against a database
  administrator. Report files and the action database are separate stores:
  crashes may leave orphan historical reports; budget is reserved before
  export so such reports are never uncounted issued drafts.
- **Local-only scope.** The server remains loopback-bound; audit exports contain
  payment information and must not be published. Old records predating this P1
  ledger are not retroactively hash-chained or charged.
- **Secondary path is not independent infrastructure.** The tested direct RPC
  endpoint may share infrastructure with MCP. It supplies corroboration, not
  authenticated provenance or a fallback.
- **Batch planning is not complete batch payment.** No nonce allocation,
  sequential-state simulation, batch confirmation, atomic batch reservation,
  signing/export endpoint or full batch UI has been implemented. The planner
  returns `signing_ready: false`, even when aggregate checks pass.

## Reproduce

```sh
python3 scripts/policy_gatekeeper_server.py --port 8791 \
  --reports output/p1/local/policy \
  --issuance-budget config/payment_p1_policy.json

python3 scripts/payment_audit.py export \
  output/p1/local/payment_preview/_actions.sqlite3 --output /tmp/action-audit.json
python3 scripts/payment_audit.py verify /tmp/action-audit.json \
  --expected-tip "0x…previously-retained-trusted-tip…"

python3 scripts/payment_batch.py \
  'Pay alice 0.1 USDC memo "invoice 24"; Pay bob 0.01 PROS' \
  --output /tmp/batch-plan.json

python3 -m pytest scripts -q
python3 scripts/build_arena_bundle.py
```

With an explicitly configured secondary endpoint:

```sh
PHAROS_SECONDARY_RPC_URL="https://rpc.pharos.xyz" \
  PHAROS_MCP_READ_ADDRESS="0x…public-address…" \
  python3 scripts/policy_gatekeeper_server.py --port 8791
```

The endpoint sees public addresses and RPC parameters, not private keys or
memo text. It is disabled unless configured. If configured, failure blocks
the candidate rather than silently dropping the extra gate.

## Verification record

- Final full regression: **385 tests passed in 80.75 seconds**; legacy smoke
  tests, Skill package validation, JavaScript syntax and curated P0 bundle
  verification all passed.
- Official EIP-55 vectors passed. The local Keccak implementation additionally
  matched the already-installed Foundry `cast` over 10 input lengths (including
  135/136/137-byte rate boundaries) and 12 checksum-address samples.
- Browser: demo review → explicit confirmation → example draft; issuance
  notice, no-refund explanation and audit-chain download appeared correctly.
  Repeated with the final code after restart; existing allowance remained
  counted and the page had no horizontal overflow.
  No application error was observed (a wallet extension separately reported
  its locked state). No wallet was unlocked or used.
- New mainnet **read-only** run: block **20920882**, 10 official MCP calls and
  six matching secondary checks (chain ID, block header, balance, sender and
  recipient code, simulation). Primary proof replay passed. Saved locally:
  `output/p1/secondary-live-review.json`, not substituted into P0 evidence.
- The copied P0 static-file baseline matched the working tree exactly.

## Next P1 milestone

1. Introduce a batch action/revision state machine, confirmation tied to every
   row and memo, and a complete batch UI.
2. Collect all batch state at a common block, then recheck before export.
3. Define ordered nonce and sequential-state behavior rather than treating
   independently simulated transfers as one atomic transaction.
4. Atomically reserve **all** batch asset/fee allowances before issuing any
   signing requests; cancellation/failure must never partially bypass policy.
5. Add integration tests, then review P1 for merge/publication separately.

Primary references used for this implementation:

- EIP-55: `https://eips.ethereum.org/EIPS/eip-55`
- Ethereum JSON-RPC: `https://ethereum.org/en/developers/docs/apis/json-rpc/`
