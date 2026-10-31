---
name: library-testing
description: Write, fix and review tests for pyKiro in tests/ — unit and property tests (including the recorded Kiro transcript fixtures), integration tests with a stand-in process and the MCP endpoint against the official mcp client, and opt-in live tests against the real kiro-cli. Use when adding or changing tests or deciding what to test for a change.
---

# Library Testing

A test exists to catch a real regression in **behaviour, contract or Kiro compatibility** — never to mirror the
code or freeze its wording. If a test can break when nothing a caller depends on changed, fix or delete it.

pyKiro sits between a Python program and an external process we don't own (kiro-cli). Pure logic is tested
offline; everything that needs Kiro to answer is tested against the real kiro-cli. Concrete templates:
`references/test-patterns.md`.

---

## Core Principles — NON-NEGOTIABLE

1. **Test through the public API.** Assert on what a caller sees: returned objects, event types and fields, raised
   error *types*, files written, the text of a live reply. Pure protocol and data helpers (parsers such as
   `normalize_update`, `PermissionRequest.from_raw`, `answer_for`, `Tool`/`ToolBinding`, `agent_definition`,
   the wire-log redaction) may be called directly with input and asserted on their output. Never assert on the
   private state of the runtime objects (`KiroClient`, `Session`, `ToolServer`) or on call counts/order.
2. **There is deliberately no fake Kiro.** Pure logic is unit-tested (parsers, answers, schemas, profiles, env).
   The tool server is tested with the official `mcp` client. Process start-up failures use a one-line Python
   stand-in (`command=[sys.executable, "-c", ...]`, see `tests/integration/test_startup.py`). Everything that
   depends on Kiro's behaviour is a live test. Don't build a fake or scripted ACP agent (decided). Don't mock
   pyKiro internals, the ACP SDK, Pydantic or anyio; `unittest.mock` is a last resort.
3. **Realistic input.** Parsers are tested on the two minimal fixtures in `tests/unit/transcripts/`
   (`shell_reject.jsonl`, `load_replay.jsonl`, real Kiro messages read by `tests/unit/test_events.py`).
   Hand-write a payload only for an edge case Kiro can't easily be made to produce (unknown kinds, malformed
   shapes).
4. **Deterministic and fast.** No network beyond loopback, no real Kiro, no credits, no sleeps outside
   `tests/live/`. The default suite runs in seconds and passes with warnings as errors (`filterwarnings =
   ["error"]` in `pyproject.toml`: an unclosed pipe, transport or socket is a bug).
5. **DAMP over DRY.** Each test reads top to bottom: arrange, act, assert. Small local helpers are fine;
   fixture webs are not.
6. **Lowest layer that proves the rule.** Pure parsing → unit/property; a real subprocess or socket →
   integration; anything that needs Kiro to answer (sessions, turns, permissions) or a fact about Kiro → a live
   test with a direct assertion for that fact (related facts may share one focused scenario to save credits).

---

## Tiers and Folders

```
tests/
├── conftest.py      # anyio backend, live-test skipping
├── unit/            # pure logic, no processes: events, permissions, profiles/process helpers, tools, render, wire log,
│                    # docs fragments (test_docs.py)
│   ├── transcripts/         # minimal excerpts of Kiro V3 wire recordings: only the rows the tests read,
│   │                        # ids and times replaced, excluded from pre-commit
│   └── test_properties.py   # Hypothesis: parsers never raise on any JSON and emit fields of their declared
│                            # types, answers never invent options
├── integration/     # real subprocesses and sockets: start-up failures with a stand-in process,
│                    # the MCP endpoint (official client, raw HTTP rules, lifecycle), import boundaries
└── live/            # opt-in (KIRO_LIVE_TESTS=1): the real kiro-cli, spends credits; `model` fixture in conftest
```

- Group by concern, not by source file. Don't split one concern across files.
- Live tests declare `pytestmark = [pytest.mark.live, ...]`; the root conftest skips them unless enabled.

---

## What to Test

**Offline** (`uv run just test`, no Kiro):

- ACP initialization: protocol version 1 and every capability used by the public API are required; malformed or
  incomplete responses fail closed.
