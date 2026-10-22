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

This is a repository-local extension of the Pharos Skill Engine workflow, not
an installed hosted SkillEngine service or a claim that `cast` was executed.
It applies the upstream transaction prechecks and gas-budget rule
`ceil(estimate × 1.2)` to actual Chain MCP data in the same request.

- Upstream: `PharosNetwork/pharos-skill-engine`
- Pinned commit: `a873387c9e34e9ae8e71360d43dc33329a335a24`
- References: `references/transaction.md` (gas calculation / transaction
  prechecks), `assets/networks.json`, `assets/tokens.json`
- Local token config: `config/pharos_mainnet_tokens.json`
- Local policy: `config/payment_mainnet_policy.json`
- Handler: `scripts/payment_chain.py`

The default limit is in units **of each token**, not USD or a rolling budget.
Only configured recipients pass. Mixed-case addresses are rejected because
this dependency-free implementation does not check EIP-55. Do not strip an
unchecked mixed-case checksum on the user's behalf.
