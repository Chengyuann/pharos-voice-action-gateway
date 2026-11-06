"""Guard the approved visual components against another accidental simplification."""
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_public_motion_components_are_present_and_not_force_hidden():
    html = (ROOT / "site/index.html").read_text()
    css = (ROOT / "site/product.css").read_text()
    for name in ["hero-orbit", "orbit-ring", "orbit-scan", "signal-rail",
                 "sandbox-waveform", "product-workspace", "mini-flow",
                 "feature-card", "motion-toggle"]:
        assert name in html, name
    assert "prefers-reduced-motion:reduce" in css
    assert 'data-motion=' in css
    for name in ["hero-orbit", "signal-rail", "product-workspace"]:
        assert not re.search(rf"\.{name}[^{{]*\{{[^}}]*display:\s*none", css)


def test_original_workspace_keeps_visuals_and_new_speech_controls():
    html = (ROOT / "web/payment-preview.html").read_text()
    css = (ROOT / "web/payment-preview.css").read_text()
    for name in ["hero-orbit", "signal-rail", "voice-console", "voice-waveform",
                 "flow-monitor", "mic-start", "mic-text", "voice-stop"]:
        assert name in html
    assert "compact-hero" not in html + css
    assert ".trust-strip,.hero-orbit,.signal-rail,.step-note{display:none" not in css


def test_presentation_scripts_parse():
    for name in ["product.js", "sandbox.js"]:
        subprocess.run(["node", "--check", str(ROOT / "site" / name)], check=True)


def test_public_font_assets_have_licenses():
    for name in ["barlow-condensed-700.ttf", "barlow-condensed-600-italic.ttf",
                 "manrope-400.ttf", "manrope-700.ttf", "ibm-plex-mono-400.ttf",
                 "LICENSE-Barlow-Condensed.txt", "LICENSE-Manrope.txt",
                 "LICENSE-IBM-Plex-Mono.txt"]:
        assert (ROOT / "site/fonts" / name).stat().st_size > 1000
