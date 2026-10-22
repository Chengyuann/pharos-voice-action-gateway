# Arena submission draft — Natural-Language Payment Agent

Status: **published, not submitted**. Arena login and final submit confirmation remain pending.

## Description

The payment agent converts a bounded English instruction into a fully prepared
unsigned Pharos request. It supports PROS, USDC and WPROS on Pacific Mainnet and
asks for clarification when an amount, asset or recipient is ambiguous.

Examples: `Pay alice 0.001 PROS`, `Send 1 or 2 USDC to alice`, and `Send $1 to
alice`. Exact configured contacts are resolved; names and `.eth` inputs are not
guessed or silently resolved. The default Alice/Bob contacts are placeholders.

Official Chain MCP checks the chain, balances, contract code, token symbol and
decimals, pending nonce, gas and read-only transfer simulation. Amount arithmetic
uses integers; excess precision is rejected.

The local UI shows a human-readable review before creating the unsigned draft.
Confirmation belongs to one server-side action revision. A fresh check is
required when the request, nonce, balance, fee or policy changes. Cancellation
prevents late work from publishing. The app never requests keys, signs or sends.

## Evidence mapping

- Parser: `scripts/payment_language.py`
- On-chain checks: `scripts/payment_chain.py`
- Lifecycle: `scripts/payment_actions.py`
- Local UI: `/payment-preview`, **Official Chain MCP** mode
- Public evidence tab: `#payment`
- Clarification: `site/proofs/language-demo.json`
- Native success: `site/proofs/pros-allow-live.json`
- ERC-20 success: `site/proofs/wpros-allow-live.json`
- Balance denial: `site/proofs/usdc-deny-live.json`
- Tests: `scripts/test_payment_chain.py`

The original samples date from October 21, 2026. A new successful PROS
review at block 20911380 was collected on October 22 and is also public. Successful samples inspect
a third-party public address without ownership claims. They are historical
review candidates, not signed transactions or current payment authority.

## Output and limitations

The interactive flow returns chain ID, sender, target, value, calldata, pending
nonce, gas limit, gas price and an EIP-681 URI after explicit confirmation.
The wallet must independently recheck these fields.

One payment only. No batching, scheduling, ENS resolution or EIP-55 validation.
Mixed-case addresses are rejected. The static proof page does not accept
payments or simulate live chain data.

## Submission fields

- Proof URL: https://chengyuann.github.io/pharos-voice-action-gateway/#payment
- Repo URL: https://github.com/Chengyuann/pharos-voice-action-gateway
- Video URL: https://chengyuann.github.io/pharos-voice-action-gateway/media/02-payment-agent-mcy.mp4

The proof page displays recorded evidence, not a live payment service.
