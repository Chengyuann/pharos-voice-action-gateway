"""Static evidence and release-bundle checks."""

from pathlib import Path
import json
import subprocess
import zipfile

import pytest

from build_arena_bundle import OUTPUT, PUBLIC_FILES, main, validate
from payment_proof import verify_proof


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


def test_static_site_is_isolated_and_references_every_evidence_file():
    html = (SITE / "index.html").read_text()
    js = (SITE / "evidence.js").read_text()
    assert "Content-Security-Policy" in html
    assert "No signatures. No funds moved." in html
    assert "127.0.0.1" not in html and "localhost" not in html
    assert "/api/" not in html + js
    for proof in (SITE / "proofs").glob("*.json"):
        assert proof.stem in js


@pytest.mark.parametrize("path", sorted((SITE / "proofs").glob("*-live.json")))
def test_published_live_proofs_replay(path):
    value = json.loads(path.read_text())
    result = verify_proof(value.get("proof", value))
    assert result["verified"] is True
    assert result["signed"] is result["broadcast"] is False
    assert result["chain_authenticity_verified"] is False


@pytest.mark.parametrize("name,expected", [
    ("policy-allow.json", "allow"),
    ("policy-deny-amount.json", "deny"),
    ("policy-deny-recipient.json", "deny"),
    ("policy-deny-time.json", "deny"),
])
def test_policy_evidence_has_decision_and_no_onchain_execution(name, expected):
    value = json.loads((SITE / "proofs" / name).read_text())
    assert value["policy_decision"]["outcome"] == expected
    assert value["submission"]["receipt"]["execution_attempted"] is False
    if expected == "deny":
        assert value["submission"]["receipt"]["mock_executor_called"] is False


def test_language_evidence_requires_explicit_answer():
    value = json.loads((SITE / "proofs" / "language-demo.json").read_text())
    assert value["first"]["status"] == "needs_details"
    assert value["first"]["field"] == "amount"
    assert value["resolved"]["payment"]["amount"] == value["answer"]["amount"]


def test_javascript_syntax():
    subprocess.run(["node", "--check", str(SITE / "evidence.js")], check=True)


def test_three_reviewed_videos_are_public_with_matching_hashes():
    from hashlib import sha256
    manifest = json.loads((SITE / "media/manifest.json").read_text())
    assert len(manifest["files"]) == 3
    assert manifest["private_reference_included"] is False
    for item in manifest["files"]:
        data = (SITE / "media" / item["file"]).read_bytes()
        assert sha256(data).hexdigest() == item["sha256"]
        assert len(data) == item["bytes"]
        assert (SITE / "media" / item["subtitles"]).is_file()
    html = (SITE / "index.html").read_text()
    assert 'id="walkthrough"' in html and "controls" in html

def test_bundle_has_allowlist_only_and_valid_manifest():
    manifest = validate()
    assert {item["path"] for item in manifest} == {
        path.relative_to(ROOT).as_posix() for path in PUBLIC_FILES
    }
    assert main() == 0
    with zipfile.ZipFile(OUTPUT) as archive:
        names = set(archive.namelist())
        assert names == {item["path"] for item in manifest} | {"MANIFEST.json"}
        saved = json.loads(archive.read("MANIFEST.json"))
        assert saved["schema"] == "pharos-arena-p0-bundle/1"
        assert len(saved["files"]) == len(manifest)