- Parsers and events from the transcripts: update kinds, tool calls, notices, replay marking, credits; malformed
  shapes become Notices that keep what is readable, and every emitted field has its declared type.
- Permission answers: only an offered option, `True` allows and anything else rejects (no policy, a raise,
  another value); malformed options offer nothing.
- Tools: schema from the signature (`Field`/`Annotated` descriptions), the docstring as description, validation
  errors and `ToolError` messages as tool errors, other exceptions only as a generic failure, threads, results,
  self-approval only when consent *and* identity match exactly, `submit_result`, the reserved `app` name.
- Agent definitions, profile homes, env, executable lookup, start-up errors (login message, exit code, stderr).
- The renderer's transcript, including dropped terminal control characters.
- The docs: every fragment a chapter quotes appears, in order, in its full program (ruff reformats any block that
  parses, so a half-statement can turn into other code); a standalone snippet says "variation" before it.
- The MCP endpoint against the official `mcp` client, and its HTTP rules (envelope, headers, Origin, a tool runs
  only for a well-formed call) and lifecycle (stop closes the listener for good).

**Live, a direct assertion per Kiro fact** (`uv run just test-live -k <name>`, spends credits; related facts may
share one scenario). The live tier is the only coverage of `session.py` and `client.py`, so the offline coverage
gate (`fail_under = 90`) omits those two files (`[tool.coverage.run] omit`): a change there needs a live test, not
an offline one:

- Turn streaming order and the `TurnResult` last; credits; a second turn waits for the first; leaving a stream
  early cancels the turn and the session continues.
- Permissions reach the policy with Kiro's consent context; the app's own tools are self-approved; a cancel answers
  a pending ask `cancelled` without waiting for the policy, and its late allow is not used.
- Lifecycle: close stops the process group quickly, is shared by concurrent callers and finishes when a caller is
  cancelled, a crash mid-turn fails the turn, close cancels running turns.
- Load with history in a new process, one load of an id at a time; agent settings (system prompt, allow/deny);
  model and effort validated against the advertised options; an unknown mode is an error.

## What NOT to Test

- Private state of the runtime objects (`_state`, `_sessions`, `_on_update`) or call counts/order on mocks.
- Exact log lines or full error messages; `match=` only a stable token (a name or id in the message).
- Pydantic, anyio, the ACP SDK or MCP behaviour on their own; dataclass defaults, `__repr__`, `__all__` contents.
- The same rule twice at two layers.

---

## Conventions

- `from __future__ import annotations`; names read as sentences (`test_cancel_answers_the_pending_ask…`).
- Async tests: `pytestmark = pytest.mark.anyio` (anyio's pytest plugin; the backend is asyncio).
- `pytest.raises(SpecificError)`; parametrize equivalent cases with readable `ids`.
- Filesystem via `tmp_path`, environment via `monkeypatch`.
- Live tests: the `model` and `effort` fixtures (`KIRO_LIVE_MODEL`, default `claude-haiku-5.5`; `KIRO_LIVE_EFFORT`,
  default `low`) on every session that sets a model, tiny prompts, an isolated
  profile unless the fact is about `Inherit()`. They may list file names under `~/.kiro` but never read Kiro's
  credential, session or log files, and never write there.
- A new transcript is a wire log (`pykiro.wire` at DEBUG, already redacted) of a run with an isolated KIRO_HOME,
  converted to one `{"t": <s>, "dir": ">>"|"<<", "msg": <json-rpc>}` object per line and cut down to the rows
  its test reads: the agent's `session/update` messages plus the request that caused them (and, for permissions,
  the ask and its answer). Leave out `initialize`, `session/new`/`session/load`/`session/list` responses, all
  `_kiro/*` notifications (they carry account and org state such as governance policy) and the process's stderr
  (versions, PID, endpoints); merge streamed text chunks into two or three; trim long option lists. Replace
  UUIDs with `00000000-0000-4000-8000-<n>`, tool-use ids with `toolu_<n>`, and shift timestamps to 2026-01-01
  (keep the spacing); paths are `/work`. Every kept row must still be a real Kiro message. Grep the result for
  personal data before committing. Steps: `references/test-patterns.md` §3.

## Verify

```bash
uv run just check && uv run just test
uv run just test-live -k <test>    # only for Kiro facts; spends credits
```
