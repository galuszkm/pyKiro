"""App tools: schemas from signatures, argument validation, execution, results, self-approval and submit_result."""

from __future__ import annotations

import dataclasses
import enum
import functools
import logging
import math
import socket
import threading
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field, ValidationError

from pykiro import KiroError, PermissionRequest, ToolError
from pykiro.tools import Tool, ToolBinding, ToolServer, check_mcp_servers, collect_tools

pytestmark = pytest.mark.anyio


class Order(BaseModel):
    sku: str
    qty: int = Field(gt=0)


class Colour(enum.Enum):
    RED = "red"
    BLUE = "blue"


@dataclasses.dataclass
class Point:
    x: float
    y: float


def word_count(text: Annotated[str, Field(description="The text to analyse.")], unique: bool = False) -> int:
    """Count words in a text."""
    return len(set(text.split())) if unique else len(text.split())


async def place_order(order: Order, note: str | None = None) -> dict:
    """Place an order."""
    return {"order_id": "A-1", "qty": order.qty, "note": note}


def test_schema_from_signature():
    tool = Tool(word_count)

    assert (tool.name, tool.description) == ("word_count", "Count words in a text.")
    assert tool.input_schema["required"] == ["text"]
    assert tool.input_schema["additionalProperties"] is False
    assert tool.input_schema["properties"]["text"]["description"] == "The text to analyse."
    assert tool.input_schema["properties"]["unique"]["default"] is False


def test_the_whole_docstring_is_the_description():
    def double(a: int) -> int:
        """Double a number.

        Args:
            a: The number.
        """
        return 2 * a

    assert Tool(double).description == "Double a number.\n\nArgs:\n    a: The number."


async def test_field_defaults_keep_their_description_and_constraints():
    def lookup(code: str = Field(pattern=r"^[A-Z]{3}$", description="Three upper-case letters."), n: int = 2) -> str:
        """Look up."""
        return code * n

    tool = Tool(lookup)

    assert tool.input_schema["required"] == ["code"]
    assert tool.input_schema["properties"]["code"]["description"] == "Three upper-case letters."
    assert await tool.invoke({"code": "ABC"}) == "ABCABC"
    with pytest.raises(ValidationError):
        await tool.invoke({"code": "abc"})


def test_models_dataclasses_enums_literals_and_constraints():
    def move(p: Point, c: Colour, mode: Literal["fast", "slow"] = "fast", n: Annotated[int, Field(ge=1, le=5)] = 1):
        """Move."""

    order_schema, move_schema = Tool(place_order).input_schema, Tool(move).input_schema

    assert order_schema["properties"]["order"] == {"$ref": "#/$defs/Order"}
    assert order_schema["$defs"]["Order"]["properties"]["qty"]["exclusiveMinimum"] == 0
    assert move_schema["$defs"]["Point"]["required"] == ["x", "y"]
    assert move_schema["$defs"]["Colour"]["enum"] == ["red", "blue"]
    assert move_schema["properties"]["mode"]["enum"] == ["fast", "slow"]
    assert (move_schema["properties"]["n"]["minimum"], move_schema["properties"]["n"]["maximum"]) == (1, 5)


async def test_parameter_names_that_clash_with_pydantic_work():
    def f(schema: str, json: int, model_config: str = "p") -> str:
        """F."""
        return f"{schema}{json}{model_config}"

    tool = Tool(f)

    assert set(tool.input_schema["properties"]) == {"schema", "json", "model_config"}
    assert await tool.invoke({"schema": "s", "json": 1}) == "s1p"


def _varargs(*args: int) -> None: ...
def _varkw(**kwargs: int) -> None: ...
def _positional(a: int, /) -> None: ...
def _no_schema(s: socket.socket) -> None: ...


@pytest.mark.parametrize(
    "fn",
    [_varargs, _varkw, _positional, _no_schema, lambda: None],
    ids=["varargs", "varkw", "positional-only", "no-schema", "lambda"],
)
def test_functions_the_agent_cant_call_are_rejected(fn):
    with pytest.raises(TypeError):
        Tool(fn)


async def test_plain_functions_run_on_a_worker_thread_and_async_ones_on_the_loop():
    threads = {}

    def sync_tool() -> None:
        threads["sync"] = threading.current_thread()

    async def async_tool() -> None:
        threads["async"] = threading.current_thread()

    await Tool(sync_tool).invoke({})
    await Tool(async_tool).invoke({})

    assert threads["sync"] is not threading.current_thread()
    assert threads["async"] is threading.current_thread()


async def test_arguments_are_validated_and_coerced():
    count, order = Tool(word_count), Tool(place_order)

    assert await count.invoke({"text": "a b", "unique": "true"}) == 2
    with pytest.raises(ValidationError):
        await count.invoke({"text": "a", "extra": 1})
    with pytest.raises(ValidationError):
        await order.invoke({"order": {"sku": "X", "qty": 0}})


async def test_results_for_the_agent():
    def out_of_stock() -> None:
        raise ToolError("sku X is out of stock")

    ok = await Tool(place_order).call({"order": {"sku": "X", "qty": 1}})
    invalid = await Tool(place_order).call({"order": {"sku": "X", "qty": 0}})
    refused = await Tool(out_of_stock).call({})

    assert ok["isError"] is False
    assert ok["structuredContent"]["order_id"] == "A-1"
    assert invalid["isError"] is True
    assert "order.qty" in invalid["content"][0]["text"]
    assert refused["isError"] is True
    assert refused["content"][0]["text"] == "sku X is out of stock"


