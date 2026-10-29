# 02 · Quick Start

You've got a quick question and you'd love to ask Kiro without opening anything else. Just `python ask.py` and an
answer shows up. That's what we build here: the "hello world" of pyKiro, plus what to do when it doesn't work.

## What you'll learn

- How to install Kiro CLI, log in, and install pyKiro.
- How to ask Kiro a question with one call, `ask_sync()`, and what's inside the answer.
- What the first errors mean, and how to fix them.

## The big idea

`ask_sync()` is the shortest road from Python to Kiro. It starts Kiro, opens a conversation, sends your question,
waits for the answer, and shuts everything down again. You get back a `TurnResult`:

| Field | What it is |
|---|---|
| `text` | The full answer, as one string. |
| `stop_reason` | Why Kiro stopped. Normally `end_turn`, which means "I'm done". |
| `credits` | What this turn cost from your Kiro plan. |
| `elapsed_ms` | How long Kiro says the turn took, in milliseconds. `None` if Kiro didn't report it. |

## The full code

```python
"""Ask Kiro one question."""

import sys

from pykiro import KiroProcessError, ask_sync

MODEL = "claude-haiku-5.5"

# 1. Write your question (change it to anything you like)
question = "In one sentence: why is the sky blue?"

# 2. Ask Kiro (this starts Kiro, waits for the answer and stops Kiro again)
try:
    answer = ask_sync(question, model=MODEL)
except KiroProcessError as error:
    print(f"Could not talk to Kiro: {error}", file=sys.stderr)
    sys.exit(1)

# 3. Show the answer and what it cost
print(answer.text)
print()
print(f"Stopped because: {answer.stop_reason}")
print(f"Credits spent: {answer.credits:.4f}")
```

Save it as `ask.py` and run `python ask.py`. Never installed anything yet? Do
[the setup below](#setting-things-up-once) first, it takes a few minutes. One run cost 0.01 to 0.02 credits in our
runs.

### What you'll see

```text
Sunlight contains all colors, and air molecules scatter shorter blue wavelengths much more strongly than longer red ones (Rayleigh scattering), so blue light reaches our eyes from all directions of the sky.

Stopped because: end_turn
Credits spent: 0.0097
```

## How it works

### Setting things up (once)

**1. Install Kiro CLI.** Follow [Kiro's installation guide](https://kiro.dev/docs/getting-started/installation/#cli).
These docs were tested with **kiro-cli 2.34.0**. Check yours with `kiro-cli --version`, and pyKiro's with
`pykiro.__version__`.

**2. Log in.** Run `kiro-cli login` and follow the browser prompts. No browser on that machine? Use
`kiro-cli login --use-device-flow`. In CI, where nobody can log in, Kiro's
[headless mode](https://kiro.dev/docs/cli/headless/) uses an API key instead ([chapter 15](15_CI_CD.md)). pyKiro
itself never needs a key: it uses whatever login Kiro CLI has, and it never reads your credentials.

**3. Install pyKiro.** You need Python 3.11–3.14. One command installs it straight from GitHub:

```bash
pip install git+https://github.com/galuszkm/pyKiro
```

Using [uv](https://docs.astral.sh/uv/)? Run `uv add git+https://github.com/galuszkm/pyKiro` in your project, and run
your programs with `uv run python ...` instead of `python ...`.

### One call, one answer

```python
answer = ask_sync(question, model=MODEL)
```

This is the whole trick. `ask_sync` waits until Kiro has answered, then hands you the result, like any normal
function. Two things happen behind the curtain that are good to know:

- The agent works in an **empty temporary folder**, so it can't see your project. Pass `cwd="some/folder"` to change
  that, and remember it can then read what's there.
- Kiro gets a **clean, temporary configuration**, so your own Kiro agents, steering files and MCP servers don't load.
  [Chapter 11](11_Project_Config_and_Profiles.md) shows how to use your own setup instead.

`ask_sync` runs pyKiro's async machinery for you, so it can't be called from inside an `async def` (Python raises a
`RuntimeError`). In async code you `await ask(...)` instead: same arguments, same result.

### Friendly errors

```python
try:
    answer = ask_sync(question, model=MODEL)
except KiroProcessError as error:
    print(f"Could not talk to Kiro: {error}", file=sys.stderr)
    sys.exit(1)
```

Most first-time problems are about the `kiro-cli` program: not installed, not logged in, or it crashed. pyKiro
raises a `KiroProcessError` for all of those, with a message that says what to do. We print it to **stderr** (the
channel for errors) and exit with code 1, so scripts and CI jobs notice the failure.

### When it doesn't work

pyKiro has three error classes of its own. `KiroError` is the parent of the other two:

| Error | Whose problem | Look at |
|---|---|---|
| `KiroProcessError` | The `kiro-cli` program: missing, not logged in, crashed, or silent. | `exit_code`, `stderr_tail` (the last lines Kiro printed) |
| `KiroProtocolError` | Kiro answered one of pyKiro's requests with an error. | `code`, `message`, `data` |
| `KiroError` | Kiro returned an unusable ACP response, a typed-result turn ended without a result, or the client closed under a running turn. | the message |

Catch the specific ones first: an `except KiroError` on top would swallow the other two. And these are the messages
you're most likely to see first:

| You see | What to do |
|---|---|
| `kiro-cli not found on PATH ...` | Install it, or pass the full path: `ask_sync(..., executable="/path/to/kiro-cli")`. |
| `Not logged in to Kiro: run kiro-cli login ...` | Run `kiro-cli login`, or set `KIRO_API_KEY` in CI. |
| `exited before answering initialize`, with `invalid value 'v3'` in the stderr lines | Your `kiro-cli` is too old. Update it. `which -a kiro-cli` shows an old copy hiding on your PATH. |
| `Kiro does not advertise required ACP capabilities` | Update `kiro-cli`; this version doesn't provide the V3 contract pyKiro uses. |
| `ValueError: '...' is not a value of 'model'` | Your account doesn't offer that model. Pick one from the list in the message. |
| `ValueError: option 'model' is not advertised` | Kiro had not listed its models yet, right after starting (observed on kiro-cli 2.34.0). Run it again. |
| It just waits | Put a deadline around the turn ([chapter 04](04_Streaming_Events.md)). |

Still stuck? Turn on pyKiro's logs before you start Kiro. They show what `kiro-cli` writes to stderr, as it arrives.
This is a variation, not part of the program above:

```python
logging.basicConfig(format="%(name)s %(levelname)s: %(message)s")
logging.getLogger("pykiro").setLevel(logging.DEBUG)
logging.getLogger("pykiro.wire").setLevel(logging.WARNING)
```

The last line keeps `pykiro.wire` quiet. That logger records every message between pyKiro and Kiro, including your
prompts, the replies and account details, so only switch it on into a private file, and never share that file.

## Gotchas, side effects and risks

- **Each `ask_sync()` starts Kiro from scratch.** That's a few seconds before and after the turn. Asking in a loop?
  Keep one client open instead ([chapter 03](03_Conversations.md)).
- **Match on types and attributes, not message text.** `exit_code` and the error class are stable. Wording may change.
- **Stderr can hold private details.** Paths, versions, endpoints. Read a `stderr_tail` before you paste it anywhere.

## Where to next

Previous: [01 · Introduction](01_Introduction.md). Next: [03 · Conversations](03_Conversations.md), where we keep one
Kiro process alive and talk for several turns. Back to [all chapters](README.md).
