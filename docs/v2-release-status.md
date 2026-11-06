# V2 English product release candidate

Prepared, published and resubmitted November 6, 2026. **The three existing Arena tasks were updated; no duplicate tasks were created.**

## Delivered

- English blue-motion product homepage, restored orbit/coin hero, waveform, workflow components, spotlight interactions, scroll reveals, mobile layouts, task deep links, privacy page and 404.
- Interactive browser-only policy sandbox: exact example amounts, named limits, allowlist and time checks; no network calls or wallet output.
- Three newly recorded English 1080p walkthroughs with authorized MCY narration, burned captions, SRT and VTT.
- Low-volume background music: `Our Future` by Declan DP, attributed on the site and ducked approximately 27–28 LU below narration.
- Common English prefixes, exact number words, sentence punctuation and follow-up handling.
- Integrated P1: up to five independent payments, address checks, common-block reviews,
  shared amounts/fees, rolling unsigned allowance, hash-linked audit and durable nonce reservations.
- Optional microphone capture, 20-second PCM bound, offline Whisper adapter,
  explicit transcript import, cancellation and local English response playback.
- Read-only stdio MCP tools: parse payment, plan batch, review payment.
- Fresh November 6 native-payment proof at block 22244464, 10 official MCP calls.
- Public evidence for the October 25 batch and October 22 installed-Skill run.

## Verification

- 501 tests passed in 91.88 seconds; focused final media and component tests also passed.
- 12 final MCY source and encoded narration segments were detected as English and matched the normalized script exactly in local ASR checks. This is an automated intelligibility check, not human listening.
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
- Videos use new screencast recordings of actual product interactions plus current screenshots. Edited/retimed footage is explicitly labeled; no new live execution is inferred from an archived screen.
- Final durations:42.45,49.81,48.53seconds. English source scan and18-frame OCR found no Chinese; custom player controls are English regardless of browser locale.
- The installed-Skill trace is dated October 22. Foundry `cast` is not currently
  available on PATH in this environment, so that complete flow was not rerun today.
- No new rank, approval, payout or score is promised.

## Release decision

The source and website are released from `main`. The three existing Arena tasks were resubmitted from their original task pages on November 6 and are IN REVIEW. No duplicate tasks were created. Scores and approval remain pending.

No signing, broadcast, real funds movement, wallet edits or private voice-template
publication occurred.
