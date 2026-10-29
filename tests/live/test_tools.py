"""App tools against the real Kiro V3 engine, served by the library's own MCP endpoint."""

from __future__ import annotations

import asyncio
import threading

import pytest
from pydantic import BaseModel, Field

from pykiro import Inherit, KiroClient, PermissionRequest, ToolCall, TurnResult, ask

pytestmark = [pytest.mark.live, pytest.mark.anyio, pytest.mark.timeout(240)]

CALLS: dict[str, tuple[bool, object]] = {}
TOOL_PROMPT = (
    "1) Call word_count on 'a b a b c' with unique true. 2) Call place_order for sku 'X-9', quantity 2. "
    "Reply with both results on one line."
)


class Order(BaseModel):
    sku: str
    qty: int = Field(gt=0)


def word_count(text: str, unique: bool = False) -> int:
    """Count words in a text, or only the distinct ones when ``unique`` is true."""
    CALLS["word_count"] = (threading.current_thread() is threading.main_thread(), text)
    return len(set(text.split())) if unique else len(text.split())


async def place_order(order: Order) -> dict:
    """Place an order in the application."""
    CALLS["place_order"] = (threading.current_thread() is threading.main_thread(), order)
    return {"order_id": "A-1", "sku": order.sku, "qty": order.qty}


def lookup(code: str = Field(pattern=r"^[A-Z]{3}$", description="Exactly three upper-case letters.")) -> str:
    """Look up a product by its three-letter code."""
    return {"ABC": "Anvil"}.get(code, "unknown")


class Verdict(BaseModel):
    sentiment: str = Field(pattern="^(positive|negative|neutral)$")
    stars: int = Field(ge=1, le=5)


def recording(asked: list[PermissionRequest], allow: bool = False):
    def policy(request: PermissionRequest) -> bool:
        asked.append(request)
        return allow

    return policy


@pytest.mark.parametrize("profile", [None, Inherit()], ids=["isolated", "inherit"])
async def test_plain_and_async_tools_run_without_asking_the_policy(model, effort, tmp_path, profile):
    asked: list[PermissionRequest] = []
    CALLS.clear()
    async with KiroClient(
        cwd=tmp_path, profile=profile, policy=recording(asked), tools=[word_count, place_order]
    ) as client:
        result = await (await client.new_session(model=model, effort=effort)).send(TOOL_PROMPT)

    assert asked == []
    assert CALLS["word_count"][0] is False  # plain function: a worker thread
    assert CALLS["place_order"] == (True, Order(sku="X-9", qty=2))  # async def on the loop; nested model validated
    assert "3" in result.text
    assert "A-1" in result.text


async def test_bad_arguments_come_back_as_tool_errors(model, effort, tmp_path):
    async with KiroClient(cwd=tmp_path, tools=[lookup]) as client:
        session = await client.new_session(model=model, effort=effort)
        events = [
            e
            async for e in session.stream(
                "Call lookup with the code 'abc' exactly as written first. If it fails, fix the call and tell me the "
                "product name."
            )
        ]

    assert any(isinstance(e, ToolCall) and e.status == "failed" for e in events)
    assert isinstance(events[-1], TurnResult)
    assert "anvil" in events[-1].text.lower()


async def test_structured_results_per_turn(model, effort, tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        session = await client.new_session(model=model, effort=effort, result_type=Verdict)
        bad = await session.send("Review: 'Broke after two days, total waste of money.' Classify it.")
        good = await session.send("Review: 'Absolutely love it, works perfectly!' Classify it.")

    assert bad.output.sentiment == "negative"
    assert good.output.sentiment == "positive"


async def test_tools_only_agent_has_no_shell_and_no_asks(model, effort, tmp_path):
    asked: list[PermissionRequest] = []
    async with KiroClient(cwd=tmp_path, policy=recording(asked, allow=True), tools=[word_count]) as client:
        session = await client.new_session(
            model=model,
            effort=effort,
            system="You are a pipeline step. Use only the provided tools.",
            kiro_tools=["@app"],
            deny=["shell", "fs_write"],
        )
        events = [
            e
            async for e in session.stream(
                "Run the shell command `touch pipeline.txt`, then count the words in 'one two'."
            )
        ]

    assert not any(isinstance(e, ToolCall) and e.kind == "execute" for e in events)
    assert asked == []
    assert not (tmp_path / "pipeline.txt").exists()


async def test_concurrent_sessions_reach_only_their_own_tools(model, effort, tmp_path):
    def secret(value: str):
        def get_secret() -> str:
            """Return this session's secret word."""
            return value

        return get_secret

    async with KiroClient(cwd=tmp_path) as client:
        a = await client.new_session(model=model, effort=effort, tools=[secret("HAZELNUT")])
        b = await client.new_session(model=model, effort=effort, tools=[secret("PERSIMMON")])
        prompt = "Call get_secret and reply with only the word it returns."
        ra, rb = await asyncio.gather(a.send(prompt), b.send(prompt))

    assert "HAZELNUT" in ra.text.upper()
    assert "PERSIMMON" in rb.text.upper()


async def test_pipeline_step_with_ask(model, effort):
    class Extracted(BaseModel):
        name: str
        year: int

    result = await ask(
        "Extract the person and the year: 'In 1969 Margaret Hamilton led the Apollo software team.'",
        model=model,
        effort=effort,
        result_type=Extracted,
        system="Extract structured data.",
        kiro_tools=["@app"],
    )

    assert result.output == Extracted(name="Margaret Hamilton", year=1969)
