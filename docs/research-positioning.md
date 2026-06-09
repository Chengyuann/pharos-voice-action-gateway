# Research Positioning

## Market Direction

Agent payment and agent wallet products are moving from simple "let agents pay" demos toward scoped authorization, policy enforcement, and auditability. For a voice-native on-chain agent, the core problem is harder because speech adds partial ASR, short pauses, barge-in, corrections, and ambiguous confirmations.

This project therefore focuses on a reusable safety primitive instead of another generic wallet UI.

## External Signals

- Google's Agent Payments Protocol (AP2) frames the core agent-payment trust gap as authorization, authenticity, and accountability. It introduces mandate-style proofs so an agent can prove that a user gave specific authority for a transaction.
- AP2 documentation also highlights "verifiable intent, not inferred action" as a core goal. This project adapts that idea to voice: a spoken command becomes a hashed voice mandate only after turn-taking commits the utterance.
- OWASP's AI Agent Security guidance recommends explicit approval for high-impact or irreversible actions, action previews before execution, risk-based autonomy boundaries, and clear audit trails. The project implements each of these as a local, testable Skill behavior.

## Differentiation

### 1. Voice-Native Mandate

The Skill creates a `voice_mandate` object that binds:

- `voice_hash`: hash of committed voice turns and turn-taking evidence
- `intent_hash`: hash of the structured action intent
- action scope: action type, chain id, token, amount, recipient
- expiration: short validity window for voice commands
- privacy flag: raw audio is not stored

This mirrors the industry movement toward explicit agent authorization receipts, but adapts it to voice and Pharos-style on-chain agents.

### 2. Policy Decision Record

Before any simulated or live transaction, the Skill evaluates:

- single-payment limit
- token allowlist
- trusted recipient allowlist
- voice mandate integrity
- no raw audio storage

The important behavior is that confirmation cannot bypass policy. A user can say `confirm execute`, but the action remains blocked if it exceeds limits or targets an untrusted address.

### 3. Readback Challenge

The Skill generates a phrase such as:

```text
PHAROS 8D76 A1B2C3
```

The phrase binds approval to a specific `action_id` and `mandate_hash`. This is designed for high-risk voice environments where a generic "yes" is not enough.

### 4. Audit Timeline

Each run produces a structured timeline:

1. voice events processed
2. barge-in detected, if any
3. intent committed
4. voice mandate created
5. policy evaluated
6. confirmation checked
7. simulated transaction or block reason

This gives a Pharos Agent Arena implementation a clean path to write only hashes and decisions on-chain while keeping raw speech local.

## Why This Fits Pharos

Pharos is positioned for AI Agent economy, on-chain payments, and scalable agent deployment. This Skill provides a missing middleware layer for voice agents that need to safely trigger on-chain actions:

- reusable as a Skill in Phase 1
- composable into a complete voice wallet / RealFi agent in Phase 2
- compatible with mock-first judging and future Pharos RPC/wallet integration
- privacy-preserving because it stores proof hashes rather than raw audio
