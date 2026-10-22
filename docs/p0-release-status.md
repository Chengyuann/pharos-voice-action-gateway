# P0 release status — October 22, 2026

**P0 implementation, publication and the three task submissions are complete.
All three Arena entries are IN REVIEW; approval and points remain pending.**

## Published release

- Evidence: https://chengyuann.github.io/pharos-voice-action-gateway/
- Repository: https://github.com/Chengyuann/pharos-voice-action-gateway
- First P0 release commit: `cf9d389d5d321106814674c091b1c6e093a4d817`
- First successful Pages run: `39748241047`
- First successful Linux CI run: `39748240990`
- Videos and captions: `site/media/`; all three public MP4 downloads match the
  reviewed hashes in `site/media/manifest.json`.

The earlier [nine-item audit](p0-audit-2026-10-22.md) is a historical checkpoint.

## Implemented and verified

| P0 area | Evidence |
|---|---|
| Official Chain MCP | Streamable HTTP sessions/cookies, read-only allowlist, bounded requests and transient-error retry |
| Network and assets | Pacific Mainnet 1672 / PROS; official USDC/WPROS code, symbol, decimals and balances checked |
| State consistency | Balance, code, metadata and simulation pinned to one block |
| Freshness reads | Pending nonce, gas estimate and gas price explicitly current, unpinned |
| Payment preparation | Nonce, gas budget, exact native/ERC-20 calldata and EIP-681; no signing or broadcasting |
| Human review | Action-revision confirmation followed by a fresh chain recheck |
| Lifecycle | Edits/cancellation revoke confirmation; late work cannot publish; draft completion is idempotent |
| Policy and audit | Spend limit, allowlist and time window; per-rule denials and local audit records |
| Installed Skills | Official Skill and local payment Skill installed through skills CLI for Codex |
| Actual Skill execution | skill_chain_flow.py runs official cast balance at the MCP block and requires agreement |
| Public evidence | Isolated static viewer, curated trace JSON and three narrated videos |

**Snapshot contract:** “all reads are pinned” was too broad in the original
plan. State and simulation are pinned; pending nonce and current fee quotes
are intentionally fresh. The official MCP fee tools do not offer a block
parameter. Both kinds of values are disclosed separately in the proofs.

## Real read-only runs

| Evidence | Collected | Block | Outcome |
|---|---|---:|---|
| site/proofs/pros-allow-live.json | October 21 | 20830064 | Allow review |
| site/proofs/wpros-allow-live.json | October 21 | 20830073 | Allow review |
| site/proofs/usdc-deny-live.json | October 21 | 20830085 | Insufficient token balance |
| site/proofs/pros-20261022-live.json | October 22, 16:09 Asia/Shanghai | 20911380 | Allow review after MCP recovery |
| site/proofs/installed-skill-flow.json | October 22, 16:33 Asia/Shanghai | 20913011 | Installed official Skill + MCP allow review |

The last run contains 10 official MCP calls, installed-file hashes, actual
Foundry cast balance output and a final decision gated on same-block balance
agreement. If MCP is unavailable, the runner stops; cast is not an RPC fallback.
This additional official-Skill gate belongs to the evidence runner, not the
local payment UI. The UI separately uses the official MCP payment checks.

The official ai.pharos.xyz/skill-engine page describes an installable skill
and gives npx skills add instructions. Installation and local execution close
the missing runtime evidence; they do not claim execution inside that site's
hosted chat interface or task acceptance by reviewers.

Saved samples are historical evidence, never wallet instructions. Public
sender inspection is not proof of ownership. No private key, signature,
broadcast or funds movement occurred. Offline replay establishes internal
consistency, not authenticated chain provenance.

## Videos

Policy: 54.20 seconds; Payment: 56.60 seconds; Composition: 59.97 seconds.
All are 1920×1080 H.264/AAC with MCY Chinese narration and captions, no presenter
or background music. Full decode and source/encoded narration checks passed.
Policy's isolated short-phrase ASR homophone error and successful contextual
recheck are retained in the local delivery reports.

The user requested completion after the audit, authorizing publication.
Subjective listening approval is not claimed. The composition film explains
October 21 evidence; the newer installed-Skill run is available in the page's
evidence selector and JSON, not retroactively claimed as footage in the film.
No video points or task approval are promised.

## Verification

The final presubmission regression suite passed **314 tests in 71.02 seconds**.
Initial public Linux CI also passed after adding the new PROS proof. Both legacy smoke suites, Skill validation,
proof replay, a published-source privacy-marker scan and all public video
hashes passed. The final suite includes the installed-Skill replay tests.

```sh
python3 -m pytest scripts -q
python3 scripts/run_demo_tests.py
python3 scripts/run_pharos_demo_tests.py
python3 scripts/payment_proof.py verify site/proofs/pros-20261022-live.json
python3 scripts/skill_chain_flow.py --verify site/proofs/installed-skill-flow.json
python3 scripts/build_arena_bundle.py
```

The hosted MCP intermittently returns HTTP 503. Two new complete runs succeeded
after recovery. Transient errors use bounded backoff and fail closed; no
permanent service recovery or guaranteed uptime is claimed.

## Arena submission receipt — October 22, 2026

The user completed login and explicitly authorized a final check followed by
submission. The authenticated account was `Chengyuann`, with no existing
submissions before these three were created. Public links, video hashes,
source commit and CI were rechecked before clicking Submit for review.

| Task | Confirmed status | Submitted release |
|---|---|---|
| Policy Gatekeeper | IN REVIEW | `ffe94cc7eb058dbb041aa21f6ff8278269d7c0ed` |
| Natural-Language Payment Agent | IN REVIEW | `ffe94cc7eb058dbb041aa21f6ff8278269d7c0ed` |
| Skill + Chain MCP Flow | IN REVIEW | `ffe94cc7eb058dbb041aa21f6ff8278269d7c0ed` |

Each task page displayed “Submitted — it's in the review queue.” A refreshed
My work dashboard independently listed exactly these three entries, all
IN REVIEW, checked at **09:36 UTC / 17:36 Asia/Shanghai**. No resubmit was
clicked. The visible UI did not expose separate submission IDs; the account
and task-page URLs identify the entries.

The receipt and screenshot are saved locally in
`output/p0/release/arena-submitted-receipt.json` and
`output/p0/release/arena-submitted-my-work.jpg`; no account-session data is
published. Awarded points were still zero at the check. Reward-wallet settings
were not changed. Reviewer feedback, approval and any points remain external
outcomes, not completed or guaranteed by this P0 delivery.

DoraHacks BUIDL 44805 is separate from Agent Arena registration.

## Deployment safety

GitHub Actions uploads **only site/** after explicit file-allowlist and proof/
media checks. Local state, reports, credentials, private MCY references and
SQLite databases are not published. The interactive product remains loopback-only:

```sh
PHAROS_MCP_READ_ADDRESS="0x…public-address…" \
  python3 scripts/policy_gatekeeper_server.py --port 8766
```

Do not expose that server publicly. The public site is recorded evidence,
not an interactive payment service or connected wallet.
