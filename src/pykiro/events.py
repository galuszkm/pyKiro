"""Events: what happens in a session, normalised from ACP updates and Kiro's ``_meta.kiro`` extensions.

Every event carries the session id, whether it is replayed history (``session/load``), and the raw payload it
was built from. Anything without a dedicated type, including updates of an unexpected shape, is a Notice.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class Event:
    """The fields every event has: the session id, whether it is replayed history, and the raw payload."""

    session_id: str
    replay: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True, kw_only=True)
class TextDelta(Event):
    """A chunk of the agent's reply."""

    text: str


@dataclass(frozen=True, kw_only=True)
class ToolCall(Event):
    """A tool call starting (ACP ``tool_call``) or changing (``tool_call_update``); fields absent from it are None."""

    tool_call_id: str
    title: str | None = None
    kind: str | None = None
    status: str | None = None
    text: str = ""
    raw_input: Any = None
    raw_output: Any = None
    failure_reason: str | None = None  # Kiro's reason when it gives one, e.g. "denied"


@dataclass(frozen=True, kw_only=True)
class TurnResult(Event):
    """How a turn ended: the last event of ``Session.stream()`` and what ``Session.send()`` returns.

    Made from the ``session/prompt`` response, never from an update. ``stop_reason`` is Kiro's, e.g.
    ``end_turn``, ``max_tokens``, ``refusal`` or ``cancelled``. ``output`` is the validated structured result when
    the session has a ``result_type`` (None when the turn was cancelled or refused).
    """

    stop_reason: str
    text: str = ""
    credits: float = 0.0
    elapsed_ms: int | None = None
    output: Any = None


@dataclass(frozen=True, kw_only=True)
class Notice(Event):
    """Everything else: other update kinds and the other notifications Kiro sends for a session.

    ``method`` is the JSON-RPC method: ``session/update``, or any other notification Kiro sends for the session
    (e.g. ``_kiro/mcp/status``). For updates, ``kind`` is the ``sessionUpdate`` value, ``detail`` is Kiro's
    ``_meta.kiro.kind``, and ``text`` is the text of a thought or user-message chunk.
    """

    method: str
    kind: str | None = None
    detail: str | None = None
    text: str | None = None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _str(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError(f"expected a string, got {type(value).__name__}")
    return value


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"expected a number, got {type(value).__name__}")
    number = float(value)  # OverflowError for an int too large for a float
    if not math.isfinite(number):
        raise ValueError(f"expected a finite number, got {number}")
    return number


def _kiro(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(_mapping(payload.get("_meta")).get("kiro"))


def normalize_update(session_id: str, update: Mapping[str, Any]) -> Event:
    """Turn one ``session/update`` payload (its ``update`` object) into an Event."""
    kiro = _kiro(update)
    common: dict[str, Any] = {"session_id": session_id, "replay": kiro.get("replay") is True, "raw": update}
    kind = _optional_str(update.get("sessionUpdate"))
    try:
        return _normalize(kind, update, kiro, common)
    except Exception:  # noqa: BLE001 - Kiro's JSON is loosely typed: an unexpected shape is passed on, not raised
        logger.debug("update=<%s> | unexpected update shape, passing it on as a Notice", kind)
        return Notice(method="session/update", kind=kind, detail=_optional_str(kiro.get("kind")), **common)


def _normalize(kind: str | None, update: Mapping[str, Any], kiro: Mapping[str, Any], common: dict[str, Any]) -> Event:
    content = update.get("content")
    if kind == "agent_message_chunk" and _mapping(content).get("type") == "text":
        return TextDelta(text=_str(_mapping(content).get("text")), **common)
    if kind in ("tool_call", "tool_call_update"):
        return ToolCall(
            tool_call_id=_str(update.get("toolCallId")),
            title=_optional_str(update.get("title")),
            kind=_optional_str(update.get("kind")),
            status=_optional_str(update.get("status")),
            text=_tool_text(content),
            raw_input=update.get("rawInput"),
            raw_output=update.get("rawOutput"),
            failure_reason=_optional_str(kiro.get("failureReason")),
            **common,
        )
    chunk = _mapping(content).get("text") if kind in ("agent_thought_chunk", "user_message_chunk") else None
    return Notice(
        method="session/update", kind=kind, detail=_optional_str(kiro.get("kind")), text=_optional_str(chunk), **common
    )


def _tool_text(content: Any) -> str:
    """The text parts of a tool call's ``content`` list."""
    if not content:
        return ""
    if not isinstance(content, list):
        raise TypeError(f"tool call content is a {type(content).__name__}, not a list")
    parts = [_mapping(_mapping(item).get("content")) for item in content]
    return "\n".join(_str(p.get("text")) for p in parts if p.get("type") == "text")


def turn_usage(update: Mapping[str, Any]) -> tuple[float, int | None] | None:
    """Credits and elapsed milliseconds from Kiro's ``turn_completion`` update; None for any other update."""
    try:
        kiro = _kiro(update)
        if kiro.get("kind") != "turn_completion":
            return None
        summaries = kiro.get("promptTurnSummaries") or []
        total = _finite(sum((_finite(s["usage"]) for s in summaries if s.get("unit", "credit") == "credit"), 0.0))
        elapsed = kiro.get("elapsedTime")
        return total, elapsed if isinstance(elapsed, int) and not isinstance(elapsed, bool) else None
    except Exception:  # noqa: BLE001 - loosely typed, as above: report no usage rather than fail the update
        logger.debug("unexpected turn_completion shape, ignoring its usage")
        return None


def context_usage(update: Mapping[str, Any]) -> float | None:
    """Percent of the context window in use, from Kiro's ``context_usage`` update; None for any other update."""
    try:
        kiro = _kiro(update)
        if kiro.get("kind") != "context_usage":
            return None
        return _finite(kiro.get("usagePercentage"))
    except Exception:  # noqa: BLE001 - loosely typed, as above: report no usage rather than fail the update
        logger.debug("unexpected context_usage shape, ignoring it")
        return None
