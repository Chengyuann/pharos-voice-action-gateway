# Pharos Voice Action Gateway

## Short Description

A voice-native mandate and policy layer for Pharos agents: commit complete voice turns, generate verifiable payment/proof mandates, and block unsafe transactions before signing.

## Project Description

Pharos Voice Action Gateway is a reusable Skill-to-Agent module that makes voice-driven on-chain agents safer. Voice agents are risky when they submit half-spoken commands, miss interruptions, or execute payments before the user has clearly confirmed the intent. This project solves that by combining a local full-duplex turn-taking gateway with a Pharos action layer, then adding a voice-native mandate, policy decision record, readback challenge, and audit timeline.

The Skill consumes streaming ASR/VAD/TTS events, emits stable turn-taking actions such as `listen`, `hold`, `commit_turn`, and `interrupt_tts`, then converts committed voice turns into structured Pharos actions. High-risk actions such as payment, contract write, or voice-session proof require explicit confirmation before they can proceed.

The current implementation is mock-first for safe hackathon judging. It does not store private keys and does not broadcast real transactions by default. Instead, it produces Pharos/EVM transaction previews, deterministic mock tx hashes, and proof payloads containing `voice_hash`, `intent_hash`, `mandate_hash`, and `action_id`. These artifacts can later be connected to a Pharos wallet, RPC adapter, or proof registry contract for Agent Arena deployment.

## Why It Matters

AI agents need reliable execution boundaries before they can safely control wallets or on-chain workflows. Text agents already have approval flows, but voice agents add extra failure modes: short pauses, barge-in, speech correction, partial ASR, and accidental confirmations. This Skill provides a reusable safety layer for voice-native Pharos agents.

## Core Features

- Voice intent commit: only complete utterances become agent intents.
- Barge-in handling: user interruption stops TTS before the agent continues.
- Explicit confirmation gate: high-risk actions require `confirm`, `approve`, or `确认执行`.
- Voice mandate: creates a verifiable mandate hash that binds voice evidence, intent, action scope, recipient, amount and expiry.
- Policy decision record: evaluates amount limits, token allowlists, trusted recipients and raw-audio privacy before submission.
- Readback challenge: binds human approval to a specific action id and mandate hash.
- Confirmation cannot bypass policy: unsafe transactions remain blocked even when the user says `confirm`.
- Pharos transaction preview: payment and proof actions are represented as EVM-compatible action previews.
- On-chain voice session proof: generates `voice_hash`, `intent_hash`, `mandate_hash`, `action_id`, and proof payload.
- MCP/AgentSkill interface: exposes `process_voice_events`, `prepare_onchain_action`, `confirm_action`, `evaluate_voice_policy`, `submit_transaction`, and `write_session_proof`.

## Demo Commands

```bash
python scripts/run_demo_tests.py
python scripts/run_pharos_demo_tests.py
python scripts/pharos_voice_action_gateway.py demo/pharos_payment_confirmed.jsonl
python scripts/pharos_voice_action_gateway.py demo/pharos_payment_pending.jsonl
python scripts/pharos_voice_action_gateway.py demo/pharos_payment_policy_blocked.jsonl
python scripts/pharos_voice_action_gateway.py demo/pharos_session_proof_confirmed.jsonl
```

## Demo Scenarios

1. Confirmed voice payment:
   - User says: `send 0.02 PHRS to 0x1111111111111111111111111111111111111111`
   - Agent asks for confirmation.
   - User says: `confirm execute`
   - Skill returns a simulated tx/proof hash.

2. Blocked voice payment:
   - User says: `帮我转账 0.05 PHRS 到 0x2222222222222222222222222222222222222222`
   - User never confirms.
   - Skill blocks submission with `blocked_by_confirmation_gate`.

3. Voice session proof:
   - User asks to record the voice session proof on chain.
   - User confirms.
   - Skill returns a proof payload that can later be written to a Pharos proof registry contract.

4. Policy-blocked payment:
   - User says: `send 0.50 PHRS to 0x9999999999999999999999999999999999999999`
   - User says: `confirm execute`
   - Skill still blocks the action because it exceeds the voice payment limit and targets an untrusted recipient.

## Future Agent Arena Path

The Phase 2 Agent can become a full voice wallet / RealFi agent:

- Connect mock transaction adapter to a Pharos RPC provider and wallet signer.
- Deploy a `VoiceSessionProofRegistry` smart contract.
- Store proof hashes on Pharos while keeping raw speech local.
- Add policies for payment limits, trusted recipients, and multi-step voice approvals.
- Turn voice mandates into signed on-chain authorization receipts.
- Use the Skill as the safety middleware between a voice model and a Pharos agent wallet.

## Repository

```text
https://github.com/Chengyuann/pharos-voice-action-gateway
```
