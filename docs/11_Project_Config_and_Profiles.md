# 11 · Project Config and Profiles

Your team has a `.kiro` folder in the repo: a steering file with the house style, maybe a skill or two. People get it
for free in the Kiro IDE and CLI. In this chapter your program gets it too, so people and programs share one agent
setup. And you'll see which configuration pyKiro loads, and when.

## What you'll learn

- The two layers of Kiro configuration: **user level** (`~/.kiro`) and **workspace level** (the project's `.kiro`).
- What a **profile** is: `Isolated()`, `Isolated(path)` and `Inherit()`.
- How a project's steering and skill load, and why a workspace is code you run.

## The big idea

Kiro reads its configuration from two layers: your own **user level** (`~/.kiro`, or the folder in `KIRO_HOME`) and
the **workspace level**, the `.kiro` folder inside the session's `cwd`. A **profile** (the type is `Profile`: either
`Isolated` or `Inherit`) decides the user layer:

| Profile | User layer | Use it for |
|---|---|---|
| `Isolated()` (the default) | A fresh, empty, temporary `KIRO_HOME`, deleted when the client closes. | Scripts, CI, anything repeatable |
| `Isolated(path)` | A private folder you choose, so stored sessions survive. | Programs that resume sessions ([chapter 13](13_Saving_and_Resuming_Sessions.md)) |
| `Inherit()` | Your real `~/.kiro`: your agents, MCP servers, hooks and rules. | Personal helpers on your own machine |

The workspace layer loads in **every** profile. With the default `Isolated()`, only the project's setup applies, so
the script behaves the same on your laptop, your colleague's laptop and in CI. Two things we use here:

- **Steering** is a Markdown file with standing rules, under `.kiro/steering/`. It always applies.
- A **skill** is a recipe for one job, under `.kiro/skills/<name>/SKILL.md`. Kiro loads it when a request matches its
  `description` ([Kiro's skills docs](https://kiro.dev/docs/skills/)).

## The full code

```python
"""Reuse a project's Kiro setup: a steering file and a skill that live in the workspace."""

import tempfile
from pathlib import Path

from pykiro import AnsiRenderer, Event, Notice, ask_sync

MODEL = "claude-haiku-5.5"

STEERING = """\
# House style

- Write in plain English, with short sentences.
- Start every bullet with a verb.
- End every answer with this line: -- the docs team
"""

SKILL = """\
---
name: release-notes
description: Use when asked to write release notes from a list of changes.
---

Write release notes like this:

1. The first line is the title: Release notes.
2. Put each change under one heading: New, Fixed or Removed. Skip headings with no changes.
3. One bullet per change.
"""

PROMPT = (
    "Use the release-notes skill to write release notes for these changes: "
    "fixed the login timeout, added dark mode, removed the old export button."
)

renderer = AnsiRenderer()


def show(event: Event) -> None:
    """Print the turn as usual, and say so when Kiro loads a steering file."""
    renderer(event)
    if isinstance(event, Notice) and event.detail == "steering_inclusion":
        print("[Kiro loaded a steering file]")


with tempfile.TemporaryDirectory() as folder:
    workspace = Path(folder)

    # 1. Write the team's .kiro folder before Kiro starts: Kiro caches agent files
    steering_folder = workspace / ".kiro" / "steering"
    steering_folder.mkdir(parents=True)
    (steering_folder / "house-style.md").write_text(STEERING)

    skill_folder = workspace / ".kiro" / "skills" / "release-notes"
    skill_folder.mkdir(parents=True)
    (skill_folder / "SKILL.md").write_text(SKILL)

    # 2. Ask Kiro, and allow it to load skills
    ask_sync(PROMPT, cwd=workspace, model=MODEL, allow=["skill"], on_event=show)
```

Save it as `release_notes.py` and run `python release_notes.py`. One run costs about 0.005 to 0.02 credits.

### What you'll see

```text
[Kiro loaded a steering file]
⚙ Load skill: release-notes  {"name": "release-notes"}
✓ Loaded skill: release-notes
kiro › Release notes

**Fixed**
- Fixed the login timeout.

**New**
- Added dark mode.

**Removed**
- Removed the old export button.

-- the docs team
── end_turn · 0.0189 credits · 4.6 s ──
```

The title and the three headings come from the **skill**. The sign-off `-- the docs team` comes from the
**steering** file. Nothing in our prompt asked for either of them.

## How it works

### The project's `.kiro` folder

In a real project these files are already in git, and you only pass `cwd=` to point Kiro at the project. We create
them in a temp folder so the example runs anywhere. Note the order: files first, then Kiro. Observed on kiro-cli
2.34.0: Kiro caches agent files, so a file written right before the session may not be seen.

### The profile: nothing to write

```python
ask_sync(PROMPT, cwd=workspace, model=MODEL, allow=["skill"], on_event=show)
```

We never mention a profile, so we get `Isolated()`: an empty temporary `KIRO_HOME`, so none of *your* agents,
steering or MCP servers load. Your login still works, because Kiro CLI keeps it outside `KIRO_HOME`. The other two
profiles go on the client. This is a variation, not part of the program above:

```python
from pykiro import Inherit, Isolated

KiroClient(profile=Isolated("./kiro-home"))  # keeps its sessions in ./kiro-home
KiroClient(profile=Inherit())  # behaves like your own Kiro CLI
```

`Isolated(path)` refuses folders that would make Kiro fall back to your real settings (your home folder, a parent of
it, `~/.kiro`, the filesystem root) with a `ValueError`.

### Skills are a permission ask

Loading a skill is an **ask** with `capability="skill"`. We wrote no policy, so without `allow=["skill"]` the ask is
rejected and the agent just does the job *without* the skill. No error. If a skill seems to do nothing, check that
loading it is allowed. `allow=` also makes this a custom agent ([chapter 09](09_Custom_Agents.md)), which doesn't
matter here because the agent reads no files.

### Seeing that it loaded

```python
if isinstance(event, Notice) and event.detail == "steering_inclusion":
    print("[Kiro loaded a steering file]")
```

When Kiro includes a steering file, it sends a notice whose `detail` is `steering_inclusion`, and the skill shows up
as a tool call titled `Load skill: release-notes` (both observed on kiro-cli 2.34.0). They're Kiro's messages, not a
promise, so use them for debugging and "did my setup load?" checks.

### Trust: a workspace is code you run

Kiro trusts every workspace: whatever is in `<cwd>/.kiro` loads, and MCP servers and hooks there can run commands on
your machine. If your program points a session at a folder it doesn't control, like a freshly cloned repository, use
the guard. This is a variation, not part of the program above:

```python
KiroClient(forbid_workspace_config=True)
```

Then creating a session whose `cwd` contains a `.kiro` entry raises a `ValueError`. It checks the `cwd` folder itself
only, not parent folders.

## Gotchas, side effects and risks

- **Isolation covers the user layer only.** It doesn't pin Kiro's version, the models or your account. Two machines:
  close, not identical.
- **Workspace config always loads.** The only ways out are an empty `cwd` (what `ask()` uses) or
  `forbid_workspace_config=True`.
- **`Inherit()` is your real setup.** It loads your hooks and MCP servers and stores sessions in your real `~/.kiro`.
- **`description` is the trigger.** A vague skill description means the agent rarely picks the skill.

## Where to next

Previous: [10 · Permissions](10_Permissions.md). Next: [12 · MCP Servers](12_MCP_Servers.md), where we plug in an
outside tool server. Back to [all chapters](README.md).
