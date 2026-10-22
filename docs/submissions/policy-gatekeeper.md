# Arena submission draft — Policy Gatekeeper

Status: **not submitted**. Publish and verify the source and evidence URL first.

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
- Public evidence page after hosting: `#policy`
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

## Submission fields to complete after approval

- Proof URL: actual public evidence deployment, `#policy`.
- Repo URL: published `Chengyuann/pharos-voice-action-gateway` release commit.
- Video URL: upload only after the final recording is checked.

Do not submit a localhost address. The static page is a recorded evidence
viewer; the loopback product generates new decisions. Local audit files are
not authenticated immutable storage. Voice is fixture replay.
