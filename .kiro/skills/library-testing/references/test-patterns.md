# Test Patterns

Copy-paste shapes for the rules in `SKILL.md`. Adapt names to the current public API (`pykiro/__init__.py`).

## 1. Integration test with a stand-in process

For what the library does when the process itself misbehaves (exits, writes to stderr, never answers). The
stand-in is a one-line Python program; it speaks no ACP, so it can't create a session.

```python
import sys

import pytest

from pykiro import KiroClient, KiroProcessError

pytestmark = pytest.mark.anyio


async def test_exit_before_initialize_is_a_process_error_with_stderr(tmp_path):
    command = [sys.executable, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(7)"]
    with pytest.raises(KiroProcessError) as caught:
        await KiroClient(command=command, cwd=tmp_path).start()

    assert caught.value.exit_code == 7
    assert "boom" in caught.value.stderr_tail
```

## 2. Unit test over a recorded transcript

Each line of `tests/unit/transcripts/<name>.jsonl` is `{"t": <seconds>, "dir": ">>" | "<<", "msg": <json-rpc>}`
(`>>` client to Kiro, `<<` Kiro to client). Tests read the agent's `session/update` payloads:

```python
TRANSCRIPTS = Path(__file__).parent / "transcripts"


def recorded_updates(name: str) -> list[dict]:
    records = [json.loads(line) for line in (TRANSCRIPTS / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]
    return [
        r["msg"]["params"]["update"] for r in records if r["dir"] == "<<" and r["msg"].get("method") == "session/update"
    ]


def test_failed_tool_call_carries_its_output_text():
    events = [normalize_update("s", u) for u in recorded_updates("shell_reject")]

    failed = next(e for e in events if isinstance(e, ToolCall) and e.status == "failed")
    assert "rejected" in failed.text
```

## 3. Capturing a transcript (spends a few credits)

There are no recording scripts; a transcript is cut by hand from a wire log, following the convention in
`SKILL.md`:

1. Run the scenario with `KiroClient(profile=Isolated(<tmp dir>), cwd=<empty tmp dir>)` and the cheap model, with
   `logging.getLogger("pykiro.wire")` at DEBUG writing to a file. The wire log already reduces MCP server
   definitions to their name and type.
2. Convert each logged message to one `{"t", "dir", "msg"}` line (`-->` is `>>`, `<--` is `<<`; `t` is seconds
   since the first message). The log prints each message as a Python dict; `ast.literal_eval` reads it back.
3. Keep only the rows the test reads (the `session/update` messages, the request that caused them, a permission
   ask and its answer) and drop the rest, as `SKILL.md` lists. Merge streamed chunks, trim long option lists.
4. Replace ids and timestamps and the paths, then grep the file for personal data (account, org, host or user
   names, real UUIDs, `toolu_bdrk_`, real dates) before committing. Check every kept row is still a real message.

## 4. Property test for a parser

```python
from hypothesis import given, strategies as st


@given(updates)  # session/update payloads with known and unknown kinds and arbitrary JSON values
def test_any_update_normalises_to_an_event(update):
    assert isinstance(normalize_update("s", update), Event)
```

Assert an invariant (never raises, never invents an option, idempotent), not a re-implementation.

## 5. MCP conformance with the official client

```python
async def test_official_mcp_client_lists_and_calls_tools(server):
    binding = await server.bind(collect_tools([word_count]), None)
    async with mcp_client(binding.mcp_server()["url"], "auto") as client:
        result = await client.call_tool("word_count", {"text": "a b"})

    assert result.content[0].model_dump()["text"] == "2"
```

`mcp` is a dev dependency only; the library never imports it.

## 6. Live test (one Kiro fact, cheapest model)

```python
pytestmark = [pytest.mark.live, pytest.mark.anyio, pytest.mark.timeout(240)]


async def test_unknown_mode_is_an_error(tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        with pytest.raises(ValueError, match="no-such-mode"):
            await client.new_session(mode="no-such-mode")  # Kiro itself falls back silently
```

Use the `model` fixture (`KIRO_LIVE_MODEL`, default `claude-haiku-5.5`) and tiny prompts.

## Decision guide

| Testing… | Tier | Kiro stand-in | Assert on |
|----------|------|---------------|-----------|
| a parser / pure function | `unit/` (+ property) | none | value, invariant, error type |
| compatibility with real messages | `unit/` | transcript fixture | events, parsed values |
| a tool's schema or call | `unit/` | none | schema, result, error type |
| a process that fails to start | `integration/` | one-line Python stand-in | error type, exit code, stderr |
| the MCP endpoint | `integration/` | official `mcp` client | responses, HTTP status |
| client/session/turn behaviour | `live/` | the real kiro-cli | events, results, error types |
| a fact about Kiro | `live/` | the real kiro-cli | the fact |
