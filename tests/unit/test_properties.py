"""Property tests: parsing any update, option list or session entry never raises, and answers never invent options."""

from __future__ import annotations

import dataclasses
import math

from hypothesis import given
from hypothesis import strategies as st

from pykiro import Event, Notice, PermissionRequest, SessionInfo, TextDelta, ToolCall
from pykiro._process import _add_loopback
from pykiro.events import context_usage, normalize_update, turn_usage
from pykiro.permissions import answer_for
from pykiro.session import parse_config_options

# Python's json module reads NaN and Infinity too, so they can reach the parsers.
scalars = (
    st.none()
    | st.booleans()
    | st.integers(-(10**6), 10**6)
    | st.floats(-1e6, 1e6)
    | st.sampled_from([math.nan, math.inf, -math.inf])
    | st.text(max_size=8)
)
json_values = st.recursive(
    scalars,
    lambda children: st.lists(children, max_size=3) | st.dictionaries(st.text(max_size=8), children, max_size=3),
    max_leaves=8,
)
# Kiro's _meta.kiro and the content shapes, with any value in any field, so the parsers' branches are reached.
kiro_meta = st.fixed_dictionaries(
    {},
    optional={
        "kind": st.sampled_from(["turn_completion", "context_usage", "turn_start"]) | json_values,
        "replay": json_values,
        "elapsedTime": json_values,
        "usagePercentage": json_values,
        "failureReason": json_values,
        "promptTurnSummaries": st.lists(
            st.fixed_dictionaries({}, optional={"usage": json_values, "unit": st.sampled_from(["credit", "token"])}),
            max_size=3,
        )
        | json_values,
    },
)
text_content = st.fixed_dictionaries({}, optional={"type": st.sampled_from(["text", "image"]), "text": json_values})
fields = {
    "content": text_content | st.lists(st.fixed_dictionaries({"content": text_content}), max_size=3) | json_values,
    "_meta": st.fixed_dictionaries({"kiro": kiro_meta}) | json_values,
    "toolCallId": json_values,
    "title": json_values,
    "kind": json_values,
    "status": json_values,
    "rawInput": json_values,
}
known_kinds = st.sampled_from(
    [
        "agent_message_chunk",
        "agent_thought_chunk",
        "tool_call",
        "tool_call_update",
        "plan",
        "config_option_update",
        "session_info_update",
        "user_message_chunk",
        "new_kind",
    ]
)
updates = st.builds(
    lambda kind, rest: {**rest, "sessionUpdate": kind},
    known_kinds | json_values,
    st.fixed_dictionaries({}, optional=fields),
)

FIELD_TYPES: dict[type, dict[str, tuple[type, ...]]] = {
    TextDelta: {"text": (str,)},
    ToolCall: {
        "tool_call_id": (str,),
        "title": (str, type(None)),
        "kind": (str, type(None)),
        "status": (str, type(None)),
        "text": (str,),
        "failure_reason": (str, type(None)),
    },
    Notice: {"method": (str,), "kind": (str, type(None)), "detail": (str, type(None)), "text": (str, type(None))},
}


config_option = st.fixed_dictionaries(
    {},
    optional={
        "id": st.sampled_from(["mode", "model"]) | json_values,
        "type": json_values,
        "options": st.lists(st.fixed_dictionaries({}, optional={"value": json_values, "options": json_values}))
        | json_values,
    },
)


session_entry = st.fixed_dictionaries(
    {}, optional=dict.fromkeys(("sessionId", "cwd", "title", "updatedAt"), json_values)
) | st.builds(lambda rest, sid: {**rest, "sessionId": sid}, st.fixed_dictionaries({}), st.text(max_size=8))


@given(st.lists(config_option | json_values, max_size=3) | json_values, session_entry | json_values)
def test_any_config_options_or_session_list_entry_parse_to_their_declared_types(options, entry):
    parsed = parse_config_options(options)
    info = SessionInfo.from_raw(entry)

    for option in parsed:
        assert isinstance(option.id, str)
        assert isinstance(option.type, str)
        assert all(isinstance(v, str) for v in option.values)
    if info is not None:
        assert isinstance(info.session_id, str)
        assert all(v is None or isinstance(v, str) for v in (info.cwd, info.title, info.updated_at))


@given(updates)
def test_any_update_normalises_to_an_event_with_fields_of_their_declared_types(update):
    event = normalize_update("s", update)
    usage = turn_usage(update)
    percent = context_usage(update)

    assert isinstance(event, Event)
    assert event.raw is update
    assert isinstance(event.replay, bool)
    values = {f.name: getattr(event, f.name) for f in dataclasses.fields(event)}
    for name, types in FIELD_TYPES[type(event)].items():
        assert isinstance(values[name], types), name
    if usage is not None:
        assert isinstance(usage[0], float)
        assert math.isfinite(usage[0])
        assert usage[1] is None or (isinstance(usage[1], int) and not isinstance(usage[1], bool))
    assert percent is None or (isinstance(percent, float) and math.isfinite(percent))


option = st.fixed_dictionaries(
    {
        "optionId": st.text(min_size=1, max_size=5),
        "kind": st.sampled_from(["allow_once", "allow_always", "reject_once", "reject_always"]),
    }
)


@given(st.lists(option, max_size=4, unique_by=lambda o: o["optionId"]), st.booleans())
def test_answer_is_an_offered_option_or_cancelled(options, allow):
    request = PermissionRequest.from_raw({"sessionId": "s", "toolCall": {"toolCallId": "t"}, "options": options})

    outcome = answer_for(request, allow=allow)["outcome"]

    if outcome["outcome"] == "selected":
        chosen = next(o for o in options if o["optionId"] == outcome["optionId"])
        assert chosen["kind"] in (("allow_once", "reject_once") if allow else ("reject_once",))
    else:
        assert outcome == {"outcome": "cancelled"}


@given(st.lists(st.sampled_from(["a.example", "localhost", "127.0.0.1", " b "]), max_size=5).map(",".join))
def test_loopback_hosts_are_present_and_existing_hosts_kept(no_proxy):
    original = [h.strip() for h in no_proxy.split(",") if h.strip()]

    hosts = _add_loopback(no_proxy).split(",")

    assert hosts[: len(original)] == original
    assert "127.0.0.1" in hosts
    assert "localhost" in hosts
    assert _add_loopback(",".join(hosts)) == ",".join(hosts)  # idempotent
