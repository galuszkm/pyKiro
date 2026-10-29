# Project Map

Where each role lives in `src/pykiro/`. Trust the roles over exact names.

| File | Role | Read first when… |
|------|------|------------------|
| `__init__.py` | The canonical public API (`__all__`) | adding or renaming anything public |
| `client.py` | `KiroClient`: start/close, validate ACP v1 and the capabilities used by the public API, JSON-RPC routing (`_handle`), session create/load/list/delete (agent definition and at most one tool binding per session; one load of an id at a time), shared `close()`, `ask()` and `ask_sync()` (its `asyncio.run` wrapper) | the lifecycle, a new ACP method, a new session option |
| `session.py` | `Session`: config options (validated at creation), `stream`/`send`/`cancel` and the turn lock, update dispatch, permission handling; `ConfigOption`, `SessionInfo` | session state, permissions, turns, cancel semantics |
| `events.py` | Event dataclasses (`TextDelta`, `ToolCall`, `Notice`, `TurnResult`), `normalize_update`, `turn_usage`, `context_usage` | a new update kind or Kiro metadata |
| `permissions.py` | `PermissionRequest`, `answer_for`, `decide` (runs the policy: an `async def` on the event loop, a plain function on a worker thread) | permission behaviour |
| `profile.py` | `Inherit` / `Isolated`, `agent_definition` (the `customAgents` entry for a session's settings), home validation and creation (`ProfileHome`) | what Kiro loads |
| `content.py` | Prompt blocks: text, `Image`, `Resource` (reads the files a caller names) | prompt content types |
| `_process.py` | Executable lookup (explicit or PATH), environment, stderr buffer, spawn (own process group), shutdown (grace, then terminate the group) | process behaviour on any OS |
| `_acp.py` | The only ACP SDK import: `Rpc` over raw dicts, wire tracing (MCP server definitions reduced to name and type) | the protocol transport |
| `render.py` | `AnsiRenderer`: a turn's events as a terminal transcript (`on_event=` callback), control characters dropped | console output |
| `tools.py` | `Tool` (a typed function's schema via pydantic `TypeAdapter`, validated call), `collect_tools`, `ToolServer` (minimal MCP 2026-07-28 HTTP endpoint: envelope, header and Origin checks; reserved server name `app`) and per-session `ToolBinding` (secret path, self-approval, `submit_result`) | tool behaviour, serving tools, self-approval, structured results |

## Tests and tooling

| Path | Role |
|------|------|
| `tests/unit/transcripts/` | Minimal excerpts of real Kiro V3 wire recordings (`shell_reject`, `load_replay`), read by `tests/unit/test_events.py` |
| `tests/integration/` | Start-up failures with a one-line Python stand-in process; the tool server against the official `mcp` client and raw HTTP; import boundaries in a subprocess |
| `tests/live/` | A direct assertion for each Kiro fact the library relies on (related facts may share one scenario), and the only tests of the turn path (`KIRO_LIVE_TESTS=1`, spends credits) |
| `docs/` | The user documentation: `README.md` (index, API map) and numbered chapters `NN_Title.md` (what you'll learn, the big idea, one full runnable program, the story quoting whole statements from it, gotchas, where to next). Markdown only; ruff formats its Python blocks, and `tests/unit/test_docs.py` checks every quoted fragment is in the program (a snippet that isn't says "variation" before it); the code was verified by running it |
| `justfile` | `install`, `check`, `lint`, `hooks`, `test`, `test-live`, `commit`, `release-dry`, `release`, `build`, `clean` |

## Invariants

- One process per `KiroClient`; many sessions per process; one turn at a time per session (a second waits on the
  session's turn lock).
- Turns end only on the `session/prompt` response; when it succeeds, its `TurnResult` is the last event of the
  stream, and when it fails the stream raises.
- App tools: one `ToolServer` per client, at most one `ToolBinding` (secret path) per session (none without app tools
  or a `result_type`); the path is the only
  credential.
- Isolated profiles own their KIRO_HOME; ephemeral ones are deleted on close.
