"""A session: one conversation with its configuration, running one turn at a time."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import logging
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass, field
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from .content import PromptContent, to_blocks
from .errors import KiroProtocolError
from .events import Event, Notice, TextDelta, ToolCall, TurnResult, context_usage, normalize_update, turn_usage
from .permissions import CANCELLED, PermissionPolicy, PermissionRequest, answer_for, decide

if TYPE_CHECKING:
    from .client import KiroClient
    from .tools import ToolBinding

logger = logging.getLogger(__name__)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mappings(value: Any) -> list[Mapping[str, Any]]:
    return [v for v in value if isinstance(v, Mapping)] if isinstance(value, list) else []


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


@dataclass(frozen=True)
class SessionInfo:
    """A stored session, from ``session/list``."""

    session_id: str
    cwd: str | None
    title: str | None
    updated_at: str | None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_raw(cls, raw: Any) -> SessionInfo | None:
        """Parse one ``session/list`` entry; None unless it is an object with a string ``sessionId``."""
        if not isinstance(raw, Mapping) or not isinstance(raw.get("sessionId"), str):
            return None
        session_id = raw["sessionId"]
        cwd, title, updated_at = (_optional_str(raw.get(k)) for k in ("cwd", "title", "updatedAt"))
        return cls(session_id, cwd, title, updated_at, raw)


@dataclass(frozen=True)
class ConfigOption:
    """One session option as advertised by Kiro (mode, model, effortLevel, ...)."""

    id: str
    type: str
    current: Any
    values: tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)


def parse_config_options(raw: Any) -> tuple[ConfigOption, ...]:
    """ConfigOptions from an ACP ``configOptions`` list; entries without a string ``id`` are left out."""
    return tuple(
        ConfigOption(
            o["id"],
            _optional_str(o.get("type")) or "select",
            o.get("currentValue"),
            _option_values(o.get("options")),
            o,
        )
        for o in _mappings(raw)
        if isinstance(o.get("id"), str)
    )


def _option_values(options: Any) -> tuple[str, ...]:
    """A select option's string values; ACP allows them grouped one level deep."""
    values: list[str] = []
    for option in _mappings(options):
        if "value" in option:
            values += [option["value"]] if isinstance(option["value"], str) else []
        else:  # a group: {"group", "name", "options": [...]}
            values += [o["value"] for o in _mappings(option.get("options")) if isinstance(o.get("value"), str)]
    return tuple(values)


_END = object()  # queued after the last update of a turn, when Kiro answers its session/prompt


@dataclass
class _Turn:
    """The running turn: its session/prompt request and the events routed to it."""

    events: asyncio.Queue[Any]
    prompt: asyncio.Task[Any]
    credits: float = 0.0
    elapsed_ms: int | None = None
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)  # set by cancel()


