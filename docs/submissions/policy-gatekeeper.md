# Arena submission — Policy Gatekeeper

Status: **submitted, IN REVIEW**, October 22, 2026, account `Chengyuann`.
Submitted release: `ffe94cc7eb058dbb041aa21f6ff8278269d7c0ed`.
Task-page success and the My work dashboard both confirmed entry into the queue.
Approval and points are pending.

## Description

Pharos Voice Action Gateway provides an explicit policy gate before action
confirmation. A configurable spend limit, recipient/token allowlist and UTC
time window return allow/deny with the deciding rule. Denials never reach the
executor. Every evaluation and completion is recorded as a distinct local JSON
audit record.

The reproducible demo includes an allowed action and one denial for each
required rule type. The allowed scenario calls only a deterministic local mock;
none of these examples sends an on-chain transaction.

## Evidence mapping

- Config: `config/policy_gatekeeper.json`
- Enforcement: `scripts/pharos_voice_action_gateway.py`
- Inspector: local `/policy-gatekeeper`
- Public evidence page: `#policy`
- Allow: `site/proofs/policy-allow.json`
- Spend denial: `site/proofs/policy-deny-amount.json`
- Allowlist denial: `site/proofs/policy-deny-recipient.json`
- Time denial: `site/proofs/policy-deny-time.json`
- Tests: `scripts/test_policy_gatekeeper.py`,
  `scripts/test_policy_gatekeeper_http.py`

## Reviewer walkthrough

Open the Policy tab. Select each denial. Compare the deciding rule, mock
executor flag and audit checks. Download the JSON to inspect the stored outcome.
Run the local inspector to generate new records instead of viewing snapshots.

## Submission fields

- Proof URL: https://chengyuann.github.io/pharos-voice-action-gateway/#policy
- Repo URL: https://github.com/Chengyuann/pharos-voice-action-gateway
- Video URL: https://chengyuann.github.io/pharos-voice-action-gateway/media/01-policy-gatekeeper-mcy.mp4

The proof page displays recorded evidence, not a live payment service.
