# Policy Gatekeeper Submission Notes

## Task

Pharos Season of AI Agent Arena: Policy Gatekeeper.

## Reviewer Checklist

| Requirement | Evidence |
|---|---|
| Three rule types | Spend limit, recipient allowlist, and UTC time window are configured in `config/policy_gatekeeper.json`; token allowlist is an additional rule |
| Allow or deny with deciding rule | `policy_decision.outcome` and `policy_decision.deciding_rule` are shown in the web demo and JSON audit |
| One deny per required rule | Presets reproduce amount, recipient, and time-window denial |
| Denied actions never execute | The UI shows `Executor: Not called`; the report sets `execution_attempted: false` and `mock_executor_called: false` |
| Audit log | Each evaluation and completion result is persisted as a separate append-only local JSON file under `reports/policy_gatekeeper/` |
| Recheck before execution | `submit_or_simulate` evaluates the current time window again before the mock executor |

## Run

```bash
python3 scripts/policy_gatekeeper_server.py --port 8766
```

Open `http://127.0.0.1:8766/policy-gatekeeper`.

## Test

```bash
python3 -m pytest scripts/test_policy_gatekeeper.py scripts/test_policy_gatekeeper_http.py -v
python3 scripts/run_demo_tests.py
python3 scripts/run_pharos_demo_tests.py
```

## Demo Order

1. Select **Allow**, enable mock approval, and evaluate. All checks pass and
   only the mock executor is called.
2. Select **Deny amount**. The spend-limit rule denies and the executor is not
   called.
3. Select **Deny recipient**. The recipient rule denies and the executor is
   not called.
4. Select **Deny time**. The time-window rule denies and the executor is not
   called.
5. Export the latest audit JSON. Earlier evaluations remain in the session
   table and in separate local files.

## Current Boundary

This build is a reviewer-openable local demo. It does not yet provide a public
deployment or an `ai.pharos.xyz` share link. It parses a strict typed payment
command for the policy demonstration; live ASR, token metadata, balance
validation, Chain MCP, wallet signing, and broadcasting remain out of scope.
The local JSON files are not tamper-proof storage; production use would require
signed records or an append-only remote registry.

The server is for local demonstration only. Before hosting publicly, use a
production server, authentication/access controls for audit downloads, rate
limits and a storage-retention policy. The sample policy uses the legacy
prototype's PHRS symbol and does not verify a live network or token contract.
The legacy JSONL voice parser still has heuristic confirmation; the new typed
demo uses a strict single-command parser and separate explicit mock approval.

`pytest` is a development dependency for the new test file; the demo server
itself still requires only Python 3.9+ and the standard library.

## Verification (October 20, 2026)

- 77 core and loopback HTTP test cases passed after the badcase review.
- Both existing demo suites passed.
- Browser interactions verified allow and the three independent denials.
- Mobile layout checked at 390px wide; no page-level horizontal overflow.
- Audit download response matched the saved JSON record exactly.
- No public deployment, live Pharos integration, competition submission,
  or real transaction was performed.

See [badcase review and design decisions](policy-gatekeeper-badcases.md).
Audit downloads are local-only, and prepared-object integrity is an accidental
mutation check rather than a cryptographic authorization mechanism.
