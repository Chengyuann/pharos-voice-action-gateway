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
| Batch lifecycle | Existing capability-token and revision state machine now binds confirmation to all rows and memos; any edit or cancellation revokes confirmation |
| Batch review and export | One common block, separately replayable row proofs, whole-batch balance/fee checks, fresh full recheck before one unsigned export |
| Shared nonce reservation | Contiguous ordered nonces reserved atomically with all asset/fee charges in the same ledger as single MCP payments; overlaps fail closed |
| Batch interface | Single/batch switch, indexed follow-up questions, full recipient list, local memos, aggregate totals and nonce order, explicit whole-batch confirmation, activity and audit downloads |

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
- **Batch export is not atomic execution.** Only Pacific Mainnet PROS, USDC
  and WPROS with ordinary EOA recipients are supported. Each row is simulated
  independently at the same block, not after applying earlier rows. Stateful
  token behavior and external pending transactions can still change outcomes.
  The export remains `signing_ready: false` and `wallet_ready: false`; each
  request needs an independent wallet check. No signing or broadcast is added.
- **Nonces do not expire locally.** Cancelling, restarting or waiting for the
  allowance window to expire does not release an exported or reserved nonce.
  A conflicting pending nonce is blocked rather than silently skipped.
  When the chain advances to a new non-overlapping pending range, a fresh
  review can proceed. This does not coordinate other apps or prove that a
  previous exported payment was executed. Legacy P1 mainnet reservations
  without nonce metadata block further exports for that sender until a
  separately designed reconciliation process exists.
- **Time and precision.** Common-block collection must finish within 60
  seconds; all rows must agree on sender, block/hash, native balance, gas price,
  pending nonce and policy. Nonce sequences use decimal strings in JSON to
  preserve integers above JavaScript's safe-integer range.

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

## Batch verification

- 30 dedicated batch lifecycle tests passed, covering shared single/batch nonce
  conflicts, whole-batch budget denial, fee-inclusive charges, clarification,
  memo edits, partial simulation failure, policy changes, cancellation during
  work, restart, disk failure, exact large nonces and legacy reservations.
- Final complete regression: **415 tests passed in 90.08 seconds**.
- Browser QA used an isolated, visibly marked `FakeChain` server. Missing amount
  follow-up, disabled export before confirmation, two-request export and the
  audit link were exercised. Screenshots and DOM checks at 320, 390, 768, 1280
  and 1600 pixels had no horizontal overflow. The final code repeated the
  mixed USDC/PROS example, whole-batch confirmation and two-request export.
- The curated P0 bundle still validates; `site/` is unchanged. Private QA
  records remain under ignored `output/p1/`, not in the public evidence.

## Next decision

The October 25 release preparation added actual read-only batch evidence,
independent replay, an isolated offline preview/ZIP builder and review fixes.
Final regression is **439 passed / 89.82s**. The latest recorded batch at
block **21189980** contains 19 MCP calls and 12 matching secondary comparisons.
See [release preparation](p1-release-preparation.md) for exact scope and commands.

Review P1 for merge/publication separately. Live microphone capture, ASR/TTS,
speaker verification, wallet signing and sequential transaction simulation
remain outside this batch milestone. No Arena resubmission is automatic.

Primary references used for this implementation:

- EIP-55: `https://eips.ethereum.org/EIPS/eip-55`
- Ethereum JSON-RPC: `https://ethereum.org/en/developers/docs/apis/json-rpc/`
