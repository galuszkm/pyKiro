"""Answering permission asks: only a policy's True allows, and only offered options are ever selected."""

from __future__ import annotations

import logging
import threading

import pytest

from pykiro import PermissionRequest
from pykiro.permissions import answer_for, decide

ALLOW = {"optionId": "accept", "name": "Allow", "kind": "allow_once"}
ALWAYS = {"optionId": "always-accept", "name": "Always", "kind": "allow_always"}
REJECT = {"optionId": "reject", "name": "Deny", "kind": "reject_once"}


def request(*options: dict, **kiro: object) -> PermissionRequest:
    return PermissionRequest.from_raw(
        {"sessionId": "s", "toolCall": {"toolCallId": "t"}, "options": list(options), "_meta": {"kiro": kiro}}
    )


def selected(answer: dict) -> str:
    return answer["outcome"].get("optionId") or answer["outcome"]["outcome"]


@pytest.mark.parametrize(
    ("options", "allow", "expected"),
    [
        ((ALLOW, ALWAYS, REJECT), True, "accept"),
        ((ALLOW, ALWAYS, REJECT), False, "reject"),
        ((ALWAYS, REJECT), True, "reject"),  # allow not offered: fall back to rejecting
        ((ALWAYS,), True, "cancelled"),  # nothing safe offered: cancel
    ],
)
def test_answer_selects_an_offered_option(options, allow, expected):
    assert selected(answer_for(request(*options), allow=allow)) == expected


def test_an_allow_without_an_allow_once_option_is_rejected_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="pykiro.permissions"):
        answer = answer_for(request(ALWAYS, REJECT), allow=True)

    assert selected(answer) == "reject"
    assert [r.levelname for r in caplog.records] == ["WARNING"]


def test_server_option_ids_are_used_verbatim():
    custom = {"optionId": "yes-42", "name": "Yes", "kind": "allow_once"}

    assert selected(answer_for(request(custom), allow=True)) == "yes-42"


def test_request_exposes_kiro_consent_and_mcp_identity():
    r = request(
        ALLOW,
        consent={"capability": "mcp", "resource": "app/count"},
        mcpTool={"version": 1, "identity": {"serverName": "app", "toolName": "count"}},
    )

    assert (r.capability, r.resource, r.mcp_server, r.mcp_tool) == ("mcp", "app/count", "app", "count")


def _true_on_a_worker_thread(request: PermissionRequest) -> bool:
    assert threading.current_thread() is not threading.main_thread()
    return True


def _raising(request: PermissionRequest) -> bool:
    raise RuntimeError(request.tool_call_id)


async def _async_true_on_the_event_loop(request: PermissionRequest) -> bool:
    assert threading.current_thread() is threading.main_thread()
    return True


async def _async_yes(request: PermissionRequest) -> str:
    return "yes"


class _AsyncCallable:
    async def __call__(self, request: PermissionRequest) -> bool:
        return True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("policy", "allowed"),
    [
        (None, False),
        (_true_on_a_worker_thread, True),
        (lambda r: "yes", False),
        (_raising, False),
        (_async_true_on_the_event_loop, True),
        (_async_yes, False),
        (_AsyncCallable(), True),
        (lambda r: _async_true_on_the_event_loop(r), True),
    ],
    ids=[
        "no-policy-rejects",
        "true-allows",
        "anything-but-true-rejects",
        "raising-rejects",
        "async-true-allows",
        "async-anything-but-true-rejects",
        "async-callable-object",
        "plain-function-returning-a-coroutine",
    ],
)
async def test_only_a_policy_returning_true_allows(policy, allowed):
    assert await decide(policy, request(ALLOW, REJECT)) is allowed


@pytest.mark.parametrize(
    "params",
    [
        {"options": [ALLOW, {"name": "no id", "kind": "reject_once"}]},
        {"options": [ALLOW, "reject"]},
        {"options": {"accept": ALLOW}},
        {"options": [{**ALLOW, "optionId": 7}]},
    ],
    ids=["option-without-id", "option-not-an-object", "options-not-a-list", "option-id-not-a-string"],
)
def test_malformed_options_offer_nothing_so_the_ask_is_cancelled(params):
    malformed = PermissionRequest.from_raw({"sessionId": "s", "toolCall": {"toolCallId": "t"}, **params})

    assert malformed.options == ()
    assert answer_for(malformed, allow=True) == {"outcome": {"outcome": "cancelled"}}


def test_malformed_kiro_metadata_reads_as_absent():
    r = PermissionRequest.from_raw(
        {"sessionId": "s", "toolCall": "t", "options": [ALLOW], "_meta": {"kiro": {"consent": "mcp", "mcpTool": 1}}}
    )

    assert (r.capability, r.resource, r.mcp_server, r.mcp_tool, r.title) == (None, None, None, None, None)
    assert selected(answer_for(r, allow=True)) == "accept"
