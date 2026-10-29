# 12 · MCP Servers

Somebody else already built the tool you need: a time zone converter, a database client, a docs search. If it's an
MCP server, you can plug it straight into a session. In this chapter we add the official MCP time server and let the
agent use it, and nothing else.

## What you'll learn

- What MCP is, in plain words, and how an MCP server differs from your app tools.
- How to hand a session an MCP server with `mcp_servers=[...]`.
- How to approve one server in your policy, and nothing else.

## The big idea

**MCP**, the [Model Context Protocol](https://modelcontextprotocol.io), is an open standard that lets an agent use
tools from outside programs. An MCP **server** is a program that offers tools. A **stdio** server is a program Kiro
starts itself. An **http** server is already running somewhere, and Kiro connects to its URL.

An MCP server can reach your session in three ways:

| Way | Where you write it | Who gets it |
|---|---|---|
| `mcp_servers=[...]` on `new_session` | In your Python code | Only that session. This chapter. |
| `<cwd>/.kiro/settings/mcp.json` | In the workspace folder | Every session whose `cwd` is that folder. |
| Your own Kiro config in `~/.kiro` | Your Kiro setup | Only with `Inherit()` ([chapter 11](11_Project_Config_and_Profiles.md)). |

And the key fact: **an MCP call is a permission ask.** Your policy decides.

## The full code

```python
"""Plug the official MCP time server into a session and approve only its tools."""

import asyncio
import tempfile

from pykiro import AnsiRenderer, KiroClient, PermissionRequest

MODEL = "claude-haiku-5.5"
PROMPT = "What time is it in Tokyo and in New York right now? Use the time tool. Answer in two short lines."

# An MCP server in ACP form. `uvx` downloads and starts it. The version is pinned on purpose.
TIME_SERVER = {
    "name": "time",
    "command": "uvx",
    "args": ["mcp-server-time@2026.10.19"],
    "env": [],
}


def only_the_time_server(request: PermissionRequest) -> bool:
    allowed = request.capability == "mcp" and request.mcp_server == "time"

    if allowed:
        print(f"[allowed: {request.capability} {request.resource}]")
    else:
        print(f"[rejected: {request.capability} {request.resource}]")
    return allowed


async def main() -> None:
    # An empty folder for the agent to work in
    with tempfile.TemporaryDirectory() as workspace:
        # 1. Start Kiro, with our policy
        async with KiroClient(cwd=workspace, policy=only_the_time_server) as client:
            # 2. Open a session that can use the MCP server
            session = await client.new_session(model=MODEL, mcp_servers=[TIME_SERVER])

            # 3. Give the server a moment to start: a turn sent right away doesn't see its tools yet
            await asyncio.sleep(5)

            # 4. Ask something
            await session.send(PROMPT, on_event=AnsiRenderer())


if __name__ == "__main__":
    asyncio.run(main())
```

Save it as `time_server.py` and run `python time_server.py`. You also need `uvx`, which comes with
[uv](https://docs.astral.sh/uv/). One run costs about 0.02 credits. The first run is slower, because `uvx`
downloads the server once.

### What you'll see

```text
⚙ Tool Load  {"tool_ids": ["time::get_current_time", "time::convert_time"]}
✓ Tool Load
⚙ @time/get_current_time  {"tool_id": "time::get_current_time", "arguments": {"timezone": "Asia/Tokyo"}}
[allowed: mcp time/get_current_time]
⚙ @time/get_current_time  {"tool_id": "time::get_current_time", "arguments": {"timezone": "America/New_Yo…
✓ @time/get_current_time
[allowed: mcp time/get_current_time]
✓ @time/get_current_time
kiro › Tokyo: Monday, October 26, 2026, 03:27 (JST).
New York: Sunday, October 25, 2026, 14:27 (EDT).
── end_turn · 0.0181 credits · 6.9 s ──
```

This is a real run, trimmed: we cut the "thinking" lines. The lines in square brackets come from our policy, the rest
from `AnsiRenderer`.

## How it works

### The server definition

```python
TIME_SERVER = {
    "name": "time",
    "command": "uvx",
    "args": ["mcp-server-time@2026.10.19"],
    "env": [],
}
```

pyKiro forwards this to Kiro unchanged. `name` is the label: tools show up as `@time/<tool>`, and it's what your
policy checks. `command` and `args` are what Kiro starts. `env` is a list of `{"name": ..., "value": ...}` entries for
the server. The name `app` is taken: it belongs to your own app tools.

**We pinned the version on purpose.** `uvx mcp-server-time` alone would fetch the newest release every time and run
it as you. A pinned version means the code you tested is the code that runs.

An http server is just a URL. This is a variation, we didn't run it:

```python
REMOTE_SERVER = {"type": "http", "name": "docs", "url": "https://example.com/mcp"}
```

ACP also allows `headers`, for things like an API key. Keep secrets in `env` and `headers` out of your code and logs.

### The policy: approve one server

Observed on kiro-cli 2.34.0, an MCP call arrives as a `PermissionRequest` with `capability="mcp"`,
`resource="time/get_current_time"`, `mcp_server="time"` and `mcp_tool="get_current_time"`. Our policy says yes only
when *both* the capability and the server name match. Why name the server? Other tools carry an MCP identity too:
Kiro's built-in web search arrives with `mcp_server="remote"`. "Any MCP thing" would let it through.

There's a shortcut, `allow=["mcp"]`, but it approves *every tool of every* server, including ones a workspace
`mcp.json` might add. A policy that names the server is the narrower door.

### A moment to start

```python
await asyncio.sleep(5)
```

Kiro starts the server when the session opens, and that takes a few seconds (`uvx` may even download it first).
Observed on kiro-cli 2.34.0: a turn sent right away didn't see the server's tools at all. The agent said "no time tool
is available", tried the shell instead, got refused by our policy, and gave up. Waiting a few seconds is a dull fix,
and it worked in every run we tried. On the very first run, while `uvx` downloads, you may need longer.

### Tool Load, then the call

Before the first call there's a `⚙ Tool Load` line. Observed on kiro-cli 2.34.0: Kiro loads MCP tools on demand, so
the agent loads the tools it wants first, then calls them. The arguments arrive wrapped, with the tool id plus the
real arguments.

### App tools or MCP servers?

| | App tools ([chapter 08](08_App_Tools.md)) | MCP servers (this chapter) |
|---|---|---|
| Where the code runs | In your program | In a separate process, or another machine |
| Your program's variables | Directly reachable | Not reachable |
| Permission | Approved by pyKiro | A permission ask, your policy decides |
| Good for | Your own data and logic | Ready-made tools others built |

## Gotchas, side effects and risks

- **`uvx` runs code from the internet, as you.** Pin the version, and use only servers you trust. Stdio servers
  aren't sandboxed.
- **The agent doesn't have to use your server.** It may try something else, like the shell. Say so in the prompt, as
  we did ("Use the time tool"), and let the policy refuse the rest.
- **A narrow `kiro_tools` hides servers.** Add `@<server>`, like `@time` ([chapter 09](09_Custom_Agents.md)).
- **Workspace servers start by themselves.** A `.kiro/settings/mcp.json` in the `cwd` loads without you asking.

## Where to next

Previous: [11 · Project Config and Profiles](11_Project_Config_and_Profiles.md). Next:
[13 · Saving and Resuming Sessions](13_Saving_and_Resuming_Sessions.md), where a conversation survives between runs.
Back to [all chapters](README.md).
