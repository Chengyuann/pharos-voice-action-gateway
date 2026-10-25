#!/usr/bin/env python3
"""Build an isolated, offline P1 evidence preview after full proof replay."""
from __future__ import annotations

import argparse
import base64
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import zipfile

from payment_batch_evidence import load_evidence, verify_evidence

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    "scripts/payment_batch.py", "scripts/payment_batch_evidence.py",
    "scripts/payment_chain.py", "scripts/payment_proof.py", "scripts/payment_ledger.py",
    "scripts/payment_actions.py", "scripts/payment_language.py", "scripts/payment_preview.py",
    "scripts/pharos_mcp.py", "scripts/payment_crosscheck.py", "scripts/evm_address.py",
    "scripts/pharos_voice_action_gateway.py", "scripts/build_p1_evidence.py",
    "config/pharos_mainnet_tokens.json", "config/payment_mainnet_policy.json",
    "config/payment_p1_policy.json", "config/payment_contacts.json", "web/p1-evidence.html",
]
REVIEW_FIELDS = {
    "id", "record_type", "kind", "created_at", "expires_at", "mode", "request_text",
    "answers", "status", "message", "plan", "questions", "payments", "payment",
    "items", "checks", "draft_allowed", "draft_label", "wallet", "network", "fee",
    "recorded", "boundary", "configuration_hash", "aggregate", "pending_nonce",
    "nonce_sequence", "integrity_hash",
}
ROW_FIELDS = {
    "id", "record_type", "created_at", "expires_at", "mode", "request_text", "answers",
    "status", "field", "question", "reason", "choices", "requires_rewrite", "message",
    "payment", "network", "wallet", "checks", "balance", "fee", "policy_decision",
    "draft_allowed", "draft_label", "configuration_hash", "recorded", "token",
    "prepared_transaction", "proof", "integrity_hash", "secondary_check",
}


def inspect_for_publication(value):
    review = value["review"]
    if set(review) != REVIEW_FIELDS or any(set(row) - ROW_FIELDS for row in review["items"]):
        raise ValueError("Unexpected private or unreviewed record fields")
    blocked_keys = {"authorization", "x-action-token", "action_id", "token_hash",
                    "password", "private_key", "secret", "cookie", "session_id", "api_key"}
    blocked_text = ("/Users/", "/home/", "file://", "ghp_", "gho_", "PRIVATE_KEY=", "Bearer ")
    def visit(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key.lower() in blocked_keys:
                    raise ValueError("Private credential/session field cannot enter evidence bundle")
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str) and any(marker in item for marker in blocked_text):
            raise ValueError("Private/local marker cannot enter evidence bundle")
    visit(value)


def source_manifest():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    return {
        "base_commit": commit, "working_tree_dirty": dirty,
        "files": {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
    }


def build(evidence_path, output):
    evidence = load_evidence(evidence_path)
    verification = verify_evidence(evidence)
    inspect_for_publication(evidence)
    output = Path(output).resolve()
    public = (ROOT / "site").resolve()
    if output == public or public in output.parents:
        raise ValueError("P1 preview must not modify the submitted P0 site")
    if output.exists() or output.with_suffix(".zip").exists():
        raise ValueError("Use a new output directory; existing previews are never overwritten")
    sources = source_manifest()
    evidence_json = json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False)
    payload = json.dumps({"evidence_json": evidence_json, "verification": verification, "sources": sources},
                         ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    # The embedded JSON is data, never markup or a script-closing delimiter.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    page = (ROOT / "web/p1-evidence.html").read_text()
    if page.count("__P1_EVIDENCE_DATA__") != 1 or page.count("__P1_LOGO__") != 1:
        raise ValueError("Evidence template markers are invalid")
    page = page.replace("__P1_EVIDENCE_DATA__", payload).replace(
        "__P1_LOGO__", base64.b64encode((ROOT / "assets/buidl-logo.png").read_bytes()).decode())
    readme = (
        "# P1 batch evidence preview\n\n"
        "Open index.html directly. This folder is a local publication candidate, not an Arena submission.\n\n"
        f"Captured: {evidence['captured_at']}\n\n"
        f"Rows: {verification['rows']}; block: {verification['block']}; "
        f"official MCP calls: {verification['calls']}; secondary comparisons: {verification['secondary_checks']}.\n\n"
        "In the source repository, verify with:\n\n"
        "```sh\npython3 scripts/payment_batch_evidence.py verify /path/to/evidence.json\n```\n\n"
        "File hashes are in MANIFEST.json. Source hashes identify the exact local implementation; "
        "a dirty working tree is not represented as a published release commit.\n\n"
        "Public addresses, amounts, memos and read-only RPC results are included. Review them before publication. "
        "The capture made no action confirmation, reservation, signature or broadcast. "
        "Offline verification proves internal consistency only, not chain authenticity or wallet authority. "
        "Independent row simulations do not establish sequential or atomic execution. "
        "Voice microphone/ASR/TTS and speaker authentication are not demonstrated by this evidence.\n"
    )
    files = {
        "index.html": page.encode(), "evidence.json": evidence_json.encode(),
        "verification.json": json.dumps(verification, indent=2).encode(),
        "SOURCES.json": json.dumps(sources, indent=2).encode(), "README.md": readme.encode(),
    }
    manifest = {"schema": "pharos-p1-evidence-bundle/1", "published": False,
                "arena_resubmitted": False, "capture_digest": evidence["digest"],
                "files": [{"path": name, "bytes": len(data), "sha256": sha256(data).hexdigest()}
                          for name, data in sorted(files.items())]}
    files["MANIFEST.json"] = json.dumps(manifest, indent=2).encode()
    output.mkdir(parents=True)
    for name, data in files.items():
        with (output/name).open("xb") as stream:
            stream.write(data)
    archive_path = output.with_suffix(".zip")
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return {"output": str(output), "archive": str(archive_path),
            "files": len(files), "verification": verification}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.evidence, args.output), indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
