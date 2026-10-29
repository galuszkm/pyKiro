# 03 · Conversations

`ask_sync()` is perfect for one question, but it starts and stops Kiro on every call. For a real conversation you
keep Kiro running and talk to it as long as you like. In this chapter we have a three-turn chat about a name for a
side project, and Kiro remembers every turn.

## What you'll learn

- The difference between a `KiroClient` (one Kiro process) and a `Session` (one conversation).
- Why you send only the *new* message each time.
- How to start and close things, first by hand, then with the shorter `async with`.

## The big idea

| Object | What it is | Cost to create one |
|---|---|---|
| `KiroClient` | One running Kiro process. Start it once, reuse it. | A few seconds. |
| `Session` | One conversation on a client. Kiro keeps its history. | About a second. |

One client can hold many sessions, like one chat app with many chat windows open. You run a turn with
`session.send(...)`. While it runs, pyKiro turns Kiro's messages into **events** (text chunks, tool calls and so
on). Hand `send` a function with `on_event=` and it gets every event. Here we use a ready-made one, `AnsiRenderer`,
which prints a readable transcript.

## The full code

```python
"""A three-turn chat on one Kiro process: Kiro remembers what was said before."""

import asyncio
import tempfile

from pykiro import AnsiRenderer, KiroClient

MODEL = "claude-haiku-5.5"


async def main() -> None:
    renderer = AnsiRenderer()

    # An empty folder for the agent to work in
    with tempfile.TemporaryDirectory() as workspace:
        client = KiroClient(cwd=workspace)
        try:
            # 1. Start Kiro
            await client.start()

            # 2. Open a session (one conversation)
            session = await client.new_session(model=MODEL)

            # 3. Chat: each message builds on the one before
            await session.send(
                "Give me 5 short names for a side project that tracks my houseplants.",
                on_event=renderer,
            )
            await session.send(
                "Pick the one you like best. Reply with only that name.",
                on_event=renderer,
            )
            await session.send(
                "Now write a one-line tagline for it.",
                on_event=renderer,
            )

            # 4. Read the bill
            print()
            print(f"This session spent {session.credits:.4f} credits.")
            print(f"The whole client spent {client.credits:.4f} credits.")
            if session.context_usage is not None:
                print(f"The model's context window is {session.context_usage:.1f}% full.")

            # 5. Close the session
            await session.close()
        finally:
            # 6. Stop Kiro, even if something above went wrong
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

Save it as `chat.py` and run `python chat.py`. The whole run cost about 0.015 credits and took about 12
seconds.

### What you'll see

```text
kiro › 1. Fernly
2. Sprout Log
3. Leafbook
4. Potwise
5. Verdant
── end_turn · 0.0095 credits · 2.3 s ──
kiro › Fernly
── end_turn · 0.0025 credits · 2.0 s ──
kiro › Keep every plant alive, one check-in at a time.
── end_turn · 0.0026 credits · 1.7 s ──

This session spent 0.0146 credits.
The whole client spent 0.0146 credits.
The model's context window is 2.0% full.
```

## How it works

### The workspace folder

```python
with tempfile.TemporaryDirectory() as workspace:
```

Every session has a **working directory** (`cwd`), the folder the agent works in. A `KiroClient` defaults to the
folder you started your script from, so the agent could read your project files, and a `.kiro` folder there would
load. For a chat about plant names we don't want that, so we use an empty temporary folder. Need more than one
folder? `new_session(additional_directories=[...])` adds more, and the agent reads them as freely as the `cwd`.

### Start, chat, close

```python
client = KiroClient(cwd=workspace)
try:
    await client.start()
    session = await client.new_session(model=MODEL)
    ...
    await session.close()
finally:
    await client.close()
```

`KiroClient(...)` only prepares the client. `await client.start()` launches the Kiro process, and
`await client.new_session(...)` opens a conversation. Kiro is a separate program and should never be left running by
accident, so `client.close()` sits in a `finally:` block, which Python runs whether the code above worked or crashed.
Closing the client also closes its sessions and cancels a turn that's still running.

### The shorter way: async with

Python has a tool for exactly this "set up, then always clean up" pattern: `async with`. It starts the client when
the block begins and closes it when the block ends, even after an error. This is a variation, not part of the
program above:

```python
async with KiroClient(cwd=workspace) as client:
    session = await client.new_session(model=MODEL)
    await session.send("Hello!", on_event=renderer)
```

That's the same as `start()`, `try`, `finally` and `close()`, with no `finally` to forget. The other chapters use
this form. A session works with `async with` too, when a conversation should end before the client does.

### Kiro remembers, so you don't have to

```python
await session.send(
    "Pick the one you like best. Reply with only that name.",
    on_event=renderer,
)
```

"The one you like best", which one? The message makes no sense on its own. It works because the session keeps the
whole conversation on Kiro's side. You send only the new message. Each `send` returns its `TurnResult`, which we
don't need here because the renderer already printed everything.

A session runs **one turn at a time**. A second `send` on the same session waits until the first is done. Want
things to happen at the same time? Use separate sessions on the same client.

### Watching and paying

`AnsiRenderer` prints the reply as it streams in (`kiro ›`), the agent's visible reasoning (`thinking ›`), tool calls
(`⚙`, then `✓` or `✗`), and a footer with the stop reason, credits and time. `on_event` must be a plain function,
not an `async def`. Chapter 04 shows how to handle events yourself.

`session.credits` is what this session spent, `client.credits` the total of all its sessions.
`session.context_usage` is how full the model's **context window** (its short-term memory) is, in percent. It's
`None` until Kiro reports it. When it gets high, start a fresh session.

## Gotchas, side effects and risks

- **The default `cwd` is your current folder.** The agent can read it. Pick the folder on purpose.
- **Close sessions you're done with.** Observed on kiro-cli 2.34.0: every open session makes creating the next one
  slower.
- **Don't create a client per message.** Starting Kiro takes seconds. Start once, reuse it.
- **Await your turns before closing.** Closing the client mid-turn cancels the turn.

## Where to next

Previous: [02 · Quick Start](02_Quick_Start.md). Next: [04 · Streaming Events](04_Streaming_Events.md), where we read
the events ourselves. Back to [all chapters](README.md).
