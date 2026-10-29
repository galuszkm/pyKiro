"""on_event and AnsiRenderer on a real turn."""

from __future__ import annotations

import io

import pytest

from pykiro import AnsiRenderer, Event, ask

pytestmark = [pytest.mark.live, pytest.mark.anyio, pytest.mark.timeout(240)]


async def test_every_event_reaches_on_event_and_renders_as_a_transcript(model, effort):
    seen: list[Event] = []
    out = io.StringIO()
    renderer = AnsiRenderer(out)

    def on_event(event: Event) -> None:
        seen.append(event)
        renderer(event)

    result = await ask("Reply with exactly the word: pong", model=model, effort=effort, on_event=on_event)

    assert seen[-1] is result
    assert "pong" in out.getvalue().lower()
    assert "end_turn" in out.getvalue().splitlines()[-1]
