---
name: pharos-payment-review
description: Prepare a single Pharos Pacific Mainnet payment for human review using official Chain MCP data, policy checks and an unsigned wallet request. Use for PROS, USDC or WPROS payment preparation and inspection; never for signing or broadcasting.
---

# Pharos payment review

Run from the repository root containing `scripts/payment_proof.py`. Python 3.9+
is sufficient; no private key, wallet login, Foundry install or package download
is needed for this read-only flow.

## One request, one composed flow

1. Obtain the public sender address the user wants inspected. The address is
   not proof that the user owns it. Do not read wallet keys or infer a sender.
2. Use the local workspace for clarification and confirmation:
   ```sh
   PHAROS_MCP_READ_ADDRESS="0x…public-address…" \
     python3 scripts/policy_gatekeeper_server.py --port 8766
   ```
   Open `/payment-preview`, select **Official Chain MCP**, and enter the
   instruction. Example: `Pay alice 0.001 PROS`. Alice and Bob in the default
   contact file are **placeholder addresses**, not verified payees.
3. The Python handler parses the text and resolves only exact configured
   contacts. Ask the displayed follow-up on ambiguity; do not guess an amount,
   asset or address. Current scope is one payment, not batching or scheduling.
4. `payment_chain.collect_chain_state` obtains state through the official
   `https://mcp.pharos.xyz/mcp` endpoint. Chain 1672 / PROS is enforced.
   Balance, contract code, ERC-20 metadata and `eth_call` are pinned to one
   block. Pending nonce and current gas quotes are explicitly unpinned.
5. `payment_chain.decide_mainnet_payment` consumes those returned values and
   the policy. Missing data or a failed check prevents draft creation.
6. Show amount, full recipient, sender, chain, asset contract, fee and checks.
   Only after the user confirms that review does the server re-read the chain
   and create an unsigned draft with nonce, gas, calldata and EIP-681 URI.
   Changed details, nonce, balance, fees or policy require another review.
7. Editing or cancelling an action invalidates its confirmation. A late
   response cannot publish a draft for an obsolete action revision.

For evidence collection without the interactive wallet handoff:

```sh
python3 scripts/payment_proof.py review "Pay alice 0.001 PROS" \
  --sender "0x…public-address…" --output /tmp/payment-proof.json
python3 scripts/payment_proof.py verify /tmp/payment-proof.json
```

The CLI writes a **review proof**, not permission to sign or send. Successful
proofs contain historical candidate transaction fields. Offline verification
checks internal consistency; it does not authenticate chain provenance or
ownership. A wallet must independently validate everything before signing.

## Upstream Skill Engine composition

This is an installable local extension of the Pharos Skill Engine workflow,
not a hosted `ai.pharos.xyz` chat service.
It applies the upstream transaction prechecks and gas-budget rule
`ceil(estimate × 1.2)` to actual Chain MCP data in the same request.

The official Skill Engine page offers the skills-CLI installation method:

```sh
npx skills add https://github.com/PharosNetwork/pharos-skill-engine --agent codex
npx skills add ./skills/pharos-payment-review --agent codex
```

For an installed-Skill evidence run, use the actual paths returned by the
installer and a working Foundry `cast`:

```sh
python3 scripts/skill_chain_flow.py \
  --request "Pay alice 0.001 PROS" --sender "0x…public-address…" \
  --installed-upstream /path/to/installed/pharos-skill-engine \
  --installed-local /path/to/installed/pharos-payment-review \
  --cast /path/to/cast --output /tmp/installed-skill-flow.json
```

This follows the installed official Skill's `cast balance` template at the
MCP snapshot block. The additional gate requires balance agreement before
retaining an unsigned candidate. If official MCP is unavailable, it stops;
direct RPC through cast is never a fallback. It invokes no `cast send`, key
access or signing. Recorded October 22 evidence, including executed commands,
installed-file hashes and their results, is in
`site/proofs/installed-skill-flow.json`.

- Upstream: `PharosNetwork/pharos-skill-engine`
- Pinned commit: `a873387c9e34e9ae8e71360d43dc33329a335a24`
- References: `references/transaction.md` (gas calculation / transaction
  prechecks), `assets/networks.json`, `assets/tokens.json`
- Local token config: `config/pharos_mainnet_tokens.json`
- Local policy: `config/payment_mainnet_policy.json`
- Handler: `scripts/payment_chain.py`

Single-payment limits are in units **of each token**, not USD. Only configured
recipients pass. P1 validates mixed-case addresses using EIP-55; invalid checksums
must not be silently lowercased. All-lowercase/all-uppercase input is accepted
without a checksum claim. Zero, known burn and known token-contract recipients
are rejected.

## P1 local guardrails (not part of the submitted P0 release)

The local action API counts unsigned draft exports against
`config/payment_p1_policy.json`, including native gas budgets. A SQLite
transaction reserves the budget before publication. Cancellation, restart or
failed export does not refund reserved budget. This is conservative local
issuance accounting, **not chain-confirmed spend or wallet authorization**.
The review displays the issuance policy and changes require a new confirmation.
Offline proofs/CLI review outputs remain evidence, not issued approvals.

Audit export and verification:

```sh
python3 scripts/payment_audit.py export /path/to/issuance-ledger.db --output /tmp/audit.json
python3 scripts/payment_audit.py verify /tmp/audit.json --expected-tip "0x…trusted-tip…"
```

Retain the trusted tip outside mutable local storage; a digest alone cannot
prove completeness or prevent a database owner from rewriting all history.
Never publish the local audit database or session records.

`PHAROS_SECONDARY_RPC_URL` optionally enables same-block read-only checks after
mandatory official MCP collection. Mismatch/unavailability blocks completion;
it never substitutes for MCP.

`scripts/payment_batch.py` supports 1–5 semicolon-separated **planning** rows
and JSON-quoted local memos. It can compare same-snapshot MCP proofs against
aggregate balances, but it does not allocate nonces, reserve batch allowance,
issue signing requests or implement batch confirmation. Do not present its
output as an executable batch.


## V2 interface and language

Use `python3 scripts/payment_skill_mcp.py` for stdio MCP. Initialize protocol
2025-03-26, then discover `parse_payment`, `plan_batch`, and `review_payment`.
Only read/prepare operations exist. Never treat returned evidence as confirmation.

The local parser accepts common English prefixes and exact number words, including
`I would like to send one USDC to alice.` and `zero point zero zero one PROS`.
Approximate amounts or unknown recipients require explicit user revision.
The P1 local workspace also supports 1–5 independent batch rows separated by
semicolons. Shared balances, fees, rolling unsigned allowance and nonce reservations
are checked before export. Batches are not atomic or sequentially simulated.

Optional local speech requires an installed offline model and runtime selected by
`PHAROS_ASR_MODEL` and `PHAROS_ASR_PYTHON`. The transcript is untrusted input:
show it to the user before copying it to a new payment revision.
