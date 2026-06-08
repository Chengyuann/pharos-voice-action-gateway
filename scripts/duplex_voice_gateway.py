#!/usr/bin/env python3
"""Local Duplex Voice Gateway.

This script consumes a JSONL stream that mimics local ASR/VAD/TTS events and
emits turn-taking decisions for a voice Agent. It is intentionally dependency
free so the core Skill can be validated without downloading speech models.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


DEFAULT_EOU_SILENCE_MS = 700
SHORT_PAUSE_MS = 280
MIN_COMMIT_CHARS = 3


COMMIT_HINTS = re.compile(r"(吧|吗|呢|一下|可以了|就这样|讲完了|没了|谢谢|please|thanks|that'?s all)[。！？!?]?$", re.I)
CONTINUE_HINTS = re.compile(r"(然后|还有|等一下|不是|我想|另外|呃|嗯|就是|比如|because|and|also|wait)$", re.I)
INTERRUPT_HINTS = re.compile(r"(等一下|停|暂停|打断|不是|先别|wait|stop|hold on|interrupt)", re.I)


@dataclass
class InputEvent:
    t: float
    type: str
    text: str = ""
    speech: bool = False


@dataclass
class OutputEvent:
    t: float
    action: str
    reason: str
    text: str = ""
    latency_ms: int = 0


@dataclass
class GatewayState:
    committed_turns: list[str] = field(default_factory=list)
    output_events: list[OutputEvent] = field(default_factory=list)
    latest_text: str = ""
    latest_speech_t: Optional[float] = None
    latest_event_t: Optional[float] = None
    tts_active: bool = False
    tts_text: str = ""
    interrupts: int = 0


def load_events(path: Path) -> list[InputEvent]:
    events: list[InputEvent] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on line {line_no}: {exc}") from exc
        events.append(
            InputEvent(
                t=float(raw.get("t", 0.0)),
                type=str(raw.get("type", "")),
                text=str(raw.get("text", "")),
                speech=bool(raw.get("speech", False)),
            )
        )
    return sorted(events, key=lambda item: item.t)


def process_events(events: list[InputEvent], eou_silence_ms: int = DEFAULT_EOU_SILENCE_MS) -> GatewayState:
    state = GatewayState()
    for event in events:
        state.latest_event_t = event.t

        if event.type in {"asr_partial", "asr_final"}:
            handle_asr(event, state)
            continue

        if event.type == "silence":
            handle_silence(event, state, eou_silence_ms)
            continue

        if event.type == "tts_start":
            state.tts_active = True
            state.tts_text = event.text
            emit(state, event.t, "tts_started", "agent started speaking", event.text)
            continue

        if event.type == "tts_end":
            state.tts_active = False
            state.tts_text = ""
            emit(state, event.t, "tts_finished", "agent finished speaking", event.text)
            continue

        emit(state, event.t, "ignore", f"unknown event type: {event.type}", event.text)

    return state


def handle_asr(event: InputEvent, state: GatewayState) -> None:
    text = normalize_text(event.text)
    if not text:
        emit(state, event.t, "listen", "empty ASR partial")
        return

    state.latest_text = text
    state.latest_speech_t = event.t

    if state.tts_active:
        if should_interrupt(text):
            state.tts_active = False
            state.interrupts += 1
            emit(state, event.t, "interrupt_tts", "user barged in while TTS was active", text)
        else:
            emit(state, event.t, "listen", "user speech detected during TTS, waiting for stronger interruption cue", text)
        return

    if event.type == "asr_final" and should_commit(text):
        commit_turn(state, event.t, "ASR final with complete intent")
    else:
        emit(state, event.t, "listen", "user is speaking", text)


def handle_silence(event: InputEvent, state: GatewayState, eou_silence_ms: int) -> None:
    if not state.latest_text or state.latest_speech_t is None:
        emit(state, event.t, "listen", "silence without active utterance")
        return

    silence_ms = int(max(0.0, event.t - state.latest_speech_t) * 1000)

    if silence_ms < SHORT_PAUSE_MS:
        emit(state, event.t, "listen", "very short pause", state.latest_text, silence_ms)
        return

    if should_hold(state.latest_text, silence_ms, eou_silence_ms):
        emit(state, event.t, "hold", "pause looks like continuation, not end of turn", state.latest_text, silence_ms)
        return

    if silence_ms >= eou_silence_ms or should_commit(state.latest_text):
        commit_turn(state, event.t, f"silence {silence_ms}ms crossed EOU threshold")
    else:
        emit(state, event.t, "hold", "waiting for EOU threshold", state.latest_text, silence_ms)


def should_interrupt(text: str) -> bool:
    if INTERRUPT_HINTS.search(text):
        return True
    return len(text) >= 4 and not CONTINUE_HINTS.search(text)


def should_commit(text: str) -> bool:
    if len(strip_punctuation(text)) < MIN_COMMIT_CHARS:
        return False
    if CONTINUE_HINTS.search(text):
        return False
    if COMMIT_HINTS.search(text):
        return True
    if re.search(r"[。！？!?]$", text):
        return True
    return len(text) >= 8


def should_hold(text: str, silence_ms: int, eou_silence_ms: int) -> bool:
    if CONTINUE_HINTS.search(text):
        return True
    if len(strip_punctuation(text)) <= 4 and silence_ms < max(1200, eou_silence_ms):
        return True
    return False


def commit_turn(state: GatewayState, t: float, reason: str) -> None:
    text = state.latest_text.strip()
    if not text:
        return
    state.committed_turns.append(text)
    emit(state, t, "commit_turn", reason, text)
    state.latest_text = ""
    state.latest_speech_t = None


def emit(state: GatewayState, t: float, action: str, reason: str, text: str = "", latency_ms: int = 0) -> None:
    state.output_events.append(OutputEvent(t=t, action=action, reason=reason, text=text, latency_ms=latency_ms))


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_punctuation(text: str) -> str:
    return re.sub(r"[\s，。！？,.!?;；:：、]+", "", text)


def write_markdown_report(state: GatewayState, source: Path, output_path: Optional[Path] = None) -> Path:
    if output_path is None:
        output_path = Path.cwd() / f"duplex_voice_report_{source.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

    lines = [
        "# Local Duplex Voice Gateway Report",
        "",
        "## Summary",
        "",
        f"- Source: `{source}`",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Committed turns: {len(state.committed_turns)}",
        f"- TTS interruptions: {state.interrupts}",
        "",
        "## Committed Turns",
        "",
    ]
    if state.committed_turns:
        lines.extend(f"{idx}. {text}" for idx, text in enumerate(state.committed_turns, 1))
    else:
        lines.append("No completed user turn was committed.")

    lines.extend(["", "## Event Timeline", "", "| t | action | latency | text | reason |", "|---:|---|---:|---|---|"])
    for event in state.output_events:
        text = event.text.replace("|", "\\|")
        reason = event.reason.replace("|", "\\|")
        lines.append(f"| {event.t:.2f} | `{event.action}` | {event.latency_ms}ms | {text} | {reason} |")

    lines.extend(
        [
            "",
            "## Local AI PC Notes",
            "",
            "- This demo processes local ASR/VAD/TTS events only.",
            "- In production, connect local ASR, VAD, EOU and TTS adapters.",
            "- OpenVINO can be used to accelerate ASR/VAD/EOU on Intel CPU/GPU/NPU.",
            "- A <=35B local model can consume `commit_turn` events as the Agent brain.",
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def write_json_report(state: GatewayState, output_path: Optional[Path] = None) -> Path:
    if output_path is None:
        output_path = Path.cwd() / f"duplex_voice_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    data: dict[str, Any] = {
        "committed_turns": state.committed_turns,
        "interrupts": state.interrupts,
        "events": [asdict(event) for event in state.output_events],
    }
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def print_summary(state: GatewayState, report_path: Path) -> None:
    print("=" * 64)
    print("Local Duplex Voice Gateway")
    print("=" * 64)
    print(f"Committed turns: {len(state.committed_turns)}")
    print(f"TTS interruptions: {state.interrupts}")
    for turn in state.committed_turns:
        print(f"- commit_turn: {turn}")
    for event in state.output_events:
        if event.action == "interrupt_tts":
            print(f"- interrupt_tts at {event.t:.2f}s: {event.text}")
    print(f"Report: {report_path}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run local duplex voice turn-taking gateway on JSONL events.")
    parser.add_argument("input", help="JSONL event file")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--output", "-o")
    parser.add_argument("--eou-silence-ms", type=int, default=DEFAULT_EOU_SILENCE_MS)
    args = parser.parse_args(argv)

    try:
        source = Path(args.input).expanduser()
        events = load_events(source)
        state = process_events(events, args.eou_silence_ms)
        output = Path(args.output).expanduser() if args.output else None
        report_path = write_json_report(state, output) if args.format == "json" else write_markdown_report(state, source, output)
        print_summary(state, report_path)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
