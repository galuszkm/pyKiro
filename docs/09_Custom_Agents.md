# 09 · Custom Agents

Kiro's default agent, `vibe`, has every built-in tool: it can read, write, run commands and browse. Great for general
work, but sometimes you want a smaller, tighter agent for one job. In this chapter we build a read-only code guide:
it reads a folder and answers questions, but it can't change or run anything.

## What you'll learn

- What the four agent settings are: `system`, `kiro_tools`, `allow` and `deny`.
- What a system prompt can and can't do for you.
- The side effects of agent settings, so they don't surprise you.

## The big idea

An **agent** in Kiro is three things in a bundle: a **prompt** (who it is), a list of **tools** (what it's able to
do) and **permission rules** (what it may do without asking). `ask_sync()`, `ask()` and `new_session()` all take the
same four optional arguments to build your own:

| Argument | What it sets | Example |
|---|---|---|
| `system` | The system prompt, the standing instruction the agent sees before your message. | `"You are a guide to this code."` |
| `kiro_tools` | Which tools the agent gets, as a list of **selectors**. | `["read", "@app"]` |
| `allow` | Capabilities the agent may use without asking anyone. | `["fs_read"]` |
| `deny` | Capabilities that are blocked, no matter what. | `["fs_write", "shell"]` |

A **selector** picks a group of tools: a tag like `read`, `write`, `shell` or `web`, a server like `@app`, or `*` for
everything. A **capability** is the kind of thing the agent wants to do, like `fs_read`, `fs_write` or `shell`. Kiro
owns both lists ([Kiro's custom agent reference](https://kiro.dev/docs/custom-agents/configuration-reference/)).

Give none of the four and you get Kiro's default agent. Give any of them and pyKiro builds a custom agent for that
one question (or session).

## The full code

```python
"""A read-only code guide: the agent can read a folder and answer questions, but not change or run anything."""

import tempfile
from pathlib import Path

from pykiro import AnsiRenderer, ask_sync

MODEL = "claude-haiku-5.5"
QUESTION = "Where is the discount applied, and what happens for orders over 100? Name the file and the line."
GUIDE = (
    "You are a guide to the code in the current folder. Read the files, then answer the question. "
    "Point to the exact lines that prove your answer. Never guess about code you have not read."
)

# A tiny shop, written into the workspace so the agent has something to read
PRICING = """\
DISCOUNT_THRESHOLD = 100
DISCOUNT_RATE = 0.10


def apply_discount(total):
    # Orders over 100 get 10% off. An order of exactly 100 gets nothing.
    if total > DISCOUNT_THRESHOLD:
        return round(total * (1 - DISCOUNT_RATE), 2)
    return total
"""

CART = """\
from pricing import apply_discount


def cart_total(prices):
    subtotal = sum(prices)
    return apply_discount(subtotal)
"""

# A folder for the agent to work in
with tempfile.TemporaryDirectory() as folder:
    workspace = Path(folder)
    (workspace / "pricing.py").write_text(PRICING)
    (workspace / "cart.py").write_text(CART)

    # 1. Ask Kiro, with our own agent settings
    ask_sync(
        QUESTION,
        cwd=workspace,
        model=MODEL,
        system=GUIDE,
        kiro_tools=["read"],
        allow=["fs_read"],
        deny=["fs_write", "shell"],
        on_event=AnsiRenderer(),
    )
```

Save it as `repo_guide.py` and run `python repo_guide.py`. The run cost about 0.015 credits.

### What you'll see

```text
kiro › I'll start by listing the workspace to find the code.
⚙ List Directory  {"path": "/work", "explanation": "Find the source files that handle …
✓ List Directory
⚙ Grep Search  {"query": "discount", "caseSensitive": false, "explanation": "Locate every refe…
✓ Grep Search
⚙ Read File  {"path": "/work/pricing.py", "offset": null, "limit": null}
⚙ Read File  {"path": "/work/cart.py", "offset": null, "limit": null}
✓ Read File
✓ Read File
kiro › The discount is applied in `pricing.py`, in the `apply_discount` function.
- **`pricing.py` line 7:** `if total > DISCOUNT_THRESHOLD:` checks the threshold, so the check is strictly greater than 100.
- **`pricing.py` line 8:** `return round(total * (1 - DISCOUNT_RATE), 2)` takes 10% off.
So an order over 100 gets 10% off. An order of exactly 100 gets nothing, as the comment on line 6 says.
── end_turn · 0.0150 credits · 8.6 s ──
```

This is a real run, trimmed. We replaced the temporary folder's name with `/work`.

## How it works

### The four settings

**`system=GUIDE`** is the job description you hand the agent on day one. It *steers* the agent, but it doesn't
*guarantee* anything: a model can still ignore it now and then. That's why the other three settings exist. They're
real limits, not polite requests.

**`kiro_tools=["read"]`** gives the agent only Kiro's reading tools (here a directory listing, a text search and a
file reader). Everything else is simply **not there**. The agent can't even try to call a write or shell tool. If
your program also uses app tools ([chapter 08](08_App_Tools.md)) or a `result_type`
([chapter 06](06_Structured_Output.md)), add `"@app"`, the tools of your own program: `kiro_tools=["read", "@app"]`.

**`allow=["fs_read"]`** fixes a surprise. With Kiro's default agent, reading the workspace just works. But as soon
as you set any agent setting, Kiro *asks* before each read (observed on kiro-cli 2.34.0). With no policy to answer,
every ask is rejected, and the guide couldn't read anything. Allowing `fs_read` makes reads free again.

**`deny=["fs_write", "shell"]`** is belt and braces. The write and shell tools are already missing, but a deny rule
is a hard limit on top. If somebody widens the tool list later by accident, the rule still holds. We deny `shell` too
because a shell can write files as well (`echo hi > file`).

### Nothing is written to disk

pyKiro sends the agent's definition inside the request that opens the session. No file appears in the workspace or
in your `~/.kiro`. The session's `mode` becomes the agent's id, something like `pykiro-292396580cc7`.

Want one of Kiro's own agents instead? Pass `mode=`, for example `client.new_session(mode="plan")`. Chapter 05 shows
how to list the modes. `mode=` and the four settings don't mix: together they raise a `ValueError`.

## Gotchas, side effects and risks

- **`allow=["fs_read"]` also covers reads outside the workspace.** Observed on kiro-cli 2.34.0: with that rule the
  agent read a file in a *different* folder, and nothing asked. If the folder is a boundary for you, use a policy
  that checks paths instead ([chapter 10](10_Permissions.md)).
- **pyKiro doesn't check selectors or capabilities.** Observed on kiro-cli 2.34.0: a made-up selector selects
  nothing, and a typo in `deny` denies nothing. Test once that your agent really lacks what you took away.
- **A narrow `kiro_tools` hides MCP servers.** Name them, like `"@time"` for a server called `time`
  ([chapter 12](12_MCP_Servers.md)).
- **Sessions with agent settings run their turns one after another.** Observed on kiro-cli 2.34.0: default sessions
  overlapped, custom-agent sessions didn't.

## Where to next

Previous: [08 · App Tools](08_App_Tools.md). Next: [10 · Permissions](10_Permissions.md), where we write a policy that
decides every ask. Back to [all chapters](README.md).
