"""AnsiRenderer: the transcript it writes for a turn's events."""

from __future__ import annotations

import io

from pydantic import BaseModel

from pykiro import AnsiRenderer, Notice, TextDelta, ToolCall, TurnResult

M = "\u203a"  # the angle quote after a speaker label


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def text(chunk: str) -> TextDelta:
    return TextDelta(session_id="s", text=chunk)


def notice(kind: str, chunk: str) -> Notice:
    return Notice(session_id="s", method="session/update", kind=kind, text=chunk)


def test_a_turn_reads_as_a_transcript():
    out = io.StringIO()
    render = AnsiRenderer(out)

    for event in [
        notice("agent_thought_chunk", "Let me look."),
        ToolCall(session_id="s", tool_call_id="t1", title="Read File", status="pending", raw_input={"path": "a.txt"}),
        ToolCall(session_id="s", tool_call_id="t1", status="completed"),
        text("The file "),
        text("says hi."),
        TurnResult(session_id="s", stop_reason="end_turn", text="The file says hi.", credits=0.0123, elapsed_ms=1500),
    ]:
        render(event)

    assert out.getvalue().splitlines() == [
        f"thinking {M} Let me look.",
        '⚙ Read File  {"path": "a.txt"}',
        "✓ Read File",
        f"kiro {M} The file says hi.",
        "── end_turn · 0.0123 credits · 1.5 s ──",
    ]


def test_failed_tools_show_why_and_results_show_their_output():
    class Verdict(BaseModel):
        label: str

    out = io.StringIO()
    render = AnsiRenderer(out, thoughts=False)

    render(notice("agent_thought_chunk", "hidden"))
    render(ToolCall(session_id="s", tool_call_id="t1", title="Shell", status="pending"))
    render(ToolCall(session_id="s", tool_call_id="t1", status="failed", text="rejected by the user\nmore"))
    render(TurnResult(session_id="s", stop_reason="cancelled", output=Verdict(label="ok")))

    assert out.getvalue().splitlines() == [
        "⚙ Shell  ",
        "✗ Shell  rejected by the user more",
        f"output {M} Verdict(label='ok')",
        "── cancelled · 0.0000 credits ──",
    ]


def test_replayed_history_shows_both_speakers():
    out = io.StringIO()
    render = AnsiRenderer(out)

    for event in [notice("user_message_chunk", "Hi"), text("Hello!"), notice("user_message_chunk", "Bye")]:
        render(event)

    assert out.getvalue() == f"you {M} Hi\nkiro {M} Hello!\nyou {M} Bye"


def test_a_live_reply_after_replayed_history_starts_its_own_block():
    out = io.StringIO()
    render = AnsiRenderer(out)

    render(TextDelta(session_id="s", text="Earlier reply.", replay=True))
    render(text("New reply."))

    assert out.getvalue() == f"kiro {M} Earlier reply.\nkiro {M} New reply."


def test_colours_only_on_a_terminal_without_no_color(monkeypatch):
    plain, coloured, refused, forced, empty = io.StringIO(), Terminal(), Terminal(), io.StringIO(), Terminal()

    AnsiRenderer(plain)(text("x"))
    AnsiRenderer(coloured)(text("x"))
    AnsiRenderer(forced, color=True)(text("x"))
    monkeypatch.setenv("NO_COLOR", "")
    AnsiRenderer(empty)(text("x"))
    monkeypatch.setenv("NO_COLOR", "1")
    AnsiRenderer(refused)(text("x"))

    assert "\033[" not in plain.getvalue()
    assert "\033[" in coloured.getvalue()
    assert "\033[" in forced.getvalue()
    assert "\033[" in empty.getvalue()  # no-color.org: an empty NO_COLOR doesn't count
    assert "\033[" not in refused.getvalue()


OSC52 = "\033]52;c;cm0gLXJmIH4=\a"  # sets the terminal's clipboard
CONTROLS = [OSC52, "\033[2J", "\a", "\x9b31m", "\r", "\b", "\x7f"]


def test_terminal_control_characters_in_event_text_are_not_written():
    out = io.StringIO()
    render = AnsiRenderer(out, color=True)
    dirty = "x" + "".join(CONTROLS) + "y"

    render(notice("agent_thought_chunk", dirty))
    render(text(dirty + "\n\tz"))
    render(ToolCall(session_id="s", tool_call_id="t1", title=dirty + "\nforged line", status="pending"))
    render(ToolCall(session_id="s", tool_call_id="t1", status="failed", failure_reason=dirty))
    render(TurnResult(session_id="s", stop_reason=dirty))
    written = out.getvalue()

    for control in ("\033]", "\033[2J", "\a", "\x9b", "\r", "\b", "\x7f"):
        assert control not in written
    assert "\033[36m" in written  # the renderer's own colours remain
    assert "\n\tz" in written  # a reply keeps its newlines and tabs
    assert "forged line" in next(line for line in written.splitlines() if "⚙" in line)  # a title stays on its line


def test_a_tool_keeps_its_latest_title():
    out = io.StringIO()
    render = AnsiRenderer(out)

    render(ToolCall(session_id="s", tool_call_id="t1", title="Read", status="pending"))
    render(ToolCall(session_id="s", tool_call_id="t1", title="Read a.txt", status="in_progress"))
    render(ToolCall(session_id="s", tool_call_id="t1", status="completed"))

    assert out.getvalue().splitlines() == ["⚙ Read  ", "✓ Read a.txt"]


def test_an_output_that_is_falsy_is_still_used():
    class Empty(io.StringIO):
        def __bool__(self) -> bool:
            return False

    out = Empty()

    AnsiRenderer(out)(text("hi"))

    assert out.getvalue() == f"kiro {M} hi"
