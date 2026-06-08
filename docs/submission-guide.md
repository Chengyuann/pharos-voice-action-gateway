# DoraHacks / Pharos Submission Guide

## Project

```text
Pharos Voice Action Gateway
```

This project upgrades Local Duplex Voice Gateway into a Skill-to-Agent module for Pharos. It turns full-duplex voice streams into safe on-chain actions: the Skill waits until the user has actually finished speaking, extracts a structured intent, prepares a Pharos transaction preview or session proof, and blocks high-risk actions until the user explicitly confirms.

## GitHub URL

```text
https://github.com/Chengyuann/local-duplex-voice-gateway
```

## Skill 信息

- Skill 名称：Pharos Voice Action Gateway
- 标签：`Pharos`、`AgentSkill`、`MCP`、`voice`、`full-duplex`、`onchain`、`wallet`、`proof`
- 阶段定位：Phase 1 reusable Skill module，可在 Phase 2 扩展成完整 voice wallet / RealFi agent

## What It Builds

1. `Voice intent commit`
   - Uses the existing turn-taking gateway to emit `commit_turn` only after the utterance is complete.
   - Avoids submitting half-spoken voice commands.

2. `Explicit confirmation gate`
   - Payment, contract write and proof actions require explicit phrases such as `confirm` or `确认执行`.
   - Unconfirmed high-risk actions are blocked.

3. `Pharos transaction adapter`
   - Produces a Pharos/EVM transaction preview in mock mode.
   - Returns deterministic tx/proof hashes for demo evidence without private keys.

4. `On-chain voice session proof`
   - Generates `voice_hash`, `intent_hash`, `action_id` and proof payload.
   - Can later be wired to a proof registry contract.

5. `AgentSkill/MCP interface`
   - Exposes five tool schemas: `process_voice_events`, `prepare_onchain_action`, `confirm_action`, `submit_transaction`, `write_session_proof`.

## 验证命令

```bash
python scripts/run_demo_tests.py
python scripts/run_pharos_demo_tests.py
python scripts/duplex_voice_gateway.py demo/duplex_conversation.jsonl
python scripts/duplex_voice_gateway.py demo/short_pause_continuation.jsonl
python scripts/pharos_voice_action_gateway.py demo/pharos_payment_confirmed.jsonl
python scripts/pharos_voice_action_gateway.py demo/pharos_payment_pending.jsonl
python scripts/pharos_voice_action_gateway.py demo/pharos_session_proof_confirmed.jsonl
```

## DoraHacks 简介

```text
Pharos Voice Action Gateway is a reusable Skill-to-Agent module that makes voice-driven on-chain agents safer. It combines a local full-duplex voice turn-taking gateway with a Pharos action layer: the Skill waits for a complete voice turn, extracts a structured intent, prepares a Pharos transaction preview or voice-session proof, and blocks high-risk actions until the user explicitly confirms. The demo is mock-first, requires no private key, and outputs deterministic tx/proof hashes that can later be wired to a Pharos wallet or proof registry contract.
```

## One-liner

```text
A confirmation-gated voice-to-onchain Skill for Pharos agents: commit complete voice turns, prepare payment/proof actions, and block unsafe transactions before signing.
```

## Demo Script

```text
1. Run the original full-duplex voice gateway demo to show commit_turn and interrupt_tts.
2. Run pharos_payment_confirmed.jsonl to show a 0.02 PHRS voice payment, explicit confirmation and simulated tx hash.
3. Run pharos_payment_pending.jsonl to show that the same high-risk payment is blocked without confirmation.
4. Run pharos_session_proof_confirmed.jsonl to show voice_hash, intent_hash and proof_payload generation.
5. Explain that mock mode is safe for judging; wallet mode can later connect to Pharos RPC/signers for live Agent Arena deployment.
```
