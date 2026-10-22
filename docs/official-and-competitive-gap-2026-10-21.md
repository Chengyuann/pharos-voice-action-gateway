# Official and Competitive Gap Review

Date: 2026-10-21

This review compares the current local Voice Action Gateway with:

- Pharos Agent Arena requirements for Policy Gatekeeper, Natural-Language
  Payment Agent, and Skill + Chain MCP Flow.
- Public repositories from `edycutjong/pharos-beacon`,
  `Rzbyte/pharos-season-of-ai`, and `Beltran12138/pharos-arena`.
- The official Pharos Chain MCP documentation and repository.

Only behavior visible in source, tests, proof files, deployed pages, or the
official task pages is counted. README claims without corresponding public
artifacts are not treated as complete evidence.

## Executive conclusion

The project is differentiated in conversational action safety, but it is not
yet a live voice product and is not optimized for the Arena scoring model.

- **Strongest area:** action revision, correction, cancellation, stale-result
  protection, confirmation binding, privacy boundaries, and focused tests.
- **Largest official gap:** no real SkillEngine + Chain MCP composition.
- **Largest payment gap:** native PHRS only; no official token-contract
  validation, contact/address book, nonce, ERC-20 calldata, EIP-681 request, or
  full on-chain dry run.
- **Largest competitive policy gap:** no rolling spend window and no
  hash-chained, independently verifiable decision log. These exceed the
  official minimum, which only requires an audit log of decisions.
- **Largest delivery gap:** no public proof link, Arena submission, task video,
  explorer proof, or public deployment.

The shortest route is to finish and submit the three existing task-shaped
products before adding microphone capture or Speaker Guard.

## Official requirement scorecard

### Policy Gatekeeper

| Official requirement | Current state | Verdict |
|---|---|---|
| Config with spend limit, allowlist, time window | Per-action amount, token and recipient allowlists, UTC window | Pass |
| Every request returns allow/deny and deciding rule | Structured policy decision and per-rule output | Pass |
| One deny per rule type; denied action never executes | Reproducible amount, recipient and time denials; mock executor protected | Pass locally |
| Audit log of all decisions | Immutable individual JSON reports and recent-record view | Pass locally; no public proof link |
| Reviewer-openable proof | Localhost only | Missing |
| Repo/submission/video | Local dirty worktree; no Arena submission or task video | Missing |

Technical behavior is close to the minimum task, but the current artifact is
not submission-complete. The sampled leading implementation additionally has
per-transaction and rolling-window spend limits, a run lock, a hash-chained
JSONL audit, proof verification, SkillEngine packaging, Chain MCP execution,
and Atlantic transaction evidence.

### Natural-Language Payment Agent

| Official requirement | Current state | Verdict |
|---|---|---|
| Parse amount, token, recipient; follow up on ambiguity | Parses one strict English payment and asks for missing fields; explicit amount correction | Partial |
| Validate recipient, token contract, sufficient balance | Address and balance checks; configured native token only | Partial: no token-contract check |
| Human-readable summary before unsigned transaction | Review UI with checks, fee and policy | Pass |
| Never signs or sends automatically | No signer or broadcaster | Pass |
| Reviewer-openable proof | Localhost only | Missing |

Public competitor implementations support contacts, ENS-related clarification,
multiple candidate recipients/amounts, native and ERC-20 assets, on-chain
symbol/decimals/code checks, checksum checks, gas, nonce, memo, EIP-681 or RLP
wallet requests, and Chain MCP dry runs. One implementation supports up to ten
payments, `each`/`split`, nonce ordering, aggregate balance checks, SkillEngine
commands, and replayable proof verification.

### Skill + Chain MCP Flow

| Official requirement | Current state | Verdict |
|---|---|---|
| Skill output depends on live Chain MCP data in the same flow | Payment output can depend on direct JSON-RPC; MCP exists only as schemas | Missing |
| End-to-end from one user request | Voice/payment UI is end-to-end locally, but not Skill + MCP composition | Missing for this task |
| Diagram/write-up of call order | General architecture diagrams exist | Partial, but no real composed flow |
| Demo with real data through both tools | No SkillEngine + Chain MCP run or trace | Missing |

This is the clearest zero-to-one gap. The official hosted MCP is public,
mainnet, keyless and rate-limited. It provides chain/block reads, balances,
nonce, `eth_call`, gas estimation, logs, receipts, and signed-raw-transaction
broadcasting. The current direct RPC adapter does not satisfy the official
composition requirement.

## Competitive product comparison

Scores are directional, based on the sampled public artifacts.

| Dimension | This project | Leading sampled builds |
|---|---:|---:|
| Fixture turn-taking and action correction | 4/5 | 1/5 |
| Action cancellation and stale-result safety | 5/5 | 2–3/5 |
| Local privacy and no-key default | 5/5 | 3–4/5 |
| Natural-language payment breadth | 2/5 | 4–5/5 |
| Official token and chain validation | 2/5 | 5/5 |
| Chain MCP integration | 0/5 | 5/5 |
| SkillEngine composition | 1/5 (schema/format intent) | 4–5/5 |
| Policy depth | 3/5 | 5/5 |
| Tamper-evident/replayable proof | 2/5 | 4–5/5 |
| Public deployment and reviewer access | 0/5 | 5/5 |
| Task breadth | 1–3 task-shaped flows | 11–17 approved tasks for leaderboard leaders |
| Tests | Strong: 204 local tests | Strong, plus live/proof tests in sampled leaders |

