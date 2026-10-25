from hashlib import sha256
import json
from html.parser import HTMLParser
import zipfile

import pytest

from build_p1_evidence import build, ROOT
from test_payment_batch_evidence import evidence, rehash


def write_evidence(tmp_path, value=None):
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(value or evidence()))
    return path


def test_standalone_bundle_is_bounded_verifiable_and_does_not_touch_p0(tmp_path):
    before = {str(p): sha256(p.read_bytes()).hexdigest() for p in (ROOT/"site").rglob("*") if p.is_file()}
    output = tmp_path/"preview"
    result = build(write_evidence(tmp_path), output)
    assert result["verification"]["verified"] and result["files"] == 6
    manifest = json.loads((output/"MANIFEST.json").read_text())
    for item in manifest["files"]:
        raw = (output/item["path"]).read_bytes()
        assert len(raw) == item["bytes"] and sha256(raw).hexdigest() == item["sha256"]
    page = (output/"index.html").read_text()
    assert "__P1_" not in page and "fetch(" not in page
    assert "Not submitted to Arena" in page and "connect-src 'none'" in page
    with zipfile.ZipFile(result["archive"]) as archive:
        assert set(archive.namelist()) == {p.name for p in output.iterdir()}
    assert before == {str(p): sha256(p.read_bytes()).hexdigest() for p in (ROOT/"site").rglob("*") if p.is_file()}


def test_embedded_download_preserves_original_integer_precision(tmp_path):
    class EmbeddedData(HTMLParser):
        active = False
        value = ""
        def handle_starttag(self, tag, attrs):
            if tag == "script" and dict(attrs).get("id") == "evidence-data":
                self.active = True
        def handle_endtag(self, tag):
            if tag == "script":
                self.active = False
        def handle_data(self, text):
            if self.active:
                self.value += text
    output = tmp_path/"preview"
    build(write_evidence(tmp_path), output)
    parser = EmbeddedData()
    parser.feed((output/"index.html").read_text())
    data = json.loads(parser.value)
    assert data["evidence_json"] == (output/"evidence.json").read_text()
    assert "new Blob([text]" in (output/"index.html").read_text()


def test_script_like_memo_remains_json_text(tmp_path):
    value = evidence('Pay alice 0.1 USDC memo "</script><img src=x onerror=alert(1)>"; Pay alice 0.2 USDC')
    output = tmp_path/"preview"
    build(write_evidence(tmp_path,value),output)
    page = (output/"index.html").read_text()
    assert "</script><img" not in page
    assert "\\u003c/script\\u003e" in page


@pytest.mark.parametrize("marker", ["/Users/private/secret", "Bearer abc", "ghp_example"])
def test_private_markers_block_publication(tmp_path, marker):
    value = evidence(f'Pay alice 0.1 USDC memo "{marker}"; Pay alice 0.2 USDC')
    with pytest.raises(ValueError, match="Private"):
        build(write_evidence(tmp_path,value),tmp_path/"preview")


def test_extra_session_fields_block_publication(tmp_path):
    value = evidence()
    value["review"]["items"][0]["session_id"] = "test"
    rehash(value)
    with pytest.raises(ValueError, match="fields"):
        build(write_evidence(tmp_path,value),tmp_path/"preview")


def test_existing_output_and_p0_directory_are_never_overwritten(tmp_path):
    path = write_evidence(tmp_path)
    output = tmp_path/"preview"
    output.mkdir()
    with pytest.raises(ValueError, match="never overwritten"):
        build(path, output)
    with pytest.raises(ValueError, match="P0"):
        build(path, ROOT/"site"/"p1")
