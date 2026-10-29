"""The only module that imports the ACP SDK (agent-client-protocol).

We use the SDK's JSON-RPC connection (framing, request correlation, concurrent request handling) but not its
typed client: that layer validates incoming messages against its schema, which drops unknown update kinds and
strips unknown fields, and Kiro extends ACP through exactly those. So handlers get raw dicts.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from acp.connection import Connection, StreamDirection, StreamEvent, StreamObserver
from acp.exceptions import RequestError

from .errors import KiroError, KiroProtocolError

# ACP v1: the session/prompt response ends the turn, which Session.stream() relies on.
PROTOCOL_VERSION = 1

_REQUIRED_FLAGS = (
    ("loadSession",),
    ("promptCapabilities", "image"),
    ("promptCapabilities", "embeddedContext"),
    ("mcpCapabilities", "http"),
)
_REQUIRED_OBJECTS = (
    ("sessionCapabilities", "list"),
    ("sessionCapabilities", "delete"),
    ("sessionCapabilities", "additionalDirectories"),
    ("sessionCapabilities", "close"),
)


def _at(root: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = root
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _validate_initialize(result: Any) -> None:
    """Require the ACP v1 capabilities used by pyKiro's public API."""
    if not isinstance(result, Mapping):
        raise KiroError(f"Kiro answered initialize with {type(result).__name__}, not an object")
    version = result.get("protocolVersion")
    if isinstance(version, bool) or version != PROTOCOL_VERSION:
        raise KiroError(f"Kiro negotiated ACP protocol version {version!r}; pyKiro requires {PROTOCOL_VERSION}")
    capabilities = result.get("agentCapabilities")
    if not isinstance(capabilities, Mapping):
        raise KiroError("Kiro answered initialize without agentCapabilities")
    missing = [".".join(path) for path in _REQUIRED_FLAGS if _at(capabilities, path) is not True]
    missing += [".".join(path) for path in _REQUIRED_OBJECTS if not isinstance(_at(capabilities, path), Mapping)]
    if missing:
        raise KiroError(f"Kiro does not advertise required ACP capabilities: {', '.join(missing)}")


wire_logger = logging.getLogger("pykiro.wire")

# handler(method, params, is_notification) -> JSON-RPC result
Handler = Callable[[str, dict[str, Any], bool], Awaitable[Any]]


def method_not_found(method: str) -> Exception:
    """The exception a handler raises to answer a request with JSON-RPC -32601."""
    return RequestError.method_not_found(method)


def _redacted(message: dict[str, Any]) -> dict[str, Any]:
    """A message safe to log: each MCP server definition is reduced to its name and type.

    Every other part of a definition can carry a secret: the app's tool path in a URL, headers, a stdio
    command's arguments and environment, and fields ACP or Kiro add later.
    """
    params = message.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("mcpServers"), list):
        return message
    summaries = [
        {k: s[k] for k in ("name", "type") if isinstance(s.get(k), str)} if isinstance(s, dict) else "<redacted>"
        for s in params["mcpServers"]
    ]
    return {**message, "params": {**params, "mcpServers": summaries}}


def _trace(event: StreamEvent) -> None:
    arrow = "-->" if event.direction is StreamDirection.OUTGOING else "<--"
    wire_logger.debug("%s %s", arrow, _redacted(event.message))


class Rpc:
    """A JSON-RPC 2.0 peer over the agent process's stdin/stdout."""

    def __init__(self, handler: Handler, writer: asyncio.StreamWriter, reader: asyncio.StreamReader) -> None:
        """Connect to the agent.

        Args:
            handler: Called for every agent-to-client request and notification.
            writer: The agent's stdin.
            reader: The agent's stdout.
        """

        async def dispatch(method: str, params: Any, is_notification: bool) -> Any:
            return await handler(method, params if isinstance(params, dict) else {}, is_notification)

        observers: list[StreamObserver] | None = [_trace] if wire_logger.isEnabledFor(logging.DEBUG) else None
        self._conn = Connection(dispatch, writer, reader, observers=observers)

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        """Send a request and return its result.

        Raises:
            KiroProtocolError: Kiro answered with an error.
            ConnectionError: The connection is gone.
        """
        try:
            return await self._conn.send_request(method, params)
        except RequestError as exc:
            raise KiroProtocolError(exc.code, str(exc), exc.data) from None

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a notification."""
        await self._conn.send_notification(method, params)

    async def close(self) -> None:
        """Stop the receive loop and in-flight handlers."""
        await self._conn.close()
