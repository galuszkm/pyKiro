"""App tools: typed Python functions the agent can call, and the minimal MCP endpoint that serves them to Kiro.

A Tool describes a function to the agent (its name, its docstring, a JSON Schema of its parameters) and calls it
with validated arguments. The ToolServer runs inside the client's event loop and speaks MCP revision 2026-07-28 only
(stateless Streamable HTTP: ``server/discover``, ``tools/list``, ``tools/call``, JSON responses). The HTTP side is
the minimum Kiro needs: one POST per connection with a Content-Length body. Bound to 127.0.0.1. Each session has
its own unguessable endpoint path: it is the only credential, and it makes tool calls and structured results reach
the session that made them. The official MCP server isn't used: its ASGI app, lifespan and global tool registry
are a large fit for three methods served at a different path per session.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import re
import secrets
from collections.abc import Callable, Iterable, Mapping, Sequence
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic.errors import PydanticInvalidForJsonSchema, PydanticSchemaGenerationError, PydanticUserError
from pydantic_core import ArgsKwargs, to_jsonable_python

from .errors import KiroError, ToolError

if TYPE_CHECKING:
    from .permissions import PermissionRequest

logger = logging.getLogger(__name__)

_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class Tool:
    """A function the agent can call: its name, its docstring as the description, and a schema of its arguments.

    Parameter types, defaults and ``Field``/``Annotated`` constraints and descriptions become the JSON Schema.
    """

    def __init__(self, fn: Callable[..., Any]) -> None:
        """Describe ``fn`` to the agent.

        Raises:
            TypeError: The function can't be called by name with JSON arguments (a class, ``*args``, ``**kwargs``,
                positional-only parameters, a lambda, a builtin without a signature, a parameter type without a
                JSON Schema, or an ``x-mcp-header`` parameter).
        """
        if isinstance(fn, type):
            raise TypeError(f"tool {fn.__name__}: a class can't be a tool; pass a function")
        self.fn = fn
        self.name = getattr(fn, "__name__", "")
        if not _NAME.match(self.name):
            raise TypeError(f"tool name {self.name!r} must match {_NAME.pattern}; use a named function")
        try:
            params = inspect.signature(fn).parameters.values()
        except (TypeError, ValueError) as exc:
            raise TypeError(f"tool {self.name}: its signature can't be read") from exc
        if bad := [p.name for p in params if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD, p.POSITIONAL_ONLY)]:
            raise TypeError(f"tool {self.name}: parameter {bad[0]} can't be passed by name")
        self.description = inspect.getdoc(fn) or ""
        try:
            self._adapter: TypeAdapter[Any] = TypeAdapter(fn)  # validates the arguments, then calls fn
            self.input_schema = self._adapter.json_schema()
        except (PydanticSchemaGenerationError, PydanticInvalidForJsonSchema, PydanticUserError) as exc:
            raise TypeError(f"tool {self.name}: a parameter type has no JSON Schema: {exc}") from exc
        if _contains_key(self.input_schema, "x-mcp-header"):  # MCP would require mirroring it into HTTP headers
            raise TypeError(f"tool {self.name}: x-mcp-header parameters are not supported")

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"

    async def invoke(self, arguments: Mapping[str, Any] | None) -> Any:
        """Validate the agent's arguments and run the function.

        ``async def`` functions run on the event loop, plain ones (and their validation) on a worker thread. An
        awaitable a plain function returns (a decorated ``async def``, say) is awaited on the event loop.

        Raises:
            pydantic.ValidationError: The arguments don't match the signature.
        """
        call = ArgsKwargs((), dict(arguments or {}))
        if inspect.iscoroutinefunction(self.fn):
            result = self._adapter.validate_python(call)
        else:
            result = await asyncio.to_thread(self._adapter.validate_python, call)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def call(self, arguments: Mapping[str, Any] | None) -> dict[str, Any]:
        """Run the tool for the agent; errors become ``isError`` results the agent can react to.

        The agent sees why its arguments are invalid and the message of a ToolError. Any other exception, and a
        result that isn't valid JSON (NaN, infinity), is logged and reaches the agent as a generic failure.
        """
        try:
            value = to_jsonable_python(await self.invoke(arguments))
            text = value if isinstance(value, str) else json.dumps(value, allow_nan=False)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            return _error(f"Invalid arguments: {problems}")
        except ToolError as exc:
            return _error(str(exc))
        except Exception:
            logger.exception("tool=<%s> | tool failed, the agent is told only that it failed", self.name)
            return _error(f"The tool {self.name} failed.")
        result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": False}
        if isinstance(value, dict):
            result["structuredContent"] = value
        return result


def _error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _contains_key(node: Any, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(_contains_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_contains_key(v, key) for v in node)
    return False


def collect_tools(functions: Iterable[Callable[..., Any]]) -> dict[str, Tool]:
    """Tools by name, sorted.

    Raises:
        TypeError: An item isn't a function the agent can call.
        ValueError: Two functions share a name.
    """
    tools: dict[str, Tool] = {}
    for fn in functions:
        if not callable(fn):
            raise TypeError(f"{fn!r} is not a function")
        tool = Tool(fn)
        if tools.setdefault(tool.name, tool) is not tool:
            raise ValueError(f"two tools are named {tool.name!r}")
    return dict(sorted(tools.items()))


# ---- serving tools to Kiro -----------------------------------------------------------------------------------------
MCP_VERSION = "2026-07-28"
VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
SERVER_NAME = "app"  # the app's tools appear to the agent as @app/<tool>; reserved while a session has app tools
SERVER_INFO = {"io.modelcontextprotocol/serverInfo": {"name": "pykiro", "version": "1"}}
RESULT_TOOL = "submit_result"
MAX_BODY = 16 * 1024 * 1024
READ_TIMEOUT = 10.0  # seconds to read a request's head, and again its body; a tool call itself has no limit
_BASE64_HEADER = re.compile(r"=\?base64\?([A-Za-z0-9+/]*={0,2})\?=")  # MCP's encoding of a header-unsafe value


def check_mcp_servers(mcp_servers: Sequence[Mapping[str, Any]]) -> None:
    """Refuse a configured MCP server named like the app's own.

    Kiro keeps the last of two servers with one name, so it would replace the app's server, and its tools would
    be approved like the app's.

    Raises:
        ValueError: A server is named ``app``.
    """
    if any(isinstance(s, Mapping) and s.get("name") == SERVER_NAME for s in mcp_servers):
        raise ValueError(f"the MCP server name {SERVER_NAME!r} is reserved for the app's tools")


class ToolBinding:
    """The tools one session offers, at its own endpoint path, plus ``submit_result`` when it has a result_type."""

    def __init__(self, server: ToolServer, tools: dict[str, Tool], result_type: type[BaseModel] | None) -> None:
        """Bind ``tools`` to a new path.

        Raises:
            TypeError: ``result_type`` is not a Pydantic model.
            ValueError: A tool is named ``submit_result`` while a result_type is set.
        """
        self.server = server
        self.key = secrets.token_urlsafe(32)
        self.result_type = result_type
        self.submitted: BaseModel | None = None
        if result_type is not None:
            if not (isinstance(result_type, type) and issubclass(result_type, BaseModel)):
                raise TypeError(f"result_type must be a pydantic BaseModel subclass, got {result_type!r}")
            if RESULT_TOOL in tools:
                raise ValueError(f"the tool name {RESULT_TOOL!r} is reserved for structured results")
            tools = {**tools, RESULT_TOOL: Tool(self._submit_result_tool(result_type))}
        self.tools = tools

    def _submit_result_tool(self, result_type: type[BaseModel]) -> Any:
        async def submit_result(result: Any) -> str:
            """Submit the final result of the task. Call this exactly once, when the result is complete."""
            self.submitted = result
            return "Result recorded."

        # Tool() builds the schema from the annotations: re-annotate the closure so pydantic validates result_type.
        submit_result.__annotations__ = {"result": result_type, "return": str}
        return submit_result

    def mcp_server(self) -> dict[str, Any]:
        """The ACP HTTP MCP server entry for ``session/new`` and ``session/load``."""
        return {
            "type": "http",
            "name": SERVER_NAME,
            "url": f"http://127.0.0.1:{self.server.port}/mcp/{self.key}",
            "headers": [],
        }

    def owns(self, request: PermissionRequest) -> bool:
        """An ask for one of these tools, judged only from Kiro's consent context and host-resolved identity."""
        return (
            request.capability == "mcp"
            and request.mcp_server == SERVER_NAME
            and request.mcp_tool in self.tools
            and request.resource == f"{SERVER_NAME}/{request.mcp_tool}"
        )

    def prompt_blocks(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The turn's prompt, plus the instruction to call ``submit_result`` when a result is expected."""
        if self.result_type is None:
            return blocks
        self.submitted = None
        instruction = (
            f"When you have the final result, call the `{RESULT_TOOL}` tool exactly once with "
            "the result as its `result` argument."
        )
        return [*blocks, {"type": "text", "text": instruction}]

    def output(self, stop_reason: str) -> BaseModel | None:
        """The turn's structured result. Cancelled and refused turns end without one.

        Raises:
            KiroError: A result was expected and the agent submitted no valid one.
        """
        if self.result_type is None or self.submitted is not None or stop_reason in ("cancelled", "refusal"):
            return self.submitted
        raise KiroError(
            f"the agent did not submit a valid {self.result_type.__name__}; "
            "if the session sets kiro_tools, it must include '@app'"
        )


class ToolServer:
    """One loopback HTTP endpoint per client; sessions bind their tool sets to it."""

    def __init__(self) -> None:
        """Create a stopped server; the first bind() starts it."""
        self.port = 0
        self._server: asyncio.Server | None = None
        self._bindings: dict[str, ToolBinding] = {}
        self._handlers: set[asyncio.Task[Any]] = set()
        self._lock = asyncio.Lock()  # bind() and stop() run one at a time
        self._stopped = False

    async def bind(self, tools: dict[str, Tool], result_type: type[BaseModel] | None) -> ToolBinding:
        """Serve ``tools`` for one session; the first bind starts the listener.

        Raises:
            RuntimeError: The server was stopped.
        """
        binding = ToolBinding(self, tools, result_type)
        async with self._lock:
            if self._stopped:
                raise RuntimeError("the tool server is stopped")
            if self._server is None:
                self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
                self.port = self._server.sockets[0].getsockname()[1]
            self._bindings[binding.key] = binding
        return binding

    def unbind(self, binding: ToolBinding) -> None:
        """Stop serving a session's tools: its path answers 404 from now on."""
        self._bindings.pop(binding.key, None)

    async def stop(self) -> None:
        """Close the endpoint for good: forget every binding and cancel the requests still being handled.

        A plain-function tool already running on a worker thread finishes there; its result is dropped.
        """
        async with self._lock:
            self._stopped = True
            self._bindings.clear()
            server, self._server = self._server, None
            if server is None:
                return
            server.close()
            handlers = list(self._handlers)
            for task in handlers:
                task.cancel()
            await asyncio.gather(*handlers, return_exceptions=True)
            await server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("the tool server handler must run in a task")
        self._handlers.add(task)
        try:
            async with asyncio.timeout(READ_TIMEOUT):
                head = await reader.readuntil(b"\r\n\r\n")
            request_line, *header_lines = head.decode("latin-1").split("\r\n")
            method, target, _ = request_line.split(" ", 2)
            headers = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in header_lines if h)}
            length = int(headers.get("content-length", "0"))
            binding = self._bindings.get(urlsplit(target).path.removeprefix("/mcp/"))
            origin = headers.get("origin")
            # refused before the body is read: a wrong path or a huge body must not hold this task or the memory
            if origin is not None and origin not in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"):
                status, payload = 403, _rpc_error(None, -32600, "origin not allowed")  # MCP: against DNS rebinding
            elif binding is None:
                status, payload = 404, b""
            elif method != "POST":
                status, payload = 405, b""
            elif length > MAX_BODY:
                status, payload = 413, b""
            else:
                async with asyncio.timeout(READ_TIMEOUT):
                    body = await reader.readexactly(length)
                status, payload = await self._respond(binding, body, headers)
            writer.write(
                f"HTTP/1.1 {status} {HTTPStatus(status).phrase}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        except (ConnectionError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            logger.debug("tool server: dropped a malformed, slow or aborted request")
        finally:
            self._handlers.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                logger.debug("tool server: the connection closed with an error", exc_info=True)

    async def _respond(self, binding: ToolBinding, body: bytes, headers: Mapping[str, str]) -> tuple[int, bytes]:
        try:
            message = json.loads(body, parse_constant=_reject_constant)
        except ValueError:
            return 400, _rpc_error(None, -32700, "the body is not valid JSON")
        if not (
            isinstance(message, dict) and message.get("jsonrpc") == "2.0" and isinstance(message.get("method"), str)
        ):
            return 400, _rpc_error(None, -32600, "expected one JSON-RPC 2.0 request")
        notification = "id" not in message
        rid = None if notification else message["id"]
        if not notification and (isinstance(rid, bool) or not isinstance(rid, str | int)):
            return 400, _rpc_error(None, -32600, "the request id must be a string or an integer")
        rpc_method, params = message["method"], message.get("params", {})
        if not isinstance(params, dict):
            return 400, _rpc_error(rid, -32602, "params must be an object")
        meta = params.get("_meta")
        version = meta.get(VERSION_KEY) if isinstance(meta, dict) else None
        if problem := _header_problem(headers, version, rpc_method, params):
            return 400, _rpc_error(rid, -32020, problem)
        if version != MCP_VERSION:
            return 400, _rpc_error(
                rid,
                -32022,
                f"unsupported protocol version {version}",
                {"supported": [MCP_VERSION], "requested": version},
            )
        if notification:
            return 202, b""
        if rpc_method == "server/discover":
            result: dict[str, Any] = {
                "supportedVersions": [MCP_VERSION],
                "capabilities": {"tools": {}},
                "ttlMs": 0,
                "cacheScope": "private",
            }
        elif rpc_method == "tools/list":
            result = {
                "tools": [
                    {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                    for t in binding.tools.values()
                ],
                "ttlMs": 0,
                "cacheScope": "private",
            }
        elif rpc_method == "tools/call":
            name, arguments = params.get("name"), params.get("arguments")
            selected = binding.tools.get(name) if isinstance(name, str) else None
            if selected is None:
                return 400, _rpc_error(rid, -32602, f"unknown tool {name!r}")
            if arguments is not None and not isinstance(arguments, dict):
                return 400, _rpc_error(rid, -32602, "the tool arguments must be an object")
            result = await selected.call(arguments)
        else:
            return 404, _rpc_error(rid, -32601, f"method not found: {rpc_method}")
        reply = {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {**result, "resultType": "complete", "_meta": SERVER_INFO},
        }
        return 200, json.dumps(reply, allow_nan=False).encode()


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _header_value(value: str | None) -> str | None:
    """A header value, decoded if MCP base64-encoded it; None for a malformed encoding."""
    match = _BASE64_HEADER.fullmatch(value or "")
    if match is None:
        return value
    try:
        return base64.b64decode(match[1], validate=True).decode("utf-8")
    except ValueError:
        return None


def _header_problem(headers: Mapping[str, str], version: Any, method: str, params: Mapping[str, Any]) -> str | None:
    """Why the MCP request headers don't match the body, or None (MCP: they are required and must match)."""
    if "mcp-protocol-version" not in headers or headers["mcp-protocol-version"] != version:
        return "the MCP-Protocol-Version header is missing or doesn't match the request's protocol version"
    if headers.get("mcp-method") != method:
        return "the Mcp-Method header is missing or doesn't match the request's method"
    if method == "tools/call" and _header_value(headers.get("mcp-name")) != params.get("name"):
        return "the Mcp-Name header is missing or doesn't match the tool name"
    return None


def _rpc_error(rid: Any, code: int, message: str, data: Any = None) -> bytes:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return json.dumps({"jsonrpc": "2.0", "id": rid, "error": error}).encode()
