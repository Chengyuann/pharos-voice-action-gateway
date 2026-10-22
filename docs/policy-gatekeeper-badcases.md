# Policy Gatekeeper: badcase review — October 20, 2026

## Method and attribution

Used the installed `claude-worker` skill for three read-only rounds:
independent review, design discussion, and post-fix review. Its configured
backend is `openrouter-4o` through the local Trae bridge; this is not a claim
that an Anthropic Claude model performed the review.

The worker's observations were static. Its first attempt to run probes was
permission-blocked. Codex independently reproduced failures, implemented
changes, and ran the checks below. No worker patches were integrated.

The checkout had pre-existing uncommitted changes, so review deliberately ran
in place without concurrent writes. The first wrapper run returned scope code
3 because it included those existing changes in its scope check; worker exit
code was 0. Later scoped discussion/review runs returned 0. No commits, stashes,
resets, pushes, deployments or real transactions were performed.

## Reproduced cases and fixes

| Badcase | Before | Now |
|---|---|---|
| Change preview amount from `0.02` to `9999` after preparation | Returned `simulated` while policy checked the original intent | `blocked_by_integrity`; no mock executor |
| Caller mutates the original intent object | Prepared intent changed through shared references | Preparation deep-copies intent; later prepared-object edits are checked |
| Very large amount with fractional units | Decimal multiplication rounded silently | Integer-based exact unit conversion; precision and uint256 bounds checked |
| `0001-01-01T00:00:00+01:00`, `9999-12-31T23:59:59-23:59` | OverflowError, no audit | Validation deny with audit |
| Repeated `confirmed` JSON key | Last value silently won | HTTP 400, including nested duplicate keys |
| NaN, Infinity, `1e400`, 4,000 nested arrays | Invalid numeric values accepted or parsing crashed | HTTP 400, finite-float and depth checks |
| Matching attacker-controlled Host/Origin | Policy endpoint returned 200 | Both GET and POST require the loopback host and correct port; present Origin must match exactly |
| Truncated/slow request or duplicate Content-Length | Misclassified storage error / ambiguous length | Bounded reads, explicit 400/408 |
| Executor raises after initial audit | Orphan pending record, misleading JSON error | Linked terminal executor-error audit; unknown mock outcome is explicit |
| Completion audit write fails after mock ran | Generic 503 obscured the actual result | 503 includes the actual result and `recorded=false`; no false persistence claim |
| Partial audit serialization or record collision | Could expose partial JSON | Complete temporary file published without overwriting an earlier record |
| Change clock/time after approving | Old approval and export remained | Approval cleared, stale result reset, stale download disabled |
| Saved executor-error result in UI | Export incorrectly disabled | Persisted error record remains downloadable; unpersisted records do not |

The exact-unit regression:

```text
Input:    12345678901234567890.123456789123456789
Old:      12345678901234567890123456790000000000
Expected: 12345678901234567890123456789123456789
```

## Discussion decisions

- Keep explicitly labelled mock replay. Client-supplied time is rejected in
  wallet mode; the mock executor cannot sign or broadcast. A blanket replay
  ban would remove the deterministic review fixtures, not protect real funds.
- Deep-copy alone is insufficient. A prepared-object fingerprint plus rebuilt
  preview catches accidental changes and mismatched artifacts. This is **not**
  authentication against hostile Python code in the same process.
- Do not claim an audit file exists when persistence fails. The initial record
  may be the only durable evidence; the response includes the final result
  where available. Process crashes still can leave a pending record.
- No real-executor idempotency, production wallet redesign, public hosting or
  live MCP integration was added. Before real execution, durable idempotency,
  signed consent, runtime isolation, and crash recovery are required.

## Verification

```bash
python3 -m pytest scripts/test_policy_gatekeeper.py scripts/test_policy_gatekeeper_http.py -q
python3 scripts/run_demo_tests.py
python3 scripts/run_pharos_demo_tests.py
git diff --check
```

Result: **77 tests passed** (previously 42); both legacy smoke suites and diff
whitespace check passed. Browser verification confirmed that editing time and
switching clocks clears approval, marks the result stale, empties old JSON and
disables old downloads.

No production security audit or live-chain test is implied by these results.
