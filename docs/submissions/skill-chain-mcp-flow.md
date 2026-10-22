# Arena submission draft — Skill + Chain MCP Flow

Status: **published, not submitted**. Arena login and final submit confirmation remain pending.

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

The official Skill Engine page describes an installable skill and recommends
`npx skills add https://github.com/PharosNetwork/pharos-skill-engine`.
Both that Skill and the local extension were installed for Codex. The October
22 run at block **20913011** records installed file hashes, 10 actual official
MCP calls, a real `cast balance` at that block, and a final decision dependent
on balance agreement. The evidence runner adds this gate after MCP collection
and before retaining the candidate. A disagreement removes the candidate;
unavailable MCP stops the run without RPC fallback.

`site/proofs/installed-skill-flow.json` holds the real run. This is local Agent
execution of installed Skills, not hosted chat inside `ai.pharos.xyz`, and not
a claim of task approval. The extra official-Skill/cast gate belongs to the
evidence runner; it is not falsely represented as an existing UI feature.

## Reproduce

```sh
python3 scripts/payment_proof.py review "Pay alice 0.001 PROS" \
  --sender "0x…public-address…" --output /tmp/new-proof.json
python3 scripts/payment_proof.py verify /tmp/new-proof.json
python3 scripts/skill_chain_flow.py --verify site/proofs/installed-skill-flow.json
```

The CLI collects review evidence without performing the final UI handoff.
The local `/payment-preview` supplies human confirmation and draft rechecks.
Saved success and denial samples are under `site/proofs/*live.json`; open the
public `#composition` tab to inspect call order and arguments.

Offline replay checks consistency, not chain provenance or wallet ownership.
No signature or broadcast occurs.

## Submission fields

- Proof URL: https://chengyuann.github.io/pharos-voice-action-gateway/#composition
- Repo URL: https://github.com/Chengyuann/pharos-voice-action-gateway
- Video URL: https://chengyuann.github.io/pharos-voice-action-gateway/media/03-skill-chain-mcp-mcy.mp4

The proof page displays recorded evidence, not a live payment service.

The video explains earlier recorded composition. The newer installed-Skill
run is in the evidence selector and downloadable JSON, not retroactively
claimed as footage in the video.
