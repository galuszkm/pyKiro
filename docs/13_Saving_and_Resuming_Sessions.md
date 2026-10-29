# 13 · Saving and Resuming Sessions

So far every conversation ended with the program. In this chapter we build a tiny journal: you write an entry, run
the program, and next time you run it, the conversation goes on where it stopped. Kiro remembers, and your program
only keeps one id.

## What you'll learn

- Where Kiro keeps a conversation, and how `Isolated(path)` makes it survive your program.
- How to open a stored session again with `load_session()`, list sessions and delete one.
- What Kiro does *not* store, so you know what to pass again.

## The big idea

Kiro stores a session's history in its home folder (`KIRO_HOME`). The **profile** ([chapter 11](11_Project_Config_and_Profiles.md))
decides which home that is:

| Profile | Kiro home | Stored sessions |
|---|---|---|
| `Isolated()` (the default) | A temporary folder | Deleted when the client closes. |
| `Isolated(path)` | A folder you choose | They stay. A later run can list and load them. |

So "remember across runs" takes two things: give `Isolated(...)` a path, and save the session's **id** yourself, so
the next run knows which conversation to continue. A small file is enough.

## The full code

```python
"""A journal that remembers: every run continues the same conversation."""

import asyncio
from pathlib import Path

from pykiro import AnsiRenderer, Isolated, KiroClient

MODEL = "claude-haiku-5.5"
PERSONA = "You are my journal companion. Answer in at most two short sentences. Today's entry: "

FOLDER = Path(".journal")
KIRO_HOME = FOLDER / "kiro-home"  # Kiro stores the conversation here
WORKSPACE = FOLDER / "workspace"  # an empty folder for the agent to work in
SESSION_FILE = FOLDER / "session_id"  # which stored session is our journal


async def main() -> None:
    # 1. Write today's entry (change it before every run)
    entry = "Today I started learning the cello."

    WORKSPACE.mkdir(parents=True, exist_ok=True)

    # 2. Start Kiro. Isolated(KIRO_HOME) keeps the conversation between runs
    async with KiroClient(cwd=WORKSPACE, profile=Isolated(KIRO_HOME)) as client:
        # 3. Open the journal's session
        if SESSION_FILE.exists():
            # Not the first entry: continue the stored conversation
            session_id = SESSION_FILE.read_text().strip()
            session = await client.load_session(session_id, model=MODEL)
        else:
            # The first entry: start a conversation and remember its id
            session = await client.new_session(model=MODEL)
            SESSION_FILE.write_text(session.id)
            entry = PERSONA + entry  # only the first entry needs it: Kiro remembers

        # 4. Write the entry
        await session.send(entry, on_event=AnsiRenderer())


if __name__ == "__main__":
    asyncio.run(main())
```

Save it as `journal.py` and run `python journal.py` from a folder of your own. Then change `entry` and run it
again. The program creates a `.journal/` folder that holds your conversation, so it's private: add `.journal/` to
your `.gitignore`. Each entry costs about 0.01 credits or less.

### What you'll see

The first run, with the entry above:

```text
kiro › That's a meaningful first step. Learning the cello is a demanding instrument, so steady, short practice sessions will help you build good habits from the start.
── end_turn · 0.0096 credits · 2.3 s ──
```

Then we changed `entry` to `"Which instrument did I start learning? One short sentence."` and ran it again:

```text
kiro › The cello.
── end_turn · 0.0026 credits · 2.4 s ──
```

The second entry never mentions the cello, and the answer still knows it. Two separate Python programs, one
conversation.

## How it works

### Where the conversation lives

`.journal/` holds three things. `kiro-home/` is the Kiro home, where Kiro writes the stored conversation. pyKiro
creates it readable by you only. `workspace/` is an empty folder for the agent to work in. `session_id` is a one-line
file with the id of our session, because one home can hold many sessions. All three paths are relative, so run the
program from somewhere else and you start a brand-new journal there.

### First entry, next entries

```python
if SESSION_FILE.exists():
    session_id = SESSION_FILE.read_text().strip()
    session = await client.load_session(session_id, model=MODEL)
else:
    session = await client.new_session(model=MODEL)
    SESSION_FILE.write_text(session.id)
```

No file means no journal yet, so we make a new session and write its id down right away. The `PERSONA` goes in front
of the very first entry only, because Kiro keeps the whole conversation, that first message included.

`load_session` opens a stored session again. pyKiro first checks that Kiro really has that id, and raises a
`ValueError` if not (Kiro itself would quietly create a new, empty session). Kiro then **replays** the old
conversation into `session.history`, and replays cost no credits. After that, `send` continues as usual.

Closing a session doesn't delete it. When the client's `async with` block ends, the session is closed and still
stored.

### History, listing and deleting

These are variations, not part of the program above:

```python
renderer = AnsiRenderer()
for event in session.history:
    renderer(event)

for stored in await client.list_sessions():
    print(stored.session_id, stored.updated_at, stored.title)

await client.delete_session(session_id)
SESSION_FILE.unlink()
```

`session.history` holds the old events, marked `replay=True`, and `AnsiRenderer` prints them as a transcript
(`you ›`, `kiro ›`). `list_sessions()` returns a `SessionInfo` per stored session, with a `title` Kiro makes up (or
`None`). `delete_session` removes a session for good; delete your id file too, or it points to nothing.

### What isn't stored: pass it again

Kiro stores the *conversation*. Your program owns the *setup*, so you pass it again on every load:
`load_session(session_id, cwd=..., model=..., tools=..., mcp_servers=..., policy=..., system=...)`. It takes the
same options as `new_session`, except `mode`.

## Gotchas, side effects and risks

- **Reuse the exact same agent settings on every load.** Observed on kiro-cli 2.34.0: Kiro doesn't store `system`,
  `kiro_tools`, `allow` or `deny`. Load with different ones and you get a `ValueError`, and after that one wrong load
  even the right settings failed: the stored session stayed on Kiro's default agent. Keep the settings in one
  constant. Our journal uses none, so there's nothing to get wrong.
- **The home folder holds your conversations.** Anyone who can read `.journal/` can read your journal.
- **Don't open one session twice.** A second `load_session` for an id that's already open on the client raises a
  `ValueError`.
- **Long journals fill the context window.** Watch `session.context_usage` and start fresh when it gets high.

## Where to next

Previous: [12 · MCP Servers](12_MCP_Servers.md). Next: [14 · Batch Jobs](14_Batch_Jobs.md), where we run many small
jobs in parallel. Back to [all chapters](README.md).
