# V2 English product release candidate

Prepared November 6, 2026. **Local candidate, not published or resubmitted.**

## Delivered

- English product homepage, mobile layouts, task deep links, privacy page and 404.
- Interactive browser-only policy sandbox: exact example amounts, named limits, allowlist and time checks; no network calls or wallet output.
- Three new English-narrated 1080p walkthroughs, burned captions, SRT and VTT.
- Common English prefixes, exact number words, sentence punctuation and follow-up handling.
- Integrated P1: up to five independent payments, address checks, common-block reviews,
  shared amounts/fees, rolling unsigned allowance, hash-linked audit and durable nonce reservations.
- Optional microphone capture, 20-second PCM bound, offline Whisper adapter,
  explicit transcript import, cancellation and local English response playback.
- Read-only stdio MCP tools: parse payment, plan batch, review payment.
- Fresh November 6 native-payment proof at block 22244464, 10 official MCP calls.
- Public evidence for the October 25 batch and October 22 installed-Skill run.

## Verification

- 496 tests passed in 89.31 seconds after the final changes.
- 12 encoded narration segments passed local ASR comparison, minimum similarity 0.9823.
- All three complete MP4s decoded without errors.
- Published bundle allowlist and proof replay passed.
- Desktop 1440 and mobile 390/320 website widths had no horizontal overflow.
- Local workspace at 390 had no horizontal overflow.
- Verified transcript import requires new review; a changed transcript removes old approval.
- Offline ASR recognized a synthetic 2.83-second English recording in 11.58 seconds,
  including model startup. This is one smoke test, not a latency benchmark.

## Not complete / not claimed

- No current microphone capture or subjective listening test by the account owner.
- Voice is turn-based, not full-duplex continuous ASR or measured acoustic barge-in.
- Speaker Guard is not implemented: enrollment consent, model/license selection,
  encrypted template storage and replay/cloning evaluation remain necessary.
- The public website has a browser-only example sandbox and recorded evidence, not a live mainnet payment backend.
- Mainnet MCP sometimes timed out in local UI tests. Successful saved evidence is
  never used as a silent fallback.
- Videos use actual captured product states with animated framing; no new live execution is inferred from an archived screen.
- The installed-Skill trace is dated October 22. Foundry `cast` is not currently
  available on PATH in this environment, so that complete flow was not rerun today.
- No new rank, approval, payout or score is promised.

## Release decision

Review the local homepage and English films, then authorize source push/deployment
and replacement of the three existing Arena submissions. Do not create duplicates.
Resubmission re-enters the review queue. Keep historical P0 available in Git history.

No signing, broadcast, real funds movement, wallet edits or private voice-template
publication occurred.
