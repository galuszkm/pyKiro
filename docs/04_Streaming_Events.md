# 04 · Streaming Events

In chapter 03 the ready-made `AnsiRenderer` printed each turn for us. Now we write our own little live console: we
read the events of a turn one by one, decide what to show, and put a deadline on the whole thing so it can never
hang.

## What you'll learn

- How `session.stream()` gives you a turn as a flow of events.
- The four kinds of events: `TextDelta`, `ToolCall`, `Notice` and `TurnResult`.
- How to stop a turn: leave the loop, or hit a deadline.

## The big idea

`stream()` is an **async generator**: a function you loop over with `async for`, and it hands you one event at a
time as they arrive. `send()` from chapter 03 is the same loop, run for you. Every event is a small frozen dataclass:

| Event | When you get it | Main fields |
|---|---|---|
| `TextDelta` | A chunk of the reply | `text` |
| `ToolCall` | A tool call starts, then every time it changes | `tool_call_id`, `title`, `status`, `raw_input`, `failure_reason` |
| `Notice` | Everything else Kiro tells us | `method`, `kind`, `detail`, `text` |
| `TurnResult` | The turn is over. Always the last event. | `stop_reason`, `text`, `credits`, `elapsed_ms` |

TL;DR: loop over `stream()`, check each event's type with `isinstance`, handle the ones you care about, ignore the
rest. And when you stop reading, the turn stops too.

## The full code

```python
"""A small live console: watch one turn event by event, with a deadline."""

import asyncio
import tempfile
from pathlib import Path

from pykiro import KiroClient, Session, TextDelta, ToolCall, TurnResult

MODEL = "claude-haiku-5.5"
PROMPT = "Read the two files in notes/ and list the top 3 next steps, one line each."
DEADLINE = 60  # seconds


def write_notes(workspace: str) -> None:
    notes = Path(workspace) / "notes"
    notes.mkdir()

    todo_file = notes / "todo.md"
    todo_text = "- Fix the login bug\n- Write the release notes\n- Update the README\n"
    todo_file.write_text(todo_text)

    ideas_file = notes / "ideas.md"
    ideas_text = "- Add dark mode\n- Try a faster build cache\n"
    ideas_file.write_text(ideas_text)


async def show_turn(session: Session, prompt: str) -> None:
    async for event in session.stream(prompt):
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, ToolCall) and event.status == "pending":
            print(f"⚙ {event.title} started")
        elif isinstance(event, ToolCall) and event.status == "completed":
            print(f"✓ {event.title} finished")
        elif isinstance(event, TurnResult):
            print()
            print(f"Done: {event.stop_reason}")
            print(f"Credits spent: {event.credits:.4f}")


async def main() -> None:
    # An empty folder, with two small note files for Kiro to read
    with tempfile.TemporaryDirectory() as workspace:
        write_notes(workspace)

        # 1. Start Kiro
        async with KiroClient(cwd=workspace) as client:
            # 2. Open a session
            session = await client.new_session(model=MODEL)

            # 3. Watch the turn, but never wait longer than the deadline
            try:
                async with asyncio.timeout(DEADLINE):
                    await show_turn(session, PROMPT)
            except TimeoutError:
                print()
                print(f"No answer after {DEADLINE} seconds, so the turn was stopped.")


if __name__ == "__main__":
    asyncio.run(main())
```

Save it as `console.py` and run `python console.py`. One run cost about 0.013 credits.

### What you'll see

```text
⚙ Read File started
⚙ Read File started
✓ Read File finished
✓ Read File finished
1. Fix the login bug (todo.md)
2. Write the release notes (todo.md)
3. Update the README (todo.md)

Done: end_turn
Credits spent: 0.0131
```

And with `DEADLINE = 2`, so the deadline wins:

```text

No answer after 2 seconds, so the turn was stopped.
```

Two seconds weren't even enough for the first tool call. The model thinks before it acts, and the deadline counts
that time too.

