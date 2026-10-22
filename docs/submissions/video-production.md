# Task videos — raw capture and narration plan

## MCY narrated delivery — October 22, 2026

The user selected **MCY pure narration, no presenter**. Three local 1920×1080
H.264/AAC films with Chinese captions are now under `outputs/pharos-mcy/`:

- `01-policy-gatekeeper-mcy.mp4` — 54.20 seconds.
- `02-payment-agent-mcy.mp4` — 56.60 seconds.
- `03-skill-chain-mcp-mcy.mp4` — 59.97 seconds.

Separate 48 kHz mono WAVs, SRTs, timelines, a Remotion source archive, license
notes and QA are in the same directory. No music or digital-human track was
used. Private MCY reference audio was neither copied nor uploaded.

Full decode, picture dimensions, black-frame, silence and loudness checks
passed. Local Whisper-small checked source and encoded narration. Policy's
isolated final phrase showed homophone errors; a contextual recheck scored
0.9615 and is documented with the original failed isolated transcript.
Subjective listening approval remains the user's review; no uploads or Arena
submissions were performed.

The raw-capture notes and earlier narration drafts below are historical.

The original raw recordings are **not final narrated submissions**. They remain
under `output/p0/videos/`; that folder is Git-ignored. The completed MCY cuts
above supersede this earlier plan for presenter choice and final TTS timing.
The original plan called for
captions and encoded-audio QA.

| Raw capture | Duration | What it proves |
|---|---:|---|
| `policy-gatekeeper-raw.webm` | 28.16 s | Live local inspector: allow and three independent policy denials |
| `payment-agent-raw.webm` | 74.00 s | Live local clarification, actual official MCP reads, confirmation and unsigned draft |
| `skill-chain-flow-raw.webm` | 30.60 s | Historical evidence viewer: successful ERC-20 trace and a fail-closed trace |

All three are 1440 × 1050, 25 fps VP8 WebM, silent. Full decode with ffmpeg
passed. A brief automation pause in the payment capture needs trimming.
The raw files are source evidence, not polished competition-ready videos.

## 1. Policy Gatekeeper narration draft

“This is the Pharos Policy Gatekeeper. No wallet is connected. An ordinary
request passes the configured spend limit, token and recipient allowlists,
and UTC time window. Only the local mock is called. Now increase the amount:
the spend limit denies it. Change the recipient: the allowlist denies it.
Move outside the time window: the time rule denies it. Each result records
the deciding rule and whether the executor was called. Approval never
overrides a failed policy.”

Shots: context and no-wallet label → Allow → each deny → JSON audit.
Keep the executor flag and deciding rule visible.

## 2. Natural-language Payment Agent narration draft

“This request contains two amounts, so the agent asks rather than guesses.
After an explicit answer, it reads the official Pharos Chain MCP. It checks
Pacific Mainnet, balances, token metadata when needed, and a read-only transfer
simulation. The review shows the sender, full recipient, asset and fee budget.
The inspected address is public and read-only, not proof of wallet ownership.
Confirmation belongs to this action version. The server reads the chain again
before producing nonce, gas and calldata in an unsigned request. Editing or
cancelling invalidates that confirmation. Nothing is signed or sent.”

Shots: ambiguous input → follow-up → explicit amount → live review →
confirmation → new checks → unsigned fields. Trim idle waiting; do not splice
a fixture in place of the live result or hide the no-ownership disclaimer.

## 3. Skill + Chain MCP narration draft

“This view contains historical evidence, not a live wallet. The payment Skill
consumes the actual recorded Chain MCP values in the same flow. The trace shows
chain and block checks, balances, contract metadata, simulation, pending nonce
and gas. Pinned state is separated from current fee and nonce calls. The Skill
applies policy and the upstream one-point-two gas safety factor to those
results. A failed balance or simulation prevents an unsigned candidate. The
offline verifier replays calls and recomputes the decision; it proves
self-consistency, not authenticated chain provenance.”

Shots: evidence boundary → allow → MCP call list → raw trace → denial →
local verifier output. Do not claim a hosted `ai.pharos.xyz` execution.

## Final assembly

- Recompose to 1920 × 1080, 30 fps; keep product controls and full addresses
  legible. Do not stretch the 1440 × 1050 recordings.
- Use the selected narration; derive scene timing from final audio.
- Add speech captions and brief on-screen task labels.
- Use no unlicensed music and no invented endorsement or point totals.
- Decode the encoded final MP4, verify captions against speech and inspect
  rendered frames. Publish only after reviewing the final artifacts.
- Public hosting and final video URLs remain pending.
