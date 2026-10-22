# P0 release status — October 22, 2026

**Local implementation is ready for verification; the public release and Arena
submissions are NOT complete.**

**Re-audit, October 22:** 296 tests and all delivery hashes passed again, but
two new official MCP probes returned HTTP 503 (last probe 15:54 Asia/Shanghai).
Remote main still points to October 8 and no Pages deployment was verified.
See [the nine-item P0 audit](p0-audit-2026-10-22.md); SkillEngine runtime
acceptance and literal all-read block pinning must not be marked complete.

## What is implemented

| P0 item | Evidence |
|---|---|
| Official Chain MCP | `scripts/pharos_mcp.py`; Streamable HTTP initialize/session/cookies, read-only allowlist, bounded I/O and tool count |
| Network separation | Atlantic demo/RPC retained; `mcp` mode enforces Pacific Mainnet 1672 / PROS |
| Official assets | PROS, USDC and WPROS; ERC-20 code, symbol, decimals and balance checked on chain |
| Payment preparation | Pending nonce, gas budget, gas price, exact native/ERC-20 calldata and EIP-681 URI |
| Same-flow composition | `collect_chain_state` → `decide_mainnet_payment`; Skill Engine gas formula and prechecks consume MCP results |
| Human review | UI summary → action-bound confirmation → fresh MCP recheck → unsigned draft |
| Lifecycle safety | Edit/correct/cancel invalidate confirmation; late work cannot publish; duplicate draft returns its prior result |
| Audit and proof | Per-action local records; exact call trace, digest and offline replay verifier |
| Reviewer evidence | `site/`: isolated static viewer with curated JSON; no local reports, sessions, signing or live RPC endpoints |
| Skill package | `skills/pharos-payment-review/SKILL.md`, validated locally |

## Historical mainnet evidence

All samples were collected on **October 21, 2026**, not today. They are
historical evidence, never current wallet instructions.

A new read-only PROS review was collected on **October 22, 2026 at 16:09
Asia/Shanghai**, block **20911380**, after the official MCP recovered. It
replayed successfully and remains an expired evidence candidate, not payment
authority: `site/proofs/pros-20261022-live.json`.

| Proof | Recorded block | Outcome |
|---|---:|---|
| `site/proofs/pros-allow-live.json` | 20830064 | Allow review |
| `site/proofs/wpros-allow-live.json` | 20830073 | Allow review |
| `site/proofs/usdc-deny-live.json` | 20830085 | Deny: insufficient token balance |

The first two inspect a publicly visible third-party account. No ownership or
wallet authority is claimed. No signature or broadcast occurred.

The fixture policy cases separately show spend-limit, recipient and time-window
denials with the mock executor uncalled. Hashes detect inconsistent changes;
they are not digital signatures. The offline verifier cannot distinguish a
wholly fabricated but internally consistent transcript from genuine MCP output.

## Acceptance and release blockers

- **Arena registration: unconfirmed.** On October 21 the current Chrome session
  reached a GitHub OAuth consent page for `Pharos Agent Arena`, owned by
  `janeshbalchandani`, requesting read-only email access for `Chengyuann`.
  Authorization was not granted. An absent OAuth grant or an absent leaderboard
  entry does not prove that no historical registration exists.
- **DoraHacks is separate.** BUIDL 44805 exists; that is not proof of registration
  in the Season of AI Agent Arena.
- **Public deployment: pending approval.** The static evidence folder must be
  hosted and verified at a public URL. It is a recorded evidence viewer, not the
  interactive local application. Do not expose `policy_gatekeeper_server.py`
  to the internet; it is intentionally loopback-only.
- **Source publication: pending approval.** The public repository previously
  pointed at the October 8 commit. Local uncommitted work is not a public build.
- **Task submission: pending approval and public URLs.** Use three task-specific
  descriptions under `docs/submissions/`. No Arena submission has been made in
  this workflow; no review outcome or points are promised.
- **Video:** MCY narrated, Chinese-captioned local films are ready in
  `outputs/pharos-mcy/` (54.20 s / 56.60 s / 59.97 s); user listening review,
  upload and submission remain pending. No presenter was used.
  Three raw silent screen recordings were captured on October 22:
  Policy Gatekeeper (28.16 s), payment clarification → live MCP → draft
  (74.00 s), and the recorded Skill+Chain evidence viewer (30.60 s).
  All decode successfully and sampled frames were inspected; the composition video is historical evidence
  playback, not a new live MCP collection.
  Source/encoded narration and full video QA are documented in the delivery;
  Policy's short final phrase has a disclosed contextual ASR recheck.
  Neither raw nor narrated output is claimed as an Arena-approved +50 video.
- **Hosted SkillEngine validation:** the repository-local extension applies the
  upstream rules and composes real Chain MCP data; installation/execution inside
  `ai.pharos.xyz` has not been demonstrated. Reviewers decide whether this
  packaging satisfies the task. Do not claim a hosted SkillEngine run.

## Local verification

On October 22: 296 tests passed, both legacy smoke suites passed, three curated
live proofs replayed, and the Skill package validator passed. Browser checks
covered all three task tabs, each denial scenario, live payment clarification
and confirmed draft creation. Desktop 1440px and mobile 390px showed no
page-level horizontal overflow. Static-site console checks were clear after
adding the local favicon.

```sh
python3 -m pytest scripts -q
python3 scripts/run_demo_tests.py
python3 scripts/run_pharos_demo_tests.py
python3 scripts/payment_proof.py verify site/proofs/pros-allow-live.json
python3 scripts/payment_proof.py verify site/proofs/wpros-allow-live.json
python3 scripts/payment_proof.py verify site/proofs/usdc-deny-live.json
python3 scripts/build_arena_bundle.py
```

To inspect the static site, serve only `site/` on loopback:

```sh
python3 -m http.server 8790 --bind 127.0.0.1 --directory site
```

The interactive product remains local:

```sh
PHAROS_MCP_READ_ADDRESS="0x…public-address…" \
  python3 scripts/policy_gatekeeper_server.py --port 8766
```

Select **Official Chain MCP**. The configured address is not a connected wallet.
No signer or broadcaster exists. Every real wallet must revalidate the full
request independently.

## Public release procedure — after explicit approval

1. Review the source diff and publish a release commit to the GitHub repository.
   Do not stage `.agent/`, `output/`, `.playwright-cli/`, `reports/`, credentials
   or local SQLite databases. The curated `site/proofs/` samples are the only
   public records selected for release.
2. Enable GitHub Pages with the GitHub Actions source. The prepared
   `.github/workflows/pages.yml` validates the curated file allowlist and
   replays proof JSON before uploading **only `site/`**.
3. Open the workflow's returned public URL without authentication. Check all
   task tabs, downloads and mobile layout. Record the deployment and source
   commit; no public URL is assumed or fabricated here.
4. Resolve the Arena OAuth permission request separately. Check the actual
   authenticated profile and existing submissions before creating any entry.
5. Review the three final submission texts and media URLs, then submit only
   the explicitly authorized entries. Record actual status/IDs after submit.
