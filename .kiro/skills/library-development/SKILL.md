---
name: library-development
description: Build and change the pyKiro library in src/pykiro — the ACP client for the Kiro CLI V3 engine (process, sessions, turns, events, permissions, profiles, app tools and their MCP endpoint). Use when adding or editing library source. Not for tests (see library-testing).
---

# Library Development

Rules for `src/pykiro/`. They describe the mental model and conventions, not a file list. Before creating anything,
read the sibling that plays the same role (`references/project-map.md`) and copy its shape.

pyKiro does one thing: **drive `kiro-cli acp --agent-engine=v3` for a Python program and get out of the way.**
Kiro owns the agent (models, tools, policy, sessions, MCP); we own the process, the configuration Kiro loads, and
the conversation (sessions, turns, events, permission answers, app tools).

---

## Core Principles — NON-NEGOTIABLE

1. **Simple over clever.** Smallest, dullest code that is correct. No speculative options, layers or "future"
   hooks. Delete code that no current feature needs.
2. **Transparency over performance.** Readable, explicit flow; optimise only with evidence.
3. **Don't reinvent the wheel.** stdlib (asyncio), the ACP SDK's JSON-RPC connection, Pydantic. Own code only where
   those demonstrably don't fit, with a one-line reason.
4. **Kiro is the source of truth.** Verify behaviour on the real engine (a live test) before coding against it;
   the live test pins the fact. Never trust docs alone: e.g. Kiro silently falls back to its default agent for an
   unknown mode, accepts any model id, and caches agent files (a file written right before `session/new` isn't
   seen).
5. **V3 only.** The library targets the Kiro CLI V3 engine and uses its features directly. At initialization it
   requires ACP v1 and every advertised capability its public API relies on, and fails closed when one is absent;
   there are no fallbacks for other agents or versions and no blocking mirror (callers use `asyncio.run`; the one
   exception is `ask_sync`, `asyncio.run(ask(...))` for scripts with no event loop).
6. **Explicit over implicit.** No globals, singletons or auto-registration; pass things as arguments.

---

## The Flow — the central mental model

```
program ──► KiroClient ──► Rpc (_acp.py: ACP SDK JSON-RPC, raw dicts) ──stdio──► kiro-cli ──► KAS (Kiro engine)
               │                                                                      │
               ├─ Session ── stream() ── Events                                       │ MCP 2026-07-28, HTTP
               └─ ToolServer (tools.py, 127.0.0.1, one secret path per session)        ◄┘
```

One turn, end to end:

1. `Session.stream()` takes the session's turn lock, sends `session/prompt` as a task, and yields the events
   routed to the turn's queue.
2. Kiro streams `session/update` notifications → `KiroClient._handle` → `Session._on_update` →
   `normalize_update` + `turn_usage` → `Session._dispatch` (the running turn's queue, or the load history).
   **Nothing on this path may `await`:** event order relies on each update being handled before the next message.
3. `session/request_permission` → `Session._on_permission`: without a running, non-cancelling turn the answer is
   `cancelled`. An ask for the session's own app tool (judged only from Kiro's consent context and host-resolved
   MCP identity; the server name `app` is reserved so no configured server can take it) is approved by the
   library; anything else goes to the `PermissionPolicy` (a plain function runs on a worker thread, an
   `async def` on the event loop; `True` allows). A cancel answers every pending ask `cancelled` at once (ACP
   requires it; Kiro then ends the turn without waiting for the policy), the policy's late answer is dropped, and
   `answer_for` picks an *offered* option.
