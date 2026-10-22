#!/usr/bin/env python3
"""Validate and package reviewer evidence without local reports or credentials."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import zipfile

from payment_proof import verify_proof
from pharos_voice_action_gateway import strict_json_loads


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
OUTPUT = DIST / "pharos-arena-p0-evidence.zip"
PUBLIC_FILES = [
    ROOT / "site" / ".nojekyll",
    ROOT / "site" / "index.html",
    ROOT / "site" / "evidence.css",
    ROOT / "site" / "evidence.js",
    ROOT / "site" / "icon.svg",
    ROOT / "site" / "media" / "manifest.json",
    *[ROOT / "site" / "media" / (name + suffix)
      for name in ["01-policy-gatekeeper-mcy", "02-payment-agent-mcy", "03-skill-chain-mcp-mcy"]
      for suffix in [".mp4", ".srt"]],
    *[ROOT / "site" / "proofs" / name for name in [
        "policy-allow.json", "policy-deny-amount.json", "policy-deny-recipient.json",
        "policy-deny-time.json", "language-demo.json", "pros-allow-live.json",
        "wpros-allow-live.json", "usdc-deny-live.json", "pros-20261022-live.json",
    ]],
    ROOT / "docs" / "p0-release-status.md",
    *sorted((ROOT / "docs" / "submissions").glob("*.md")),
    ROOT / "skills" / "pharos-payment-review" / "SKILL.md",
]


def validate() -> list[dict]:
    if len(PUBLIC_FILES) != len(set(PUBLIC_FILES)) or any(not path.is_file() for path in PUBLIC_FILES):
        raise ValueError("Arena bundle file list is incomplete or duplicated")
    if len(PUBLIC_FILES) > 40:
        raise ValueError("Arena bundle file list is unexpectedly large")
    actual_site_files = {p for p in (ROOT / "site").rglob("*") if p.is_file()}
    allowed_site_files = {p for p in PUBLIC_FILES if ROOT / "site" in p.parents}
    if actual_site_files != allowed_site_files:
        raise ValueError("Unapproved or missing files in the public site directory")
    site_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in PUBLIC_FILES
        if path.suffix in {".html", ".css", ".js", ".md"}
    )
    forbidden = [
        "X-Action-Token", "_actions.sqlite3", "reports/payment_preview",
        "file:///Users/", "/Users/bytedance/", "PRIVATE_KEY=", "gho_",
    ]
    found = [value for value in forbidden if value in site_text]
    if found:
        raise ValueError("Public bundle contains private/local markers: " + ", ".join(found))
    media = strict_json_loads((ROOT / "site/media/manifest.json").read_text())
    approved_media = {p.name for p in PUBLIC_FILES if p.suffix == ".mp4"}
    if {item["file"] for item in media.get("files", [])} != approved_media:
        raise ValueError("Public media manifest does not match the allowlist")
    for item in media["files"]:
        data = (ROOT / "site/media" / item["file"]).read_bytes()
        if len(data) != item["bytes"] or sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("Public video does not match its reviewed checksum")
    if "localhost" in site_text or "127.0.0.1" in site_text:
        # Local commands in the release-status document are deliberate, but the
        # static page itself must not direct reviewers to a loopback proof URL.
        page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        if "localhost" in page or "127.0.0.1" in page:
            raise ValueError("Static evidence page contains a loopback URL")

    manifest = []
    for path in PUBLIC_FILES:
        data = path.read_bytes()
        limit = 10_000_000 if path.suffix == ".mp4" else 2_000_000
        if len(data) > limit:
            raise ValueError(f"{path.relative_to(ROOT)} exceeds its public file limit")
        relative = path.relative_to(ROOT).as_posix()
        if path.parent == ROOT / "site" / "proofs":
            value = strict_json_loads(data.decode("utf-8"))
            if path.name.endswith("-live.json"):
                verify_proof(value.get("proof", value))
            elif not isinstance(value, dict):
                raise ValueError(f"{relative} must contain an object")
        manifest.append({"path": relative, "bytes": len(data), "sha256": sha256(data).hexdigest()})
    return manifest


def main() -> int:
    manifest = validate()
    DIST.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in PUBLIC_FILES:
            archive.write(path, path.relative_to(ROOT).as_posix())
        encoded = json.dumps({
            "schema": "pharos-arena-p0-bundle/1",
            "created_by": "scripts/build_arena_bundle.py",
            "files": manifest,
            "boundaries": [
                "recorded evidence, not a live payment service",
                "no signature or broadcast",
                "offline replay verifies self-consistency, not chain provenance",
            ],
        }, indent=2).encode("utf-8")
        archive.writestr("MANIFEST.json", encoded)
    print(f"Built {OUTPUT.relative_to(ROOT)} with {len(manifest)} validated files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
