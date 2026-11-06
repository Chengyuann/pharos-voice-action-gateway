"""Bounded loopback-only audio transcription. No audio persists after the worker exits."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import wave

MAX_SECONDS = 20
SAMPLE_RATE = 16000
MAX_BYTES = SAMPLE_RATE * 2 * MAX_SECONDS + 128
_SLOT = threading.Lock()


def runtime_config():
    python = os.environ.get("PHAROS_ASR_PYTHON", "")
    model = os.environ.get("PHAROS_ASR_MODEL", "")
    available = bool(python and Path(python).is_file() and model and Path(model).is_dir())
    return {"available": available, "language": "en", "max_seconds": MAX_SECONDS,
            "local_only": True, "audio_retained": False,
            "message": "Local speech recognition ready." if available else
            "Set PHAROS_ASR_PYTHON and PHAROS_ASR_MODEL to enable offline recognition."}


def validate_audio(raw):
    if not isinstance(raw, bytes) or not 44 <= len(raw) <= MAX_BYTES:
        raise ValueError("Audio exceeds the 20-second limit.")
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
                raise ValueError("Use 16 kHz mono PCM16 WAV audio.")
            frames = wav.getnframes()
            if not SAMPLE_RATE // 4 <= frames <= SAMPLE_RATE * MAX_SECONDS:
                raise ValueError("Record between 0.25 and 20 seconds.")
            if len(wav.readframes(frames)) != frames * 2:
                raise ValueError("The audio file is incomplete.")
    except (wave.Error, EOFError) as exc:
        raise ValueError("Invalid WAV audio.") from exc
    return frames / SAMPLE_RATE


def transcribe(raw):
    duration = validate_audio(raw)
    if not runtime_config()["available"]:
        raise ValueError("Local speech recognition is not configured.")
    if not _SLOT.acquire(blocking=False):
        raise ValueError("Another recording is being transcribed. Try again when it finishes.")
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="pharos-audio-") as directory:
            path = Path(directory) / "input.wav"
            path.write_bytes(raw)
            env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                       TOKENIZERS_PARALLELISM="false")
            worker = Path(__file__).with_name("voice_worker.py")
            result = subprocess.run(
                [os.environ["PHAROS_ASR_PYTHON"], str(worker), str(path), os.environ["PHAROS_ASR_MODEL"]],
                capture_output=True, text=True, timeout=90, env=env,
            )
            if result.returncode or len(result.stdout) > 8192:
                raise ValueError("Local transcription failed. Use typed input or record again.")
            payload = json.loads(result.stdout)
            text = payload.get("text")
            if not isinstance(text, str) or len(text) > 500:
                raise ValueError("Speech result is not a valid payment instruction.")
            return {"text": text.strip(), "audio_seconds": duration,
                    "processing_ms": round((time.monotonic() - started) * 1000),
                    "source": "local_whisper", "audio_retained": False,
                    "requires_text_review": True}
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise ValueError("Transcription did not complete. No payment was created.") from exc
    finally:
        _SLOT.release()
