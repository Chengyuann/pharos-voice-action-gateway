"""Static evidence and release-bundle checks."""

from pathlib import Path
import json
import subprocess
import zipfile

import pytest

from build_arena_bundle import OUTPUT, PUBLIC_FILES, main, validate
from payment_proof import verify_proof
from skill_chain_flow import verify_flow
from payment_batch_evidence import verify_evidence


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


def test_static_site_is_isolated_and_references_every_evidence_file():
    html = (SITE / "index.html").read_text()
    js = (SITE / "evidence.js").read_text()
    assert "Content-Security-Policy" in html
    assert "Unsigned only. You approve in your wallet." in html
    assert 'href="http://127.0.0.1' not in html and 'href="http://localhost' not in html
    assert "/api/" not in html + js
    for proof in (SITE / "proofs").glob("*.json"):
        assert proof.stem in js


@pytest.mark.parametrize("path", sorted((SITE / "proofs").glob("*-live.json")))
def test_published_live_proofs_replay(path):
    value = json.loads(path.read_text())
    if path.name == "batch-p1-live.json":
        result = verify_evidence(value)
    else:
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
    for name in ["evidence.js", "video-controls.js"]:
        subprocess.run(["node", "--check", str(SITE / name)], check=True)


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


def test_published_installed_skill_flow_replays():
    result = verify_flow(json.loads((SITE / "proofs/installed-skill-flow.json").read_text()))
    assert result["verified"] is True
    assert result["decision"] == "allow_review"
    assert result["signed"] is result["broadcast"] is False

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


def test_release_language_is_english():
    import re
    for path in [*SITE.glob("*.html"), *SITE.glob("*.js"), *SITE.joinpath("media").glob("*.srt")]:
        assert not re.search("[\u4e00-\u9fff]", path.read_text()), path.name
    for file in (SITE / "media").glob("*.vtt"):
        assert file.read_text().startswith("WEBVTT")


def test_english_player_labels_do_not_depend_on_browser_locale():
    html = (SITE / "index.html").read_text()
    js = (SITE / "video-controls.js").read_text()
    assert 'aria-label="English video controls"' in html
    assert "video.controls = false" in js
    assert '"Play"' in js and '"Pause"' in js
    assert '"Full screen"' in js


def test_background_music_is_credited_without_publishing_the_source_mp3():
    manifest = json.loads((SITE / "media/manifest.json").read_text())
    html = (SITE / "index.html").read_text()
    assert manifest["background_music"] is True
    assert manifest["music"]["title"] == "Our Future"
    assert manifest["music"]["artist"] == "Declan DP"
    assert "Our Future" in html and "Declan DP" in html
    for url in ["https://license.declandp.info", "https://links.al/CaH",
                "https://links.al/youtube"]:
        assert url in html
    assert not list(SITE.rglob("*.mp3"))
    assert all(item["background_music"] == "our-future-declan-dp"
               for item in manifest["files"])
