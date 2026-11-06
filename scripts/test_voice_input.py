import io
import wave
import pytest
from voice_input import validate_audio, runtime_config
from http.client import HTTPConnection
from test_payment_preview_http import server, request


def wav_data(seconds=1, rate=16000, channels=1, width=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(b"\0" * int(seconds * rate * channels * width))
    return buf.getvalue()


def test_bounded_wav():
    assert validate_audio(wav_data()) == 1


@pytest.mark.parametrize("kwargs", [
    {"seconds": 21}, {"seconds": .1}, {"rate": 48000}, {"channels": 2}, {"width": 1},
])
def test_rejects_unbounded_or_unsupported_audio(kwargs):
    with pytest.raises(ValueError):
        validate_audio(wav_data(**kwargs))


def test_rejects_truncated_audio():
    with pytest.raises(ValueError):
        validate_audio(wav_data()[:-200])


def test_asr_is_opt_in(monkeypatch):
    monkeypatch.delenv("PHAROS_ASR_PYTHON", raising=False)
    monkeypatch.delenv("PHAROS_ASR_MODEL", raising=False)
    assert runtime_config()["available"] is False


def test_transcription_api_does_not_create_payment(server, monkeypatch):
    monkeypatch.setattr("voice_input.transcribe", lambda raw: {
        "text": "Pay alice 1 USDC", "requires_text_review": True, "audio_retained": False,
    })
    client = HTTPConnection("127.0.0.1", server.server_port)
    client.request("POST", "/api/voice/transcribe", wav_data(), {"Content-Type": "audio/wav"})
    response = client.getresponse()
    assert response.status == 200
    assert b"requires_text_review" in response.read()
    client.close()
    assert not server.payment_report_dir.exists()


def test_voice_api_rejects_cross_origin(server):
    client = HTTPConnection("127.0.0.1", server.server_port)
    client.request("POST", "/api/voice/transcribe", wav_data(),
                   {"Content-Type": "audio/wav", "Origin": "https://attacker.example"})
    response = client.getresponse()
    assert response.status == 403
    response.read()
    client.close()


def test_voice_config_and_assets(server):
    status, _, data = request(server, "GET", "/api/voice/config")
    assert status == 200 and data["local_only"] and not data["audio_retained"]
    for file in ["live-voice.js", "voice-capture.js"]:
        assert request(server, "GET", "/assets/" + file)[0] == 200