### Where this project is ahead

In the sampled public payment and policy implementations, this project has a
more explicit conversational action lifecycle:

- input revision invalidates prior confirmation;
- cancellation wins over stale client state;
- late review/draft work cannot publish;
- duplicate drafts are controlled;
- restart revokes old action sessions;
- correction preserves unchanged payment fields;
- voice fixture turn-taking, barge-in and short-pause behavior are visible in
  the same product.

This can be a real product differentiator once it sits on top of official
Pharos data, transaction preparation and live audio. Today the voice input is
fixture replay, so it is not yet proof of a production voice experience and is
not a substitute for the official task evidence.

### Where competitors are ahead

1. **They use the official tools for real.**
   Public code contains Streamable HTTP MCP clients, rate-limit handling,
   pinned-block reads, Chain MCP traces, and SkillEngine references.
2. **They publish proof, not only tests.**
   Proof JSON, audit logs, explorer transactions, verification commands and
   deployed browser demos let reviewers inspect outcomes.
3. **They cover the complete payment handoff.**
   Token metadata, nonce, gas, ERC-20 calldata, EIP-681/RLP, multiple payments,
   contacts, ambiguity and aggregate balances are handled.
4. **They build for the leaderboard.**
   As of 2026-10-21, the official board showed the leader at 1,745 points from
   17 tasks and second place at 1,465 points from 11 tasks. A single polished
   product cannot close that scoring gap.

## Product gap versus broader agent-payment products

The market direction is moving beyond text parsing toward wallet-integrated,
multi-rail agent payment infrastructure. That raises the baseline for:

- standard signing requests rather than custom JSON drafts;
- explicit scopes, limits and revocation;
- merchant/payee identity and token-contract verification;
- proof of execution and reconciliation;
- multiple payment rails and machine-to-machine payment protocols.

This project should not try to match a full payment platform now. Its defensible
position is a **voice-first safety and review layer** for Pharos agents. To make
that credible, the voice layer must control a real official Chain MCP payment
preparation flow rather than a parallel demo adapter.

## Recommended priority

### P0: Convert existing work into Arena points

1. Connect the Payment Preview to the official hosted Chain MCP.
2. Pin all reads for one review to one block.
3. Read nonce, native/token balances, contract code, token symbol/decimals, gas
   estimate and gas price through MCP.
4. Add native PROS plus official USDC/WPROS ERC-20 preparation.
5. Emit an unsigned wallet request with nonce, gas, calldata and EIP-681 where
   applicable.
6. Package the decision step as an actual SkillEngine-format skill.
7. Produce proof JSON with tool names, arguments, block, results and verifier.
8. Deploy a public read-only demo and submit Policy, Natural-Language Payment,
   and Skill + Chain MCP Flow separately.
9. Record one focused video per task.

These three official tasks are worth 300 base points and up to 150 additional
video points. They should not be submitted as one combined build.

### P1: Close policy and language breadth

- Rolling spend window with concurrency-safe state.
- Hash-chained append-only audit and verifier.
- Contacts/address book and guided ambiguity questions.
- Checksum, zero/burn/token-contract recipient checks.
- Memo, multiple payment and aggregate-balance handling.
- Independent direct-RPC cross-check only as secondary evidence, not as a
  replacement for Chain MCP.

### P2: Return to the voice moat

- Explicit microphone start/stop and bounded local audio ingestion.
- Local ASR with partial/final events wired to the current action revision.
- Measured end-of-utterance and barge-in latency.
- Opt-in 1:1 Speaker Guard with replay/cloning tests.
- Voice remains a risk signal and interaction channel; wallet confirmation
  remains independent.

## Strategic recommendation

Use a hybrid strategy:

- **Score first:** submit three narrow, fully evidenced official tasks.
- **Differentiate second:** show the same payment flow being corrected and
  cancelled by voice without stale approval reuse.
- **Expand third:** add a few easy read-only MCP tasks only if leaderboard rank
  matters.

Do not spend the next milestone on Speaker Guard. It improves differentiation
but closes none of the current official acceptance gaps.

## Sources checked

- https://pharos-agent-arena.vercel.app/tasks/policy-gatekeeper
- https://pharos-agent-arena.vercel.app/tasks/natural-language-payment-agent
- https://pharos-agent-arena.vercel.app/tasks/skill-chain-mcp-flow
- https://pharos-agent-arena.vercel.app/leaderboard
- https://docs.pharos.xyz/tooling-and-infrastructure/mcp
- https://github.com/PharosNetwork/pharos-mcp
- https://github.com/PharosNetwork/pharos-skill-engine
- https://github.com/edycutjong/pharos-beacon
- https://github.com/Rzbyte/pharos-season-of-ai
- https://github.com/Beltran12138/pharos-arena
