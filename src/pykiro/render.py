"""AnsiRenderer: prints a session's events as a readable transcript, coloured on a terminal."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import TextIO

from .events import Event, Notice, TextDelta, ToolCall, TurnResult

DIM = "\033[2m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"
MARK = "\u203a"  # the angle quote after a speaker label
LABELS = {"reply": ("kiro", CYAN), "thought": ("thinking", DIM), "you": ("you", GREEN)}
# C0 controls except tab and newline (ESC, CR, BEL, backspace, ...), DEL and C1 controls: in model- or tool-written
# text they could drive the terminal (clipboard, title, cursor) or forge transcript lines.
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


class AnsiRenderer:
    """Prints events: the reply as it streams, thoughts dimmed, tool calls with their outcome, and a footer.

    Pass it as a callback, or call it with each event::

        renderer = AnsiRenderer()
        await session.send("Write a haiku.", on_event=renderer)
        for event in session.history:  # a loaded session's earlier conversation
            renderer(event)

    Colours are used only when the output is a terminal and ``NO_COLOR`` is unset or empty; ``color=True`` forces
    them. Terminal control characters in event text are not written.
    """

    def __init__(self, file: TextIO | None = None, *, color: bool | None = None, thoughts: bool = True) -> None:
        """Create a renderer.

        Args:
            file: Where to write (default: ``sys.stdout`` at the time of writing).
            color: Force colours on or off (default: on for a terminal unless ``NO_COLOR`` is set and not empty).
            thoughts: Show the agent's visible reasoning.
        """
        self._file = file
        self._color = color
        self._thoughts = thoughts
        self._streaming: str | None = None  # "reply", "thought" or "you" while a text block is open
        self._replay = False
        self._tools: dict[str, str] = {}  # tool_call_id -> latest title: updates may omit it

    def __call__(self, event: Event) -> None:
        """Write one event."""
        if event.replay != self._replay:  # a replayed history and a live turn never share a block
            self._end_block()
            self._replay = event.replay
        if isinstance(event, TextDelta):
            self._stream("reply", event.text)
        elif isinstance(event, ToolCall):
            self._tool(event)
        elif isinstance(event, TurnResult):
            self._result(event)
        elif isinstance(event, Notice) and event.text:
            if event.kind == "user_message_chunk":
                self._stream("you", event.text)
            elif event.kind == "agent_thought_chunk" and self._thoughts:
                self._stream("thought", event.text)

    def _stream(self, block: str, text: str) -> None:
        if self._streaming != block:
            label, colour = LABELS[block]
            self._end_block()
            self._write(f"{self._paint(BOLD + colour, f'{label} {MARK}')} ")
            self._streaming = block
        text = CONTROL.sub("", text)
        self._write(self._paint(DIM, text) if block == "thought" else text)

    def _tool(self, event: ToolCall) -> None:
        started = event.tool_call_id in self._tools
        title = _single_line(event.title or "") or self._tools.get(event.tool_call_id)
        title = title or _single_line(event.kind or "") or "tool"
        self._tools[event.tool_call_id] = title
        if not started:
            preview = json.dumps(event.raw_input) if event.raw_input is not None else ""
            self._line(f"{self._paint(YELLOW, '⚙')} {title}  {self._paint(DIM, _one_line(preview))}")
        if event.status == "completed":
            self._line(f"{self._paint(GREEN, '✓')} {title}")
        elif event.status == "failed":
            reason = _one_line(event.failure_reason or event.text)
            self._line(f"{self._paint(RED, '✗')} {title}  {self._paint(DIM, reason)}")

    def _result(self, result: TurnResult) -> None:
        if result.output is not None:
            self._line(f"{self._paint(BOLD, f'output {MARK}')} {_single_line(repr(result.output))}")
        parts = [_single_line(result.stop_reason), f"{result.credits:.4f} credits"]
        if result.elapsed_ms is not None:
            parts.append(f"{result.elapsed_ms / 1000:.1f} s")
        colour = DIM if result.stop_reason == "end_turn" else YELLOW
        self._line(self._paint(colour, f"── {' · '.join(parts)} ──"))
        self._tools.clear()

    def _line(self, text: str) -> None:
        self._end_block()
        self._write(text + "\n")

    def _end_block(self) -> None:
        if self._streaming is not None:
            self._write("\n")
            self._streaming = None

    def _paint(self, codes: str, text: str) -> str:
        return f"{codes}{text}{RESET}" if self._use_color() else text

    def _use_color(self) -> bool:
        if self._color is None:
            self._color = self._out().isatty() and not os.environ.get("NO_COLOR")  # no-color.org: empty means unset
        return self._color

    def _out(self) -> TextIO:
        return self._file if self._file is not None else sys.stdout

    def _write(self, text: str) -> None:
        self._out().write(text)
        self._out().flush()


def _single_line(text: str) -> str:
    """``text`` without control characters, its whitespace runs collapsed to single spaces."""
    return " ".join(CONTROL.sub("", text).split())


def _one_line(text: str, width: int = 80) -> str:
    text = _single_line(text)
    return text if len(text) <= width else text[: width - 1] + "…"
