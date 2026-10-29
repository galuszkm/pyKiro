"""Permission asks and how they are answered.

Kiro's rules decide allow/deny first; only *ask* outcomes reach the client. A policy cannot block an action the
loaded configuration already allows, so hard restrictions belong in a session's ``deny`` rules.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeAlias

logger = logging.getLogger(__name__)

CANCELLED: dict[str, Any] = {"outcome": {"outcome": "cancelled"}}


@dataclass(frozen=True)
class PermissionOption:
    """One answer Kiro offers for an ask; the library replies with the ``option_id`` of an offered one."""

    option_id: str
    name: str
    kind: str  # allow_once | allow_always | reject_once | reject_always


@dataclass(frozen=True)
class PermissionRequest:
    """One ``session/request_permission``.

    ``capability`` and ``resource`` are Kiro's consent context (``shell`` + the command line, ``mcp`` +
    ``server/tool``); ``mcp_server`` and ``mcp_tool`` are the host-resolved MCP identity. Never authorise from
    ``title``: the model writes it.
    """

    session_id: str
    tool_call_id: str
    title: str | None
    options: tuple[PermissionOption, ...]
    raw_input: Any = None
    capability: str | None = None
    resource: str | None = None
    mcp_server: str | None = None
    mcp_tool: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_raw(cls, params: Mapping[str, Any], raw_input: Any = None) -> PermissionRequest:
        """Parse the request params; ``raw_input`` is used when Kiro's tool call carries none.

        A malformed part reads as absent; malformed options read as none offered, so the ask is answered
        ``cancelled``.
        """
        tool_call = _mapping(params.get("toolCall"))
        kiro = _mapping(_mapping(params.get("_meta")).get("kiro"))
        consent = _mapping(kiro.get("consent"))
        identity = _mapping(_mapping(kiro.get("mcpTool")).get("identity"))
        return cls(
            session_id=str(params.get("sessionId")),
            tool_call_id=str(tool_call.get("toolCallId")),
            title=_optional_str(tool_call.get("title")),
            options=_options(params.get("options")),
            raw_input=tool_call.get("rawInput", raw_input),
            capability=_optional_str(consent.get("capability")),
            resource=_optional_str(consent.get("resource")),
            mcp_server=_optional_str(identity.get("serverName")),
            mcp_tool=_optional_str(identity.get("toolName")),
            raw=params,
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _options(raw: Any) -> tuple[PermissionOption, ...]:
    """The offered options; none at all if any entry is malformed, so nothing is selected by accident."""
    if not isinstance(raw, list):
        return ()
    if not all(isinstance(o, Mapping) and isinstance(o.get("optionId"), str) for o in raw):
        return ()
    return tuple(PermissionOption(o["optionId"], str(o.get("name", "")), str(o.get("kind", ""))) for o in raw)


# Returns True to allow the action once; anything else rejects it. An async def runs on the event loop; a plain
# function on a worker thread (an awaitable it returns is awaited on the event loop).
PermissionPolicy: TypeAlias = Callable[[PermissionRequest], bool | Awaitable[bool]]


def answer_for(request: PermissionRequest, *, allow: bool) -> dict[str, Any]:
    """The ACP answer: the offered ``allow_once`` or ``reject_once`` option, never an invented one.

    An allow that wasn't offered falls back to ``reject_once`` (logged as a warning); if that wasn't offered
    either, the answer is ``cancelled``.
    """
    for kind in ("allow_once", "reject_once") if allow else ("reject_once",):
        for option in request.options:
            if option.kind == kind:
                return {"outcome": {"outcome": "selected", "optionId": option.option_id}}
        if kind == "allow_once":
            logger.warning(
                "tool_call=<%s>, offered=<%s> | no allow_once option offered, rejecting",
                request.tool_call_id,
                [o.kind for o in request.options],
            )
    return CANCELLED


async def decide(policy: PermissionPolicy | None, request: PermissionRequest) -> bool:
    """Run ``policy``: an ``async def`` on the event loop, a plain function on a worker thread.

    An awaitable the policy returns (an async callable object, a decorated ``async def``) is awaited on the event
    loop. No policy, a policy that raises, or any answer but True rejects.
    """
    if policy is None:
        return False
    try:
        answer = policy(request) if inspect.iscoroutinefunction(policy) else await asyncio.to_thread(policy, request)
        if inspect.isawaitable(answer):
            answer = await answer
        return answer is True
    except Exception:
        logger.exception("tool_call=<%s> | permission policy failed, rejecting", request.tool_call_id)
        return False
