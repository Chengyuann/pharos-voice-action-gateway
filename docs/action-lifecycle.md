# Local Action Lifecycle

Implemented on 2026-10-21. This is the first action-safety milestone, not a live
speech or Speaker Guard release.

## Behavior

- One action has an unpredictable capability token, a revision, and server-side state.
- Update/invalidate/correct advances the revision and discards prior review/confirmation.
- Explicit corrections change only the amount. Ambiguous corrections invalidate
  prior approval and require rewriting the request.
- Cancel ignores the client's revision so a stop can cancel the latest version.
  A cancelled action cannot resume; the page starts a new one for another review.
- Review and draft network checks run outside SQLite write transactions. Final
  publication checks the action revision/status again inside a transaction.
- A ready, unexpired saved review must be explicitly confirmed before a draft.
- Concurrent duplicate drafts are rejected; retries after success return the
  same saved draft. No automatic retry of a failed draft is performed.
- Server restart changes the process epoch and rejects all old action sessions.
- The silence-driven gateway path no longer commits a partial merely because
  its text is long enough before the configured silence threshold. ASR-final
  completion heuristics remain a legacy prototype, not reliable semantic EOU.

## API

All requests are JSON and subject to the existing loopback Host/Origin checks.
Create an action with `POST /api/payment/action` and `{}`. Keep its `token`
only in page memory; all following requests require `X-Action-Token`.

| Path suffix under `/api/payment/` | JSON fields |
|---|---|
| `action/update` | `action_id`, `revision`, `input: {text, answers, mode}` |
| `action/invalidate` | `action_id`, `revision` |
| `action/correct` | `action_id`, `revision`, `text` |
| `action/cancel` | `action_id` |
| `review` | `action_id`, `revision` |
| `action/confirm` | `action_id`, `revision`, `review_id` |
| `draft` | `action_id`, `revision`, `review_id` |

An action revision mismatch or invalid transition returns HTTP 409. Missing or
incorrect capability tokens return HTTP 403. Invalid input returns HTTP 400.
Storage failures do not return success.

The default running server rejects legacy payment calls with no action session.
`allow_legacy_payment_api` is an internal compatibility-test flag, not a public
query parameter or CLI option. The separate Policy Gatekeeper mock inspector
retains its own explicit mock-only API.

## Storage and limits

`reports/payment_preview/_actions.sqlite3` contains workflow state and token
digests, with mode 0600. It is not exposed by the JSON audit route. The existing
payment JSON audit reports contain action ID/revision but never capability tokens.

This remains a local single-user app. A capability token is not a user's identity,
biometric verification, or transaction authorization. Same-origin compromised
code or a malicious local user is outside this prototype's protection.
Audit/recent routes remain local-workspace history, not multi-user access control.

SQLite state and immutable JSON files are separate stores. A process failure
between file publication and transaction commit can leave an orphan historical
file. Restart rejects old sessions, but cross-store recovery and bounded retention
are required before public hosting. A cancelled action cannot recall a draft that
was already exported or saved before cancellation was accepted. No signing or
broadcast exists.

## Verification

The focused suite covers current confirmation, cancellation, corrections,
ambiguous input, token isolation, stale revisions, server restart, private
database permissions, delayed review/draft results, concurrent duplicate draft
requests, live-recheck record protection, and persistence failures.

```bash
python3 -m pytest scripts/test_payment_actions.py -q
python3 -m pytest scripts/test_payment_preview.py \
  scripts/test_payment_preview_http.py scripts/test_payment_rpc.py \
  scripts/test_policy_gatekeeper.py scripts/test_policy_gatekeeper_http.py \
  scripts/test_payment_actions.py -q
python3 scripts/run_demo_tests.py
python3 scripts/run_pharos_demo_tests.py
```

Test results and browser verification are recorded in the project progress log.
No real microphone, biometric enrollment, voice model download, wallet action,
or public deployment was performed in this milestone.

## Next

Connect a local ASR adapter with explicit microphone start/stop and server-side
audio limits. Then evaluate opt-in 1:1 Speaker Guard as a separate consistency
signal, never as a replacement for the current-review confirmation.