class Session:
    """One conversation. Create it with KiroClient.new_session() or KiroClient.load_session().

    ``context_usage`` is the percent of the model's context window in use, from the latest report Kiro sent (it is
    not clamped to 100); None until Kiro sends one.
    """

    def __init__(
        self,
        client: KiroClient,
        session_id: str,
        cwd: str,
        policy: PermissionPolicy | None,
        tools: ToolBinding | None,
    ) -> None:
        """Wrap a session Kiro created; the client calls this."""
        self.client = client
        self.id = session_id
        self.cwd = cwd
        self.policy = policy
        self.credits = 0.0
        self.context_usage: float | None = None
        self.config_options: tuple[ConfigOption, ...] = ()
        self._history: list[Event] = []
        self._tools = tools
        self._turn: _Turn | None = None
        self._turn_lock = asyncio.Lock()
        self._loading = False
        self._tool_inputs: dict[str, Any] = {}  # Kiro's permission requests omit rawInput; the tool_call has it
        self._closed = False

    def __repr__(self) -> str:
        return f"<Session {self.id} mode={self.mode!r} model={self.model!r}>"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.close()

    # ---- configuration (set when the session is created) ----------------------------------------------------
    @property
    def history(self) -> tuple[Event, ...]:
        """Events received while the session was loading; replayed history has ``replay=True``."""
        return tuple(self._history)

    @property
    def mode(self) -> str | None:
        """The active mode (Kiro agent) id, from the advertised options."""
        return self._current("mode")

    @property
    def model(self) -> str | None:
        """The active model id, from the advertised options."""
        return self._current("model")

    @property
    def effort(self) -> str | None:
        """The active effort level, from the advertised options; None if Kiro advertises none."""
        return self._current("effortLevel")

    def _current(self, option_id: str) -> Any:
        return next((o.current for o in self.config_options if o.id == option_id), None)

    async def _set_option(self, option_id: str, value: str) -> None:
        """Set an advertised option to one of its advertised values (Kiro itself doesn't validate them)."""
        option = next((o for o in self.config_options if o.id == option_id), None)
        if option is None:
            raise ValueError(f"option {option_id!r} is not advertised: {[o.id for o in self.config_options]}")
        if option.type == "select" and value not in option.values:
            raise ValueError(f"{value!r} is not a value of {option_id!r}: {list(option.values)}")
        if option.current == value:
            return
        result = await self.client._request(
            "session/set_config_option", {"sessionId": self.id, "configId": option_id, "value": value}
        )
        if options := parse_config_options(_mapping(result).get("configOptions")):
            self.config_options = options

    # ---- turns ----------------------------------------------------------------------------------------------
    async def stream(self, content: PromptContent) -> AsyncGenerator[Event]:
        """Run one turn and yield its events; when the turn ends normally, the last one is its TurnResult.

        The turn is cancelled when the generator is closed: leaving an ``async for`` early (``break``, an
        exception, cancelling the task) does that. If you keep the generator (e.g. ``anext``), close it with
        ``contextlib.aclosing(...)`` or call ``session.cancel()``. A turn started while another one runs on this
        session waits for it.

        Raises:
            KiroProcessError: kiro-cli died during the turn.
            KiroError: The session has a result_type and the agent submitted no valid result, or the client closed.
        """
        blocks = to_blocks(content)
        await self._turn_lock.acquire()
        try:
            turn = self._begin_turn(blocks)
        except BaseException:
            self._turn_lock.release()
            raise
        text: list[str] = []
        try:
            while (event := await turn.events.get()) is not _END:
                if isinstance(event, TextDelta):
                    text.append(event.text)
                yield event
            try:
                response = _mapping(turn.prompt.result())  # raises the request's error: kiro-cli died, client closed
                stop_reason = str(response.get("stopReason") or "end_turn")
            except KiroProtocolError:
                # Kiro answers a prompt cancelled before the model starts with -32603 "This operation was aborted"
                if not turn.cancelled.is_set():
                    raise
                response, stop_reason = {}, "cancelled"
            output = self._tools.output(stop_reason) if self._tools is not None else None
            result = TurnResult(
                session_id=self.id,
                stop_reason=stop_reason,
                text="".join(text),
                credits=turn.credits,
                elapsed_ms=turn.elapsed_ms,
                output=output,
                raw=response,
            )
        finally:  # runs when the generator is closed (aclose(), or GC after a break), not when the caller stops reading
            if turn.prompt.done():
                self._end_turn()
            else:  # left early: cancel in a task that ends the turn even if this one is cancelled meanwhile
                await asyncio.shield(asyncio.create_task(self._cancel_and_end_turn()))
        yield result

    async def send(self, content: PromptContent, *, on_event: Callable[[Event], None] | None = None) -> TurnResult:
        """Run a turn to completion and return its result.

        Args:
            content: The prompt.
            on_event: A plain function called with every event of the turn as it arrives, e.g. an
                ``AnsiRenderer()``. For async handling, iterate ``stream()`` instead.

        Raises:
            TypeError: ``on_event`` returned an awaitable (it is an ``async def``); the turn is cancelled.
        """
        result = None
        async with contextlib.aclosing(self.stream(content)) as events:  # an error in on_event cancels the turn now
            async for event in events:
                if on_event is not None and inspect.isawaitable(returned := on_event(event)):
                    if inspect.iscoroutine(returned):
                        returned.close()  # never scheduled: no "never awaited" warning
                    raise TypeError("on_event must be a plain function; iterate stream() for async handling")
                if isinstance(event, TurnResult):
                    result = event
        if result is None:
            raise RuntimeError("the turn ended without a TurnResult")
        return result

    async def cancel(self) -> None:
        """Cancel the running turn, if any, and wait until Kiro has answered its prompt. The session stays usable.

        A stream still being read ends normally, with stop reason ``cancelled``.
        """
        turn = self._turn
        if turn is None or turn.prompt.done():
            return
        turn.cancelled.set()  # pending permission asks are answered "cancelled" now
        try:
            await self.client._notify("session/cancel", {"sessionId": self.id})
        except Exception:
            logger.debug("session=<%s> | could not send session/cancel", self.id, exc_info=True)
        try:
            await asyncio.shield(turn.prompt)
        except Exception:
            logger.debug("session=<%s> | the cancelled prompt ended with an error", self.id, exc_info=True)

    async def close(self) -> None:
        """Cancel the running turn and release the session in Kiro; it stays stored and loadable.

        Use ``async with`` (or call this) for sessions that end before the client does: on kiro-cli 2.34.0 every
        open session was observed to slow session creation on its client. Closing the client closes them all.
        """
        if self._closed:
            return
        await self.cancel()
        self._closed = True
        try:
            if self.client.running:
                await self.client._request("session/close", {"sessionId": self.id})
        except Exception:
            logger.debug("session=<%s> | session/close failed", self.id, exc_info=True)
        finally:
            self.client._forget(self)  # also when cancelled mid-request: _closed is set, nothing retries

    def _begin_turn(self, blocks: list[dict[str, Any]]) -> _Turn:
        self.client._check_open()
        if self._closed:
            raise RuntimeError(f"session {self.id} is closed")
        if self._tools is not None:
            blocks = self._tools.prompt_blocks(blocks)
        self._tool_inputs.clear()
        events: asyncio.Queue[Any] = asyncio.Queue()
        prompt = asyncio.create_task(self.client._request("session/prompt", {"sessionId": self.id, "prompt": blocks}))
        # Kiro sends every update of a turn before answering session/prompt, and updates are routed without awaiting, so
        # when this callback runs every event is already queued: _END is last.
        prompt.add_done_callback(lambda _: events.put_nowait(_END))
        self._turn = _Turn(events, prompt)
        return self._turn

    def _end_turn(self) -> None:
        self._turn = None
        self._turn_lock.release()

    async def _cancel_and_end_turn(self) -> None:
        try:
            await self.cancel()
        finally:
            self._end_turn()

    # ---- driven by the client -------------------------------------------------------------------------------
    def _on_update(self, update: Mapping[str, Any]) -> None:
        # Must not await: event order relies on each update being handled before the next message.
        event = normalize_update(self.id, update)
        usage = turn_usage(update)
        if usage is not None and not event.replay:
            self.credits += usage[0]
            self.client.credits += usage[0]
            if self._turn is not None:
                self._turn.credits += usage[0]
                self._turn.elapsed_ms = usage[1]
        if isinstance(event, ToolCall) and event.raw_input is not None:
            self._tool_inputs[event.tool_call_id] = event.raw_input
        if (percent := context_usage(update)) is not None:
            self.context_usage = percent
        # Kiro re-advertises options when they change (model registry reloads, mode switches); keep
        # mode/model/effort current.
        if update.get("sessionUpdate") == "config_option_update" and isinstance(update.get("configOptions"), list):
            self.config_options = parse_config_options(update["configOptions"])
        self._dispatch(event)

    def _on_notice(self, method: str, params: Mapping[str, Any]) -> None:
        self._dispatch(Notice(session_id=self.id, method=method, raw=params))

    def _dispatch(self, event: Event) -> None:
        if self._loading:
            self._history.append(event)
        elif self._turn is not None:
            self._turn.events.put_nowait(event)

    async def _on_permission(self, params: Mapping[str, Any]) -> dict[str, Any]:
        request = PermissionRequest.from_raw(params)
        if request.raw_input is None:
            request = dataclasses.replace(request, raw_input=self._tool_inputs.get(request.tool_call_id))
        turn = self._turn
        if turn is None or turn.cancelled.is_set():
            return CANCELLED  # ACP: asks during a cancel are answered "cancelled"
        if self._tools is not None and self._tools.owns(request):
            return answer_for(request, allow=True)
        allow = await self._decide_unless_cancelled(turn, request)
        if allow is None or self._turn is not turn:
            return CANCELLED
        return answer_for(request, allow=allow)

    async def _decide_unless_cancelled(self, turn: _Turn, request: PermissionRequest) -> bool | None:
        """The policy's answer, or None if the turn is cancelled first.

        ACP: a cancel answers every pending ask "cancelled" at once. A plain policy still running on its worker
        thread finishes there, and its answer is dropped.
        """
        decision = asyncio.create_task(decide(self.policy, request))
        cancelled = asyncio.create_task(turn.cancelled.wait())
        try:
            await asyncio.wait((decision, cancelled), return_when=asyncio.FIRST_COMPLETED)
        finally:
            cancelled.cancel()
            if not decision.done():
                decision.cancel()
        if turn.cancelled.is_set() or decision.cancelled():
            return None
        return decision.result()