async def test_an_unexpected_failure_is_logged_but_the_agent_sees_no_details(caplog):
    def connect() -> None:
        raise ConnectionError("postgres://admin:hunter2@db/prod refused")

    with caplog.at_level(logging.ERROR, logger="pykiro.tools"):
        failed = await Tool(connect).call({})

    assert failed["isError"] is True
    assert "hunter2" not in failed["content"][0]["text"]
    assert "connect" in failed["content"][0]["text"]
    assert any("hunter2" in (r.exc_text or "") for r in caplog.records)


async def test_a_result_that_isnt_valid_json_is_a_tool_error(caplog):
    def ratio() -> dict:
        return {"value": math.nan}

    with caplog.at_level(logging.CRITICAL, logger="pykiro.tools"):
        result = await Tool(ratio).call({})

    assert result["isError"] is True
    assert "structuredContent" not in result


async def test_awaitables_from_wrapped_or_callable_async_tools_are_awaited():
    async def double(n: int) -> int:
        return 2 * n

    @functools.wraps(double)
    def wrapped(*args: int, **kwargs: int) -> object:  # a sync decorator around an async def
        return double(*args, **kwargs)

    assert await Tool(wrapped).invoke({"n": 2}) == 4
    assert (await Tool(wrapped).call({"n": 3}))["content"][0]["text"] == "6"


class Product(BaseModel):
    name: str


@pytest.mark.parametrize("fn", [Product, Point, dict, vars], ids=["model-class", "dataclass", "builtin-type", "vars"])
def test_classes_and_builtins_without_a_signature_are_rejected(fn):
    with pytest.raises(TypeError):
        Tool(fn)


def test_a_parameter_mirrored_into_an_mcp_header_is_rejected():
    def regional(region: Annotated[str, Field(json_schema_extra={"x-mcp-header": "Region"})]) -> str:
        """Regional."""
        return region

    with pytest.raises(TypeError, match="x-mcp-header"):
        Tool(regional)


@pytest.mark.parametrize(
    ("servers", "refused"),
    [
        ([{"type": "http", "name": "app", "url": "http://example.com/mcp", "headers": []}], True),
        ([{"name": "app", "command": "app-server", "args": [], "env": []}], True),
        ([{"type": "http", "name": "apps", "url": "http://example.com/mcp", "headers": []}], False),
        ([], False),
    ],
    ids=["http-app", "stdio-app", "other-name", "none"],
)
def test_a_configured_mcp_server_cant_take_the_apps_name(servers, refused):
    if refused:
        with pytest.raises(ValueError, match="app"):
            check_mcp_servers(servers)
    else:
        check_mcp_servers(servers)


def test_collect_tools_sorts_by_name_and_rejects_duplicates_and_non_functions():
    def other_word_count() -> None: ...

    other_word_count.__name__ = "word_count"

    assert list(collect_tools([word_count, place_order])) == ["place_order", "word_count"]
    with pytest.raises(ValueError, match="word_count"):
        collect_tools([word_count, other_word_count])
    with pytest.raises(TypeError):
        collect_tools(["word_count"])  # ty: ignore[invalid-argument-type]


def tool_ask(server: str, tool: str, resource: str) -> PermissionRequest:
    kiro = {
        "consent": {"capability": "mcp", "resource": resource},
        "mcpTool": {"identity": {"serverName": server, "toolName": tool}},
    }
    return PermissionRequest.from_raw(
        {"sessionId": "s", "toolCall": {"toolCallId": "t"}, "options": [], "_meta": {"kiro": kiro}}
    )


@pytest.mark.parametrize(
    ("server", "tool", "resource", "owned"),
    [
        ("app", "word_count", "app/word_count", True),
        ("app", "rm_rf", "app/rm_rf", False),
        ("other", "word_count", "other/word_count", False),
        ("app", "word_count", "other/word_count", False),
    ],
    ids=["ours", "unknown-tool", "other-server", "mismatched-resource"],
)
def test_only_asks_exactly_for_the_sessions_tools_are_self_approved(server, tool, resource, owned):
    binding = ToolBinding(ToolServer(), collect_tools([word_count]), None)

    assert binding.owns(tool_ask(server, tool, resource)) is owned


class Verdict(BaseModel):
    label: str
    score: int = Field(ge=0, le=10)


async def test_structured_result_is_validated_and_reset_per_turn():
    binding = ToolBinding(ToolServer(), {}, Verdict)
    submit = binding.tools["submit_result"]

    invalid = await submit.call({"result": {"label": "spam", "score": 12}})
    await submit.call({"result": {"label": "spam", "score": 9}})

    assert invalid["isError"] is True  # the agent sees why and can correct it
    assert binding.output("end_turn") == Verdict(label="spam", score=9)
    binding.prompt_blocks([])  # the next turn
    assert binding.output("cancelled") is None
    with pytest.raises(KiroError, match="Verdict"):
        binding.output("end_turn")


def test_submit_result_is_reserved_when_a_result_type_is_set():
    def submit_result(x: int) -> int:
        return x

    with pytest.raises(ValueError, match="submit_result"):
        ToolBinding(ToolServer(), collect_tools([submit_result]), Verdict)
