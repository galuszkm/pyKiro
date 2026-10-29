"""Normalising ACP updates into events and reading Kiro's payloads, using the recorded Kiro V3 transcripts as input."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from pykiro import Notice, SessionInfo, TextDelta, ToolCall
from pykiro.events import context_usage, normalize_update, turn_usage
from pykiro.session import parse_config_options

TRANSCRIPTS = Path(__file__).parent / "transcripts"  # sanitised Kiro V3 recordings


def recorded_updates(name: str) -> list[dict]:
    records = [json.loads(line) for line in (TRANSCRIPTS / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]
    return [
        r["msg"]["params"]["update"] for r in records if r["dir"] == "<<" and r["msg"].get("method") == "session/update"
    ]


def test_recorded_turn_maps_to_text_tool_calls_and_notices():
    updates = recorded_updates("shell_reject")

    events = [normalize_update("s", u) for u in updates]

    assert {type(e) for e in events} == {TextDelta, ToolCall, Notice}
    used, elapsed_ms = next(u for u in map(turn_usage, updates) if u is not None)
    assert used == pytest.approx(0.006258, abs=1e-6)
    assert elapsed_ms is not None


def test_failed_tool_call_carries_its_output_text():
    events = [normalize_update("s", u) for u in recorded_updates("shell_reject")]

    failed = next(e for e in events if isinstance(e, ToolCall) and e.status == "failed")
    assert "rejected" in failed.text
    assert failed.raw_input["command"] == "touch probe-file.txt"


def test_thoughts_and_lifecycle_updates_are_notices_with_kiro_kind():
    events = [normalize_update("s", u) for u in recorded_updates("shell_reject")]
    notices = [e for e in events if isinstance(e, Notice)]

    assert {"turn_start", "turn_end", "turn_completion", "context_usage"} <= {n.detail for n in notices}
    assert any(n.kind == "agent_thought_chunk" and n.text for n in notices)


def test_replayed_history_is_marked_and_current_state_is_not():
    events = [normalize_update("s", u) for u in recorded_updates("load_replay")]

    replayed = [e for e in events if e.replay]
    assert any(isinstance(e, TextDelta) for e in replayed)
    assert any(isinstance(e, Notice) and e.kind == "user_message_chunk" and e.text for e in replayed)
    assert not any(e.replay for e in events if isinstance(e, Notice) and e.kind == "config_option_update")


@pytest.mark.parametrize(
    "update",
    [
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "image", "data": "x"}},
        {"sessionUpdate": "tool_call"},
        {"sessionUpdate": "tool_call", "toolCallId": "t", "content": "not a list"},
        {"sessionUpdate": "brand_new_kind"},
        {},
    ],
    ids=["non-text-chunk", "tool-call-without-id", "malformed-tool-content", "unknown-kind", "empty"],
)
def test_anything_else_is_a_notice(update):
    assert type(normalize_update("s", update)) is Notice


REPLAYED = {"_meta": {"kiro": {"replay": True, "kind": "history"}}}


@pytest.mark.parametrize(
    "update",
    [
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": 7}, **REPLAYED},
        {"sessionUpdate": "tool_call", "toolCallId": ["t"], **REPLAYED},
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "t",
            "content": [{"content": {"type": "text"}}],
            **REPLAYED,
        },
    ],
    ids=["number-as-text", "list-as-tool-call-id", "tool-text-missing"],
)
def test_a_malformed_known_update_is_a_notice_that_keeps_what_is_readable(update):
    event = normalize_update("s", update)

    assert type(event) is Notice
    assert (event.kind, event.detail, event.replay) == (update["sessionUpdate"], "history", True)
    assert event.raw is update


def test_optional_fields_of_the_wrong_type_read_as_absent():
    update = {"sessionUpdate": "tool_call", "toolCallId": "t", "title": 3, "status": ["done"], "kind": None}

    event = normalize_update("s", update)

    assert isinstance(event, ToolCall)
    assert (event.title, event.status, event.kind, event.text) == (None, None, None, "")
    assert normalize_update("s", {"sessionUpdate": 5, "content": {"text": 1}}) == Notice(
        session_id="s", method="session/update"
    )


def usage_update(usage: object, elapsed: object = 7) -> dict:
    kiro = {"kind": "turn_completion", "elapsedTime": elapsed, "promptTurnSummaries": [{"usage": usage}]}
    return {"sessionUpdate": "session_info_update", "_meta": {"kiro": kiro}}


@pytest.mark.parametrize(
    "usage", [True, math.nan, math.inf, "0.5", None, 10**400], ids=["bool", "nan", "inf", "str", "none", "huge-int"]
)
def test_only_finite_numbers_count_as_credits(usage):
    assert turn_usage(usage_update(usage)) is None


@pytest.mark.parametrize("elapsed", ["7", 7.5, True, None], ids=repr)
def test_elapsed_time_is_whole_milliseconds_or_none(elapsed):
    assert turn_usage(usage_update(0.25, elapsed)) == (0.25, None)


@pytest.mark.parametrize(("percent", "read"), [(140.5, 140.5), (0, 0.0), (True, None), (math.nan, None), ("5", None)])
def test_context_usage_is_any_finite_number(percent, read):
    update = {
        "sessionUpdate": "session_info_update",
        "_meta": {"kiro": {"kind": "context_usage", "usagePercentage": percent}},
    }

    assert context_usage(update) == read


def test_credits_sum_only_credit_units():
    update = {
        "sessionUpdate": "session_info_update",
        "_meta": {
            "kiro": {
                "kind": "turn_completion",
                "elapsedTime": 7,
                "promptTurnSummaries": [{"usage": 1}, {"usage": 0.5, "unit": "credit"}, {"usage": 9, "unit": "token"}],
            }
        },
    }

    assert turn_usage(update) == (1.5, 7)


def test_context_usage_is_read_from_kiros_context_usage_updates():
    percents = [p for u in recorded_updates("shell_reject") if (p := context_usage(u)) is not None]

    assert percents == [1.967, 1.9792]


def test_recorded_config_options_list_their_values():
    update = next(u for u in recorded_updates("load_replay") if u["sessionUpdate"] == "config_option_update")

    options = {o.id: o for o in parse_config_options(update["configOptions"])}

    assert options["mode"].values == ("vibe", "spec")
    assert options["model"].values == ("claude-sonnet-5.5", "claude-haiku-5.5")


def test_grouped_config_option_values_are_read_one_level_deep():
    grouped = [{"group": "anthropic", "name": "Anthropic", "options": [{"value": "a"}, {"value": "b"}]}, {"value": "c"}]

    (model,) = parse_config_options([{"id": "model", "type": "select", "currentValue": "a", "options": grouped}])

    assert model.values == ("a", "b", "c")


def test_malformed_config_options_and_values_are_left_out():
    raw = [
        "model",
        {"type": "select"},
        {"id": 7},
        {"id": "model", "type": 3, "options": [{"value": "a"}, {"value": 5}, "b", {"options": "x"}]},
    ]

    (model,) = parse_config_options(raw)

    assert (model.id, model.type, model.values) == ("model", "select", ("a",))
    assert parse_config_options({"id": "model"}) == ()


def test_stored_sessions_need_a_string_id_and_read_wrong_types_as_absent():
    info = SessionInfo.from_raw({"sessionId": "s1", "cwd": 3, "title": "Plants", "updatedAt": None})

    assert info == SessionInfo("s1", None, "Plants", None)
    assert SessionInfo.from_raw({"sessionId": 1, "title": "x"}) is None
