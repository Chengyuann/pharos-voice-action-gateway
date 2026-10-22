# Arena submission draft — Skill + Chain MCP Flow

Status: **not submitted**. Hosted SkillEngine acceptance is not yet verified.

## Description

A repository-local Pharos payment Skill composes official Chain MCP reads with
an executable decision step. The tools are not merely shown side by side:
`decide_mainnet_payment` consumes the exact balances, simulation result, nonce
and gas values returned in that same request.

```text
User request
  → parse / clarify amount, asset, recipient
  → official Pharos Chain MCP
      get_chain_info / get_block_number / get_block
      get_balance / get_code / eth_call(metadata + balance + transfer)
      get_transaction_count(pending) / estimate_gas / get_gas_price
  → payment Skill: policy + balance + fee + simulation decision
  → human-readable review
  → explicit action-revision confirmation
  → new Chain MCP checks (changes revoke approval)
  → unsigned wallet request + trace proof
```

State and simulation are pinned to one block. Pending nonce, gas estimate and
gas price are explicitly current, unpinned calls because the official tool
interface does not provide a block parameter for those fee calls.

## Skill Engine relationship

`skills/pharos-payment-review/SKILL.md` packages the local extension. It uses the
network/token configuration and transaction prechecks from
`PharosNetwork/pharos-skill-engine` at
`a873387c9e34e9ae8e71360d43dc33329a335a24`. The executable handler applies the
upstream `ceil(estimate × 1.2)` gas rule to actual Chain MCP output.

This does not claim an installed hosted SkillEngine service, a `cast` execution,
or a run inside `ai.pharos.xyz`. The current proof is local Skill-format
composition. Validate the accepted runtime/packaging with reviewers before
claiming full task acceptance.

## Reproduce

```sh
python3 scripts/payment_proof.py review "Pay alice 0.001 PROS" \
  --sender "0x…public-address…" --output /tmp/new-proof.json
python3 scripts/payment_proof.py verify /tmp/new-proof.json
```

The CLI collects review evidence without performing the final UI handoff.
The local `/payment-preview` supplies human confirmation and draft rechecks.
Saved success and denial samples are under `site/proofs/*live.json`; open the
public `#composition` tab after hosting to inspect call order and arguments.

Offline replay checks consistency, not chain provenance or wallet ownership.
No signature or broadcast occurs.

## Submission fields to complete after approval

- Proof URL: actual public evidence deployment, `#composition`.
- Repo URL: published release commit including the Skill package.
- Video URL: final reviewed composition walkthrough.
