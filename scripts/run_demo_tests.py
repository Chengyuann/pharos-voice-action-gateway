#!/usr/bin/env python3
"""Smoke tests for Local Duplex Voice Gateway."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from duplex_voice_gateway import load_events, process_events


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    state = process_events(load_events(root / "demo" / "duplex_conversation.jsonl"))
    actions = [event.action for event in state.output_events]
    assert "commit_turn" in actions, actions
    assert "interrupt_tts" in actions, actions
    assert state.interrupts == 1, state.interrupts
    assert any("总结这份合同" in turn for turn in state.committed_turns), state.committed_turns
    print("PASS duplex_conversation: commit_turn + interrupt_tts")

    state2 = process_events(load_events(root / "demo" / "short_pause_continuation.jsonl"))
    actions2 = [event.action for event in state2.output_events]
    assert "hold" in actions2, actions2
    assert state2.committed_turns[-1].endswith("然后生成一个待办"), state2.committed_turns
    print("PASS short_pause_continuation: hold before commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