4. The `session/prompt` response ends the turn: its task's done-callback queues the end marker after every update
   already routed, and `stream()` yields the `TurnResult` (with the binding's structured output) last. Leaving the
   loop early cancels the turn in a task that holds the turn lock until Kiro answers. Kiro's `turn_end` update is
   informational only.

Every other agent-to-client request is answered with JSON-RPC -32601 so Kiro never waits.

---

## Dependency Direction

```
foundation:  errors
data:        events · permissions · profile · content · render
transport:   _process (the kiro-cli process) · _acp (the only module importing `acp`)
runtime:     session ◄ client ──► tools
public API:  __init__.py
```

- Data modules do no network or process I/O. Their file I/O is local and explicit: `content` reads the files a
  caller passes (`Image.from_file`, `Resource.from_file`), `profile` creates and removes the KIRO_HOME, and
  `render` writes the transcript.
- Only `_acp.py` imports the ACP SDK; it exposes raw-dict JSON-RPC. Never use the SDK's typed client: it drops
  unknown update kinds and strips unknown fields.
- The core never imports `mcp` or an HTTP framework (enforced by `tests/integration/test_boundaries.py`).
- Data modules never import runtime modules.

---

## Rules That Keep It Robust

- **Kiro's JSON is loosely typed.** Read it with plain `.get()` and shallow `isinstance` checks: a field a
  consumer depends on (reply text, tool call id) must be a string, an optional string field of another type reads
  as None, and a number must be finite and not a `bool`. `normalize_update` and `turn_usage` each have one
  fallback, so an unexpected shape becomes a `Notice` (keeping its replay flag, kind and detail) or no usage, and
  never raises out of the update path. Responses are read the same way: `parse_config_options` and
  `SessionInfo.from_raw` leave out entries without a string id, so they never raise. Unknown kinds become `Notice`
  events; every event keeps its `raw` payload. No Pydantic models or enums for Kiro's messages.
- **Launch the way Kiro documents for ACP clients:** `kiro-cli acp --agent-engine=v3 --auth-method=cli`. `cli`
  (the default on kiro-cli 2.34.0) takes the engine's tokens from kiro-cli's own credential store; `client` would
  send the engine's token requests to us, and we answer those -32601.
- **Never invent permission options**; answer with an offered option id, falling back to `reject_once`, then
  `cancelled`.
- **A turn the library cancelled ends as `cancelled`.** Kiro answers a prompt cancelled before the model starts
  with JSON-RPC -32603 ("This operation was aborted") instead of a `cancelled` stop reason; `stream()` reports a
  turn it cancelled as `cancelled` either way (live test `test_closing_the_client_cancels_running_turns`).
- **Validate config values against what the session advertises** (`Session._set_option`, at session creation) —
  Kiro doesn't.
- **The process runs in its own process group**, and escalation signals the group. Signalling the group stops
  kiro-cli and kiro-cli-chat; the Node.js engine (`acp-server.js`) runs in its own group and exits on its own once
  they are gone (within 1 s, measured on kiro-cli 2.34.0). `close()` gives kiro-cli one second to exit, then
  terminates the group (on Windows: a forced `taskkill /T`) instead of waiting for the V3 engine's 5 s exit, and
  stored sessions still load afterwards (live tests `test_streaming_then_close_stops_the_whole_process_group_quickly`,
  `test_load_in_a_new_process`).
- **One cleanup per client.** `close()` runs its cleanup once, in a task every caller awaits; a cancelled caller
  waits for it before the cancellation propagates. `ToolServer.stop()` is final and shares `bind()`'s lock.
  `load_session()` reserves the id before its first await, so one id is never opened twice.
- **Pass the full parent environment** to kiro-cli (proxies, CA bundles, toolchains); the SDK's launcher keeps
  only six variables.
- **The library never writes to the user's `~/.kiro` (or anywhere) for agents:** a session's settings travel in
  `session/new` / `session/load` `_meta.kiro.customAgents`, with `modeId` equal to the definition's `id`. On
  `session/load` Kiro activates only the agent the session was created with (live test
  `test_load_in_a_new_process`).

---

## Python Conventions

- `from __future__ import annotations` in modules with annotations, and a module docstring whose first line states
  the module's job.
- Fully typed signatures; `X | None`, `list`, `dict`; never `Optional`/`Union`/`List`. Python 3.11 syntax only (no
  PEP 695 `type` statements): use `TypeAlias`.
- Docstrings on the public API; Google style `Args:` / `Returns:` / `Raises:` where they add information.
- Comments only for the non-obvious (a Kiro quirk, a protocol rule, a reason). Never narrate the code.
- Early returns; nesting ≤ 3 levels; small functions with one job.
- Kiro-side failures raise `KiroError` or one of its two subclasses: `KiroProcessError` (kiro-cli missing, not
  logged in, exited) and `KiroProtocolError` (a JSON-RPC error). Misuse raises the builtin that fits: `ValueError`
  for a bad value, `TypeError` for a wrong type, `RuntimeError` for a call in the wrong state. Message: what is
  wrong, then the valid choices. Chain with `from exc`, or `from None` for noise. The one other public exception
  is `ToolError`, which an app tool raises to give the agent a message; no further hierarchy.
- No silent `except`. Broad `except Exception` only where a failure must be surfaced elsewhere (a turn's error),
  turned into a safe answer (a failing policy rejects, a crashing tool is a generic tool error), or must not stop
  a cleanup (cancel, close), and it is logged: cleanup failures at DEBUG with `exc_info=True`. No
  `contextlib.suppress(Exception)`; narrow suppressions (`TimeoutError`, `OSError`) are control flow.
- Logging: `logger = logging.getLogger(__name__)`; `%s` args, never f-strings; field-value pairs then a message:
  `logger.warning("pid=<%s>, timeout=<%s> | kiro-cli did not exit, terminating it", pid, timeout)`.
  Wire tracing is the `pykiro.wire` logger at DEBUG and reduces every MCP server definition to its name and type
  (URLs, headers, stdio arguments and environment can all carry secrets; the app's tool path is one).
- The root `__all__` in `__init__.py` is the canonical public API. Other non-underscore names in submodules are
  importable but not promised; prefix a helper with `_` when it is meant to be private.
- Platform-specific imports (e.g. `subprocess` for Windows-only code) go inside the platform branch, so type
  checkers and editors don't strip them as unused on other platforms.
- ruff checks what it can of these conventions (the rule set is `[tool.ruff.lint]` in `pyproject.toml`); the rest
  is review. A `# noqa: <code>` needs a reason comment unless the code itself makes it obvious.

---

## Adding or Changing a Feature — Checklist

1. **Verify the Kiro behaviour first** (a live test), and keep the live test that pins the fact.
2. **Find the layer** (Dependency Direction) and read its sibling.
3. **Write the smallest code** that reads Kiro's JSON with plain `.get()` inside the existing fallbacks.
4. **Test it** per the `library-testing` skill: unit/property tests for pure logic (with the recorded transcripts
   for real Kiro messages), integration tests for the tool server and process start-up, a live test for anything
   that needs Kiro to answer. There is no fake Kiro: don't build one.
5. **Update what callers read** in the same change: the docstring, `README.md`, the affected
   `docs/NN_*.md` chapters (re-run their full code); and these skills when a rule or the mental model changes.
6. **Verify:** `uv run just check`, then `uv run just test`, then only the live tests the change needs
   (`uv run just test-live -k <name>`; they spend credits).

---

## Things NOT to Do

- Don't add options, layers, plugins or compatibility shims nobody needs today, and don't add a blocking mirror beyond `ask_sync`.
- Don't support old protocol revisions "just in case" (the tool server speaks MCP 2026-07-28 only).
- Don't import `acp` outside `_acp.py`, or `mcp`/HTTP frameworks anywhere in `src/`.
- Don't `await` while routing an update.
- Don't authorise from tool titles, model-written descriptions or MCP annotations.
- Don't trust Kiro's validation of modes, models or efforts: check against the advertised options.
- Don't read Kiro's credential, session or log files, or write into `~/.kiro`.
- Don't use `print()` for diagnostics, f-strings in logging calls, `eval`/`exec`, or `shell=True`.
- Don't leave dead code, commented-out code or narrating comments.
