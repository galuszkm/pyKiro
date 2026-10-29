"""KiroClient: owns one ``kiro-cli acp --agent-engine=v3`` process and the sessions on it."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import tempfile
from collections.abc import Callable, Coroutine, Mapping, Sequence
from importlib.metadata import version
from pathlib import Path
from types import TracebackType
from typing import Any, ParamSpec, Self, TypeVar

from pydantic import BaseModel

from . import _process
from ._acp import PROTOCOL_VERSION, Rpc, _validate_initialize, method_not_found
from .content import PromptContent
from .errors import KiroError, KiroProcessError
from .events import Event, TurnResult
from .permissions import CANCELLED, PermissionPolicy
from .profile import Isolated, Profile, ProfileHome, agent_definition
from .session import Session, SessionInfo, parse_config_options
from .tools import ToolBinding, ToolServer, check_mcp_servers, collect_tools

__version__ = version("pykiro")

logger = logging.getLogger(__name__)

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _blocking(fn: Callable[_P, Coroutine[Any, Any, _R]]) -> Callable[_P, _R]:
    """``fn`` run with ``asyncio.run``, keeping its signature and docstring."""

    @functools.wraps(fn)
    def run(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        return asyncio.run(fn(*args, **kwargs))

    return run


class KiroClient:
    """Owns one Kiro process: launch, ``initialize``, sessions and shutdown. Use it as ``async with``."""

    def __init__(
        self,
        *,
        profile: Profile | None = None,
        policy: PermissionPolicy | None = None,
        cwd: str | os.PathLike[str] | None = None,
        executable: str | os.PathLike[str] | None = None,
        command: Sequence[str] | None = None,
        env: Mapping[str, str] | None = None,
        tools: Sequence[Callable[..., Any]] = (),
        startup_timeout: float = 60.0,
        forbid_workspace_config: bool = False,
    ) -> None:
        """Configure the client; start() launches Kiro.

        Args:
            profile: ``Isolated()`` (default: a temporary KIRO_HOME without the user's Kiro configuration) or
                ``Inherit()`` (the user's own ``~/.kiro``).
            policy: Decides permission asks for sessions that don't set their own: a function that returns True
                to allow the action once (default: reject every ask). An ``async def`` runs on the event loop, a
                plain function on a worker thread. Asks for the session's own app tools never reach it.
            cwd: Default working directory for sessions (default: the current directory).
            executable: Path to kiro-cli (default: kiro-cli on PATH).
            command: Run this exact command instead of ``<kiro-cli> acp --agent-engine=v3 --auth-method=cli``
                (tests, wrappers).
            env: Extra environment variables; the full parent environment is always passed.
            tools: App tools offered to every session: typed functions, plain or ``async def``. The agent sees them
                as ``@app/<tool>``.
            startup_timeout: Seconds to wait for ``initialize``.
            forbid_workspace_config: Refuse sessions whose cwd contains a ``.kiro`` entry (workspace agents,
                skills, steering, MCP servers and hooks load in every profile, and Kiro's ACP server trusts every
                workspace). Only the cwd itself is checked, not ``additional_directories`` or parent directories.

        Raises:
            TypeError: A tool can't be described to the agent.
        """
        self.profile = profile if profile is not None else Isolated()
        self.policy = policy
        self.cwd = str(Path(cwd or Path.cwd()).resolve())
        self.credits = 0.0
        self.exit_code: int | None = None
        self._executable = executable
        self._command: list[str] | None = list(command) if command else None
        self._env = env
        self._tools = collect_tools(tools)
        self._tool_server = ToolServer()
        self._startup_timeout = startup_timeout
        self._forbid_workspace_config = forbid_workspace_config
        self._state = "new"  # new -> starting -> running -> closing -> closed
        self._home: ProfileHome | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._rpc: Rpc | None = None
        self._stderr = _process.StderrBuffer()
        self._stderr_task: asyncio.Task[None] | None = None
        self._sessions: dict[str, Session] = {}
        self._loading: set[str] = set()  # ids load_session() is opening; reserved before its first await
        self._closing: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        return await self.start()

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.close()

    # ---- state ----------------------------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        """Whether Kiro has answered ``initialize`` and the client is not closing or closed."""
        return self._state == "running"

    @property
    def kiro_home(self) -> Path | None:
        """The KIRO_HOME Kiro runs with (None for Inherit)."""
        return self._home.path if self._home else None

    @property
    def pid(self) -> int | None:
        """The kiro-cli process id (also its process group id on POSIX)."""
        return self._proc.pid if self._proc else None

    def stderr_tail(self, lines: int = 40) -> str:
        """The last lines kiro-cli wrote to stderr."""
        return self._stderr.tail(lines)

    def _check_open(self) -> None:
        if self._state != "running":
            raise RuntimeError(f"the client is {self._state}")

    # ---- lifecycle ------------------------------------------------------------------------------------------
    async def start(self) -> Self:
        """Launch kiro-cli and ``initialize``.

        Raises:
            KiroError: Kiro did not negotiate the ACP v1 contract required by pyKiro.
            KiroProcessError: kiro-cli was not found, the user is not logged in, or kiro-cli exited or did not
                answer in time.
            ValueError: The client's cwd is not a directory.
        """
        if self._state != "new":
            raise RuntimeError("a KiroClient can be started once")
        if not Path(self.cwd).is_dir():
            raise ValueError(f"cwd {self.cwd} is not a directory")
        self._state = "starting"
        try:
            self._home = ProfileHome(self.profile)
            argv = self._command or [_process.find_executable(self._executable), *_process.ACP_ARGS]
            self._proc = await _process.spawn(argv, _process.build_env(self._home.path, self._env), self.cwd)
            if self._proc.stdin is None or self._proc.stdout is None or self._proc.stderr is None:
                raise RuntimeError("kiro-cli was started without stdio pipes")
            self._stderr_task = asyncio.create_task(self._stderr.pump(self._proc.stderr))
            self._rpc = Rpc(self._handle, self._proc.stdin, self._proc.stdout)
            result = await asyncio.wait_for(
                self._request(
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "clientCapabilities": {
                            "fs": {"readTextFile": False, "writeTextFile": False},
                            "terminal": False,
                        },
                        "clientInfo": {"name": "pykiro", "version": __version__},
                    },
                ),
                self._startup_timeout,
            )
            _validate_initialize(result)
        except TimeoutError:
            await self._teardown()
            raise KiroProcessError("kiro-cli did not answer initialize", stderr_tail=self._stderr.tail()) from None
        except BaseException:
            await self._teardown()
            raise
        self._state = "running"
        return self

    async def close(self) -> None:
        """Cancel running turns, stop kiro-cli and clean up. Safe to call more than once and concurrently.

        Every call returns once the cleanup has finished. Cancelling a call (e.g. ``asyncio.timeout``) doesn't
        interrupt the cleanup: the cancellation is raised once it has finished. A turn still running when kiro-cli
        stops raises KiroError("the client closed") in its stream.
        """
        if self._state == "new":
            self._state = "closed"
            return
        if self._closing is None:
            if self._state == "closed":  # a start() that failed has already cleaned up
                return
            self._state = "closing"
            self._closing = asyncio.create_task(self._close())
        closing = self._closing
        try:
            await asyncio.shield(closing)
        except asyncio.CancelledError:
            if not closing.done():
                await asyncio.wait([closing])
            raise

    async def _close(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            try:
                await asyncio.wait_for(asyncio.gather(*(s.cancel() for s in self._sessions.values())), 10)
            except Exception:
                logger.debug("pid=<%s> | running turns did not end, stopping kiro-cli", self.pid, exc_info=True)
        await self._teardown()

    async def _teardown(self) -> None:
        await self._tool_server.stop()
        if self._proc is not None:
            self.exit_code = await _process.shutdown(self._proc)
        if self._rpc is not None:
            await self._rpc.close()
        if self._stderr_task is not None:
            try:
                await asyncio.wait_for(self._stderr_task, 2)
            except Exception:
                logger.debug("pid=<%s> | stopped reading kiro-cli's stderr", self.pid, exc_info=True)
        self._sessions.clear()
        if self._home is not None:
            self._home.cleanup()
        self._state = "closed"

    # ---- JSON-RPC -------------------------------------------------------------------------------------------
    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        if self._rpc is None:
            raise RuntimeError("the client is not started")
        try:
            return await self._rpc.request(method, params)
        except ConnectionError:
            raise await self._connection_lost() from None

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        if self._rpc is not None:
            await self._rpc.notify(method, params)

    async def _connection_lost(self) -> Exception:
        """The error to raise when the connection to kiro-cli is gone."""
        if self._state in ("closing", "closed"):
            return KiroError("the client closed")
        if self._proc is None or self._stderr_task is None:
            return RuntimeError("the client is not started")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._proc.wait(), 2)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(self._stderr_task), 1)
        if self._state == "starting":
            return _process.startup_error(self._proc.returncode, self._stderr)
        return KiroProcessError(
            "kiro-cli exited unexpectedly", exit_code=self._proc.returncode, stderr_tail=self._stderr.tail()
        )

    async def _handle(self, method: str, params: dict[str, Any], is_notification: bool) -> Any:
        """Agent-to-client messages. Routing an update must not await (event ordering depends on it)."""
        session_id = params.get("sessionId")
        session = self._sessions.get(session_id) if isinstance(session_id, str) else None
        if method == "session/request_permission":
            return await session._on_permission(params) if session else CANCELLED
        if not is_notification:
            raise method_not_found(method)  # Kiro must never wait on a request we don't implement
        if session is None:
            return None
        if method == "session/update" and isinstance(params.get("update"), dict):
            session._on_update(params["update"])
        else:
            session._on_notice(method, params)
        return None

    # ---- sessions -------------------------------------------------------------------------------------------
    def _forget(self, session: Session) -> None:
        if self._sessions.get(session.id) is session:  # never another wrapper registered under the same id
            del self._sessions[session.id]
        if session._tools is not None:
            self._tool_server.unbind(session._tools)

    def _session_params(
        self,
        cwd: str | os.PathLike[str] | None,
        mode: str | None,
        mcp_servers: Sequence[Mapping[str, Any]],
        additional_directories: Sequence[str | os.PathLike[str]],
        agent: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """The ``session/new`` / ``session/load`` params, without the app's tool server."""
        path = Path(cwd).resolve() if cwd is not None else Path(self.cwd)
        if not path.is_dir():
            raise ValueError(f"session cwd {path} is not a directory")
        if self._forbid_workspace_config and (path / ".kiro").exists():
            raise ValueError(f"{path / '.kiro'} exists and forbid_workspace_config is set")
        params: dict[str, Any] = {"cwd": str(path), "mcpServers": list(mcp_servers)}
        if additional_directories:
            params["additionalDirectories"] = [str(Path(d).resolve()) for d in additional_directories]
        if agent is not None:  # without the modeId, Kiro registers the agent but doesn't select it
            params["_meta"] = {"kiro": {"modeId": agent["id"], "customAgents": [agent]}}
        elif mode is not None:
            params["_meta"] = {"kiro": {"modeId": mode}}
        return params

    async def _bind_tools(
        self,
        tools: Sequence[Callable[..., Any]] | None,
        result_type: type[BaseModel] | None,
        mcp_servers: Sequence[Mapping[str, Any]],
    ) -> ToolBinding | None:
        """Serve the client's and the session's tools (and submit_result) at a new path; None if there are none."""
        session_tools = collect_tools(tools or ())
        if both := sorted(self._tools.keys() & session_tools.keys()):
            raise ValueError(f"tools {both} are given to both the client and the session")
        if not self._tools and not session_tools and result_type is None:
            return None
        check_mcp_servers(mcp_servers)
        return await self._tool_server.bind(dict(sorted({**self._tools, **session_tools}.items())), result_type)

    async def new_session(
        self,
        cwd: str | os.PathLike[str] | None = None,
        *,
        model: str | None = None,
        effort: str | None = None,
        mode: str | None = None,
        system: str | None = None,
        kiro_tools: Sequence[str] | None = None,
        allow: Sequence[str] = (),
        deny: Sequence[str] = (),
        policy: PermissionPolicy | None = None,
        tools: Sequence[Callable[..., Any]] | None = None,
        result_type: type[BaseModel] | None = None,
        mcp_servers: Sequence[Mapping[str, Any]] = (),
        additional_directories: Sequence[str | os.PathLike[str]] = (),
    ) -> Session:
        """Create a session.

        ``system``, ``kiro_tools``, ``allow`` and ``deny`` become an agent definition sent with the session;
        nothing is written to disk.

        Args:
            cwd: The session's working directory (default: the client's).
            model: An advertised model id; validated against the session's options.
            effort: An advertised effort level for the model.
            mode: An advertised mode (a Kiro or user agent such as ``"plan"``), instead of the agent settings.
            system: The system prompt.
            kiro_tools: Kiro tool selectors the agent may use, e.g. tags (``read``, ``write``, ``shell``,
                ``web``), ``@builtin``, ``@<server>`` for one MCP server (``@app`` is the app's own tools), or ``*``
                (the default); Kiro's custom-agent documentation lists them all. Kiro gives the agent only the
                tools listed: include ``@app`` (or ``*``) when the session has app tools or a ``result_type``. A
                narrower list also leaves out the tools of ``mcp.json`` servers unless they are named with
                ``@<server>``.
            allow: Kiro capabilities allowed without asking, e.g. ``fs_read``, ``fs_write``, ``shell``,
                ``web_fetch``, ``web_search``, ``mcp``, ``skill``. The names go to Kiro unchecked.
            deny: Capabilities denied outright; a deny beats an allow in any other scope. The names go to Kiro
                unchecked: kiro-cli 2.34.0 was observed to ignore a name it doesn't know, so a typo denies nothing.
            policy: Permission policy for this session, plain or ``async def`` (default: the client's).
            tools: Extra app tools for this session.
            result_type: A Pydantic model. A turn's ``output`` is a validated instance (the agent sees validation
                errors and can submit again), or None when the turn was cancelled or refused.
            mcp_servers: Standalone MCP server definitions in ACP form, forwarded unchanged. While the session
                has app tools or a ``result_type``, the name ``app`` is reserved.
            additional_directories: Extra workspace roots: the agent reads there without asking, like in ``cwd``.

        Raises:
            ValueError: Invalid cwd, mode, model or effort, or an MCP server named ``app``.
        """
        self._check_open()
        if mode is not None and (system is not None or kiro_tools is not None or allow or deny):
            raise ValueError("pass mode= or the agent settings (system, kiro_tools, allow, deny), not both")
        agent = agent_definition(system, kiro_tools, allow, deny)
        params = self._session_params(cwd, mode, mcp_servers, additional_directories, agent)
        binding = await self._bind_tools(tools, result_type, mcp_servers)
        if binding is not None:
            params["mcpServers"].insert(0, binding.mcp_server())
        try:
            result = await self._request("session/new", params)
            result = result if isinstance(result, dict) else {}
            session_id = result.get("sessionId")
            if not isinstance(session_id, str):
                raise KiroError(f"Kiro answered session/new without a session id: {result!r}")
            options = parse_config_options(result.get("configOptions"))
        except BaseException:
            if binding is not None:
                self._tool_server.unbind(binding)
            raise
        session = Session(self, session_id, params["cwd"], policy if policy is not None else self.policy, binding)
        self._sessions[session.id] = session
        session.config_options = options
        return await self._configure(session, agent["id"] if agent else mode, model, effort)

    async def _configure(self, session: Session, mode: str | None, model: str | None, effort: str | None) -> Session:
        try:
            # Kiro silently falls back to its default agent when a mode is unknown or a profile fails to load.
            if mode is not None and session.mode != mode:
                raise ValueError(f"Kiro did not activate mode {mode!r} (session is in {session.mode!r})")
            if model is not None:
                await session._set_option("model", model)
            if effort is not None:
                await session._set_option("effortLevel", effort)
        except BaseException:
            try:
                await session.close()
            except Exception:
                logger.debug("session=<%s> | closing the unconfigured session failed", session.id, exc_info=True)
            raise
        return session

    async def load_session(
        self,
        session_id: str,
        cwd: str | os.PathLike[str] | None = None,
        *,
        model: str | None = None,
        effort: str | None = None,
        system: str | None = None,
        kiro_tools: Sequence[str] | None = None,
        allow: Sequence[str] = (),
        deny: Sequence[str] = (),
        policy: PermissionPolicy | None = None,
        tools: Sequence[Callable[..., Any]] | None = None,
        result_type: type[BaseModel] | None = None,
        mcp_servers: Sequence[Mapping[str, Any]] = (),
        additional_directories: Sequence[str | os.PathLike[str]] = (),
    ) -> Session:
        """Load a stored session; its replayed history is in ``session.history``.

        Kiro doesn't store client-owned inputs (cwd, MCP servers including the app's tools, agent settings), so
        they are sent again, including the agent definition. Takes the same options as new_session(), except
        ``mode``. Pass the agent settings the session was created with: on kiro-cli 2.34.0 a stored session was
        observed to fall back to Kiro's default agent rather than switch to another one, and the library then
        raises ValueError. ``cwd`` may differ from the original one: Kiro's tools then work in the new directory
        and the session is also listed under it, but the agent's earlier context names the original directory.

        Raises:
            ValueError: No stored session with that id, or it is already open or being loaded; an invalid cwd,
                model or effort, or an MCP server named ``app``.
        """
        self._check_open()
        if session_id in self._sessions or session_id in self._loading:
            raise ValueError(f"session {session_id} is already open")

        # Before the first await: a concurrent load of this id is refused above
        self._loading.add(session_id)

        try:
            # Kiro answers session/load for an id it never stored by creating that session.
            if session_id not in {s.session_id for s in await self.list_sessions()}:
                raise ValueError(f"no stored session {session_id} in this profile")
            agent = agent_definition(system, kiro_tools, allow, deny)
            params = self._session_params(cwd, None, mcp_servers, additional_directories, agent)
            binding = await self._bind_tools(tools, result_type, mcp_servers)
            if binding is not None:
                params["mcpServers"].insert(0, binding.mcp_server())
            policy = policy if policy is not None else self.policy
            session = Session(self, session_id, params["cwd"], policy, binding)
            self._sessions[session_id] = session  # registered first: the history arrives during the load
            session._loading = True
            try:
                result = await self._request("session/load", {**params, "sessionId": session_id})
                options = parse_config_options(result.get("configOptions") if isinstance(result, dict) else None)
            except BaseException:
                self._forget(session)
                raise
            finally:
                session._loading = False
            session.config_options = options
            return await self._configure(session, agent["id"] if agent else None, model, effort)
        finally:
            self._loading.discard(session_id)

    async def list_sessions(self, cwd: str | os.PathLike[str] | None = None) -> list[SessionInfo]:
        """Stored sessions in this profile's KIRO_HOME, optionally only those for ``cwd``."""
        self._check_open()
        params: dict[str, Any] = {"cwd": str(Path(cwd).resolve())} if cwd is not None else {}
        sessions: list[SessionInfo] = []
        while True:
            result = await self._request("session/list", params)
            page = result if isinstance(result, dict) else {}
            entries = page.get("sessions")
            infos = [SessionInfo.from_raw(e) for e in entries] if isinstance(entries, list) else []
            sessions += [info for info in infos if info is not None]
            cursor = page.get("nextCursor")
            if not isinstance(cursor, str) or not cursor:
                return sessions
            params["cursor"] = cursor

    async def delete_session(self, session_id: str) -> None:
        """Delete a stored session."""
        self._check_open()
        await self._request("session/delete", {"sessionId": session_id})


async def ask(
    prompt: PromptContent,
    *,
    cwd: str | os.PathLike[str] | None = None,
    model: str | None = None,
    effort: str | None = None,
    system: str | None = None,
    kiro_tools: Sequence[str] | None = None,
    allow: Sequence[str] = (),
    deny: Sequence[str] = (),
    tools: Sequence[Callable[..., Any]] = (),
    result_type: type[BaseModel] | None = None,
    policy: PermissionPolicy | None = None,
    profile: Profile | None = None,
    executable: str | os.PathLike[str] | None = None,
    on_event: Callable[[Event], None] | None = None,
) -> TurnResult:
    """One question, one answer: start Kiro, run one turn, stop Kiro.

    Starting Kiro takes a few seconds; programs that ask more than once should keep a KiroClient. ``ask_sync()``
    takes the same arguments and runs ``ask()`` with ``asyncio.run``, for scripts with no event loop running.

    Args:
        prompt: The prompt.
        cwd: The session's working directory (default: an empty temporary directory, so no project files are in
            its workspace).
        model: An advertised model id; validated against the session's options.
        effort: An advertised effort level for the model.
        system: The system prompt.
        kiro_tools: Kiro tool selectors the agent may use, e.g. tags (``read``, ``write``, ``shell``,
            ``web``), ``@builtin``, ``@<server>`` for one MCP server (``@app`` is the app's own tools), or ``*``
            (the default); Kiro's custom-agent documentation lists them all. Kiro gives the agent only the
            tools listed: include ``@app`` (or ``*``) when the session has app tools or a ``result_type``. A
            narrower list also leaves out the tools of ``mcp.json`` servers unless they are named with
            ``@<server>``.
        allow: Kiro capabilities allowed without asking, e.g. ``fs_read``, ``fs_write``, ``shell``,
            ``web_fetch``, ``web_search``, ``mcp``, ``skill``. The names go to Kiro unchecked.
        deny: Capabilities denied outright; a deny beats an allow in any other scope. The names go to Kiro
            unchecked: kiro-cli 2.34.0 was observed to ignore a name it doesn't know, so a typo denies nothing.
        tools: App tools: typed functions, plain or ``async def``. The agent sees them as ``@app/<tool>``.
        result_type: A Pydantic model. A turn's ``output`` is a validated instance (the agent sees validation
            errors and can submit again), or None when the turn was cancelled or refused.
        policy: Decides permission asks: a function that returns True to allow the action once (default: reject
            every ask). An ``async def`` runs on the event loop, a plain function on a worker thread. Asks for the
            session's own app tools never reach it.
        profile: ``Isolated()`` (default: a temporary KIRO_HOME without the user's Kiro configuration) or
            ``Inherit()`` (the user's own ``~/.kiro``).
        executable: Path to kiro-cli (default: kiro-cli on PATH).
        on_event: A plain function called with every event of the turn as it arrives, e.g. an
            ``AnsiRenderer()``. For async handling, use a KiroClient and iterate ``Session.stream()``.

    Raises:
        KiroError: Kiro did not negotiate the required ACP contract, the client closed, or a structured result was
            expected but not submitted.
        KiroProcessError: kiro-cli was not found, the user is not logged in, or kiro-cli exited or did not
            answer in time.
        ValueError: Invalid cwd, model or effort.
        TypeError: A tool can't be described to the agent, or ``on_event`` returned an awaitable (it is an
            ``async def``).
    """
    with tempfile.TemporaryDirectory(prefix="pykiro-cwd-") as empty_dir:
        async with KiroClient(profile=profile, policy=policy, cwd=cwd or empty_dir, executable=executable) as client:
            session = await client.new_session(
                model=model,
                effort=effort,
                system=system,
                kiro_tools=kiro_tools,
                allow=allow,
                deny=deny,
                tools=tools,
                result_type=result_type,
            )
            return await session.send(prompt, on_event=on_event)


ask_sync = _blocking(ask)