## How it works

### One loop for the whole turn

```python
async for event in session.stream(prompt):
```

`stream()` starts the turn and gives you the events in Kiro's order. When the turn ends normally, the last event is
the `TurnResult`, and the loop simply ends. No "is it done yet?" checks.

### Text: print it as it comes

```python
if isinstance(event, TextDelta):
    print(event.text, end="", flush=True)
```

A `TextDelta` is the *new* piece of the reply, not the text so far. `end=""` skips the newline, and `flush=True`
puts it on screen right away. Chunks can end in the middle of a word, so collect them if you need whole lines.

### Tool calls: start, then updates

```python
elif isinstance(event, ToolCall) and event.status == "pending":
    print(f"⚙ {event.title} started")
elif isinstance(event, ToolCall) and event.status == "completed":
    print(f"✓ {event.title} finished")
```

A tool call is a little story told over several events: first "starting" (`pending`), then updates (`in_progress`,
then `completed`, or `failed` with a `failure_reason`). They all share one `tool_call_id`. An update only carries
what changed, so fields like `title` can be `None` on later events. In the sample, both reads started before either
finished: several calls can be in flight at once.

`title` (`Read File`) is a label for humans. Show it, but never decide anything based on it, because Kiro or the
model writes it.

### Notices: everything else

Kiro sends many kinds of updates. pyKiro gives special types only to the ones you nearly always want, and everything
else arrives as a `Notice`: status messages, context usage, and the agent's visible reasoning (`kind ==
"agent_thought_chunk"`). Our loop ignores them, and that's the point: when a new Kiro version adds a new kind of
message, your code keeps working.

### Stopping a turn

```python
try:
    async with asyncio.timeout(DEADLINE):
        await show_turn(session, PROMPT)
except TimeoutError:
    ...
```

A turn keeps running on Kiro's side until it's done, and you keep paying for it. To stop it, you just stop reading.
pyKiro then **cancels** the turn (it tells Kiro "please stop" and waits for the answer):

| You do this | What happens |
|---|---|
| `break` out of the loop | pyKiro cancels the turn. |
| An exception flies out of the loop | pyKiro cancels the turn. |
| A deadline like `asyncio.timeout(...)` runs out | pyKiro cancels the turn, then you get a `TimeoutError`. |
| `await session.cancel()` from another task (a "Stop" button) | The turn ends with `stop_reason == "cancelled"`. |

`asyncio.timeout(seconds)` is standard Python 3.11. The deadline covers everything, the model's thinking time
included, so give it some air. In all cases the session is fine afterwards, and the next turn runs as normal.

Want only the first few lines of a long reply? Leave the loop with `break` once you have them. This is a variation,
not part of the program above:

```python
text = ""
async for event in session.stream("List 50 fruits, one per line."):
    if isinstance(event, TextDelta):
        text += event.text
        if text.count("\n") >= 3:
            break  # leaving the loop cancels the turn
```

### When things go wrong

If `kiro-cli` dies mid-turn, the loop raises `KiroProcessError` instead of ending with a `TurnResult`. If the client
closes while you read, you normally get a `TurnResult` with `stop_reason == "cancelled"`.

## Gotchas, side effects and risks

- **A stopped turn is not a free turn.** Observed on kiro-cli 2.34.0: cancelled turns reported 0 credits, but the
  model may already have worked. Treat your credit count as a lower bound.
- **A deadline isn't a budget.** It limits how long *you* wait, not what the turn costs.
- **Never call `send()` on a session from inside its own `stream()` loop.** One turn at a time: it would wait for
  itself forever.
- **Your app tools aren't interrupted.** A cancel stops the agent, not a Python function of yours that's already
  running ([chapter 08](08_App_Tools.md)).

## Where to next

Previous: [03 · Conversations](03_Conversations.md). Next: [05 · Models and Credits](05_Models_and_Credits.md), where
we pick a model and see what things cost. Back to [all chapters](README.md).
