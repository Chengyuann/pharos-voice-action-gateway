# P1 Release Preparation

Status: local release candidate, October 25, 2026. Not pushed, deployed,
merged into main or resubmitted to Arena.

## What Was Checked

The review covered the batch parser, action lifecycle, common-block MCP reads,
shared issuance/nonce reservations, proof replay and the evidence export path.
The intended deployment remains a single user's loopback service, not a public
multi-tenant payment backend.

Three issues were addressed:

1. A slow batch checked the 60-second limit only after collecting all rows.
   Every row now inherits one monotonic deadline, including optional secondary
   RPC reads. Session cleanup does not open a new request after expiry.
2. The draft constructor checked each row's integrity but did not explicitly
   compare its prepared transaction with the replayed MCP proof. It now rejects
   a disagreement. Aggregate snapshots also compare the block timestamp.
3. A browser can round JSON integers larger than its safe-integer range.
   The offline preview preserves the evidence JSON text for display/download
   rather than regenerating it from parsed JavaScript numbers.

## Live Read-only Evidence

Two separate captures inspected the already-used public address
`0x6ea606ec600f47411bf56df699cf695a0b0e9071`. No ownership claim is made.
The recipient was the configured example address
`0x1111111111111111111111111111111111111111`.

| Capture | Block | Rows | MCP calls | Secondary comparisons | Result |
|---|---:|---:|---:|---:|---|
| Official MCP only | 21189882 | 2 | 19 | 0 | Ready at capture |
| Official MCP + configured RPC | 21189980 | 2 | 19 | 12 | Ready at capture; comparisons matched |

Both requests were native PROS: 0.000001 and 0.000002. The proposed nonce
sequence was 28, 29. These are historical observations, not wallet instructions.
The runs made no action confirmation, no issuance/nonce reservation, no
signature and no broadcast. They do not demonstrate ERC-20 batching, sequential
simulation, live microphone recognition or speaker authentication.

Offline replay recomputes row proofs, aggregate balances, gas budget, proposed
nonces, displayed checks and status. It checks internal consistency, not
authenticated provider provenance. The secondary RPC may share infrastructure
with the primary MCP service.

## Reproduce

Use a verified public address that is appropriate for inspection. Recipient
aliases and the policy are local configuration; do not treat example contacts
as actual payees.

```sh
python3 scripts/payment_batch_evidence.py collect \
  'Pay alice 0.000001 PROS; Pay alice 0.000002 PROS' \
  --sender 0xPUBLIC_ADDRESS \
  --output output/p1/new-batch-capture.json

python3 scripts/payment_batch_evidence.py verify \
  output/p1/new-batch-capture.json

python3 scripts/build_p1_evidence.py \
  output/p1/new-batch-capture.json \
  --output output/p1/new-release-preview
```

`PHAROS_SECONDARY_RPC_URL` enables the existing secondary check. Failure does
not fall back to demo data. An incomplete capture remains saved for diagnosis,
but the builder refuses to turn it into a complete successful evidence page.
Existing files/directories are never overwritten.

The output directory contains `index.html`, `evidence.json`,
`verification.json`, `SOURCES.json`, `MANIFEST.json` and `README.md`, with a ZIP
alongside it. The HTML embeds its data and logo and needs no backend or network
request. Open it directly in a browser. The download retains exact JSON
integers and the file manifest records SHA-256 hashes.

Source hashes identify the implementation used to validate/build the package.
A dirty working tree is labeled as such, never passed off as a published
release commit. Captures and bundles stay under ignored `output/p1/`; no action
tokens, local ledger databases or private paths belong in a public release.

## Publication Decision

The P0 `site/` directory and its 22-file baseline remain unchanged. P1 uses
a separate template and builder; the builder refuses to write inside `site/`.
The existing curated P0 bundle still validates.

The next external action requires a publication decision: publish P1 at a
separate versioned destination, then decide whether to update an existing Arena
submission. No resubmission or claim of extra competition points is automatic.

## Final Verification

- Full regression: **439 tests passed in 89.82 seconds**.
- Both legacy smoke suites and the 28-file curated P0 evidence bundle passed.
- All 22 P0 site file hashes match the retained baseline.
- Browser QA covered Payments, Evidence and Request trace at 320, 390, 768,
  1280 and 1600 pixels: no overflow, one visible panel, logo loaded.
- Keyboard arrow navigation switched tabs correctly. Console: no errors or
  warnings.
- The JSON downloaded through the browser matched the bundled original
  byte-for-byte and passed the independent verifier afterward.
- The new live evidence covers two native PROS rows only. ERC-20 batches,
  adversarial network timing and wallet execution were not live-tested.
