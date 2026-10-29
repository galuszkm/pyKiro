"""ToolServer conformance: the official MCP client (2026-07-28), plus the HTTP rules a raw client can hit.

The per-session path is the only credential, so a wrong path is the only way to be refused.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel

from pykiro.tools import ToolServer, collect_tools

pytestmark = pytest.mark.anyio


class Order(BaseModel):
    sku: str
    qty: int


def word_count(text: str) -> int:
    """Count words."""
    return len(text.split())


async def place_order(order: Order) -> dict:
    """Place an order."""
    return {"order_id": "A-1", "qty": order.qty}


@pytest.fixture
async def server() -> AsyncIterator[ToolServer]:
    srv = ToolServer()
    yield srv
    await srv.stop()


@asynccontextmanager
async def mcp_client(url: str, mode: str) -> AsyncGenerator[Client]:
    async with httpx2.AsyncClient() as http, Client(streamable_http_client(url, http_client=http), mode=mode) as client:
        yield client


@pytest.mark.parametrize("mode", ["auto", "2026-07-28"])
async def test_official_mcp_client_lists_and_calls_tools(server, mode):
    binding = await server.bind(collect_tools([word_count, place_order]), None)
    async with mcp_client(binding.mcp_server()["url"], mode) as client:
        listed = await client.list_tools()
        counted = await client.call_tool("word_count", {"text": "a b a"})
        ordered = await client.call_tool("place_order", {"order": {"sku": "X", "qty": 3}})
        invalid = await client.call_tool("place_order", {"order": {"sku": "X", "qty": "lots"}})

    assert [t.name for t in listed.tools] == ["place_order", "word_count"]
    assert counted.content[0].model_dump()["text"] == "3"
    assert ordered.structured_content == {"order_id": "A-1", "qty": 3}
    assert invalid.is_error


async def test_each_binding_serves_only_its_own_tools(server):
    a = await server.bind(collect_tools([word_count]), None)
    b = await server.bind(collect_tools([place_order]), None)
    async with mcp_client(a.mcp_server()["url"], "auto") as ca, mcp_client(b.mcp_server()["url"], "auto") as cb:
        assert [t.name for t in (await ca.list_tools()).tools] == ["word_count"]
        assert [t.name for t in (await cb.list_tools()).tools] == ["place_order"]


async def test_concurrent_binds_share_one_listener(server):
    bindings = await asyncio.gather(*(server.bind(collect_tools([word_count]), None) for _ in range(3)))

    assert len({urlsplit(b.mcp_server()["url"]).port for b in bindings}) == 1


def rpc(method: str, version: str = "2026-07-28", **params: object) -> dict:
    meta = {"io.modelcontextprotocol/protocolVersion": version, "io.modelcontextprotocol/clientCapabilities": {}}
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": meta}}


def notification(method: str) -> dict:
    message = rpc(method)
    del message["id"]
    return message


def mcp_headers(body: object) -> dict[str, str]:
    """The request headers an MCP 2026-07-28 client mirrors from the body."""
    if not isinstance(body, dict) or not isinstance(body.get("params"), dict):
        return {}
    params = body["params"]
    headers = {"Mcp-Method": str(body.get("method"))}
    if version := (params.get("_meta") or {}).get("io.modelcontextprotocol/protocolVersion"):
        headers["MCP-Protocol-Version"] = version
    if isinstance(params.get("name"), str):
        headers["Mcp-Name"] = params["name"]
    return headers


async def post(url: str, body: object, headers: dict[str, str] | None = None) -> httpx2.Response:
    content = body if isinstance(body, str) else json.dumps(body)
    async with httpx2.AsyncClient() as http:
        return await http.post(url, content=content, headers=mcp_headers(body) if headers is None else headers)


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({}, 200),
        ({"url_suffix": "x"}, 404),
        ({"body": rpc("resources/list")}, 404),
        ({"body": rpc("tools/call", name="nope")}, 400),
        ({"body": rpc("tools/list", version="2099-01-01")}, 400),
        ({"body": [rpc("tools/list")]}, 400),
        ({"body": notification("notifications/cancelled")}, 202),
        ({"body": notification("notifications/cancelled"), "headers": {}}, 400),
        ({"body": {**rpc("tools/list"), "jsonrpc": "1.0"}}, 400),
        ({"body": {**rpc("tools/list"), "id": None}}, 400),
        ({"body": {**rpc("tools/list"), "params": []}}, 400),
        ({"body": '{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"n": NaN}}'}, 400),
        ({"headers": {}}, 400),
        ({"headers": {**mcp_headers(rpc("tools/list")), "MCP-Protocol-Version": "2025-11-25"}}, 400),
        ({"headers": {**mcp_headers(rpc("tools/list")), "Mcp-Method": "tools/call"}}, 400),
        ({"headers": {**mcp_headers(rpc("tools/list")), "Origin": "http://evil.example"}}, 403),
    ],
    ids=[
        "ok",
        "unknown-path",
        "unknown-method",
        "unknown-tool",
        "unsupported-version",
        "batch",
        "notification",
        "notification-no-headers",
        "json-rpc-1",
        "null-id",
        "params-not-an-object",
        "nan",
        "no-headers",
        "version-header-mismatch",
        "method-header-mismatch",
        "foreign-origin",
    ],
)
async def test_http_rules(server, change, status):
    url = (await server.bind(collect_tools([word_count]), None)).mcp_server()["url"] + change.get("url_suffix", "")

    response = await post(url, change.get("body", rpc("tools/list")), change.get("headers"))

    assert response.status_code == status


async def test_a_loopback_origin_is_allowed(server):
    url = (await server.bind(collect_tools([word_count]), None)).mcp_server()["url"]
    origin = f"http://127.0.0.1:{urlsplit(url).port}"

    response = await post(url, rpc("tools/list"), {**mcp_headers(rpc("tools/list")), "Origin": origin})

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("body", "headers", "runs"),
    [
        (rpc("tools/call", name="ping", arguments={}), None, True),
        (
            rpc("tools/call", name="ping"),
            {**mcp_headers(rpc("tools/call", name="ping")), "Mcp-Name": "=?base64?cGluZw==?="},
            True,
        ),
        (rpc("tools/call", name="ping", arguments=[]), None, False),
        ({**rpc("tools/call", name="ping"), "jsonrpc": "1.0"}, None, False),
        (rpc("tools/call", name="ping"), {**mcp_headers(rpc("tools/call", name="ping")), "Mcp-Name": "other"}, False),
        (rpc("tools/call", name="ping"), {**mcp_headers(rpc("tools/call", name="ping")), "Mcp-Name": ""}, False),
    ],
    ids=["ok", "base64-name-header", "arguments-not-an-object", "json-rpc-1", "name-header-mismatch", "no-name-header"],
)
async def test_a_tool_runs_only_for_a_well_formed_call(server, body, headers, runs):
    calls = []

    def ping() -> str:
        """Ping."""
        calls.append(1)
        return "pong"

    url = (await server.bind(collect_tools([ping]), None)).mcp_server()["url"]

    response = await post(url, body, headers)

    assert (response.status_code == 200) is runs
    assert bool(calls) is runs


async def refused(port: int) -> bool:
    try:
        _, writer = await asyncio.open_connection("127.0.0.1", port)
    except OSError:
        return True
    writer.close()
    await writer.wait_closed()
    return False


async def test_an_unbound_path_answers_404_and_others_keep_working(server):
    a = await server.bind(collect_tools([word_count]), None)
    b = await server.bind(collect_tools([word_count]), None)

    server.unbind(a)

    assert (await post(a.mcp_server()["url"], rpc("tools/list"))).status_code == 404
    assert (await post(b.mcp_server()["url"], rpc("tools/list"))).status_code == 200


async def test_a_stopped_server_closes_its_listener_and_refuses_new_bindings(server):
    port = urlsplit((await server.bind(collect_tools([word_count]), None)).mcp_server()["url"]).port or 0

    await server.stop()
    await server.stop()

    assert await refused(port)
    with pytest.raises(RuntimeError):
        await server.bind(collect_tools([word_count]), None)


async def test_a_stop_during_a_bind_closes_the_listener_that_bind_started(server, monkeypatch):
    listening = asyncio.Event()
    release_bind = asyncio.Event()
    stop_started = asyncio.Event()
    start_server = asyncio.start_server

    async def paused_start_server(*args, **kwargs):
        listener = await start_server(*args, **kwargs)
        listening.set()
        await release_bind.wait()
        return listener

    async def stop_server() -> None:
        stop_started.set()
        await server.stop()

    monkeypatch.setattr(asyncio, "start_server", paused_start_server)
    bind = asyncio.create_task(server.bind(collect_tools([word_count]), None))
    await listening.wait()
    stop = asyncio.create_task(stop_server())
    await stop_started.wait()
    release_bind.set()
    binding, _ = await asyncio.gather(bind, stop)

    assert await refused(urlsplit(binding.mcp_server()["url"]).port or 0)
    with pytest.raises(RuntimeError):
        await server.bind(collect_tools([word_count]), None)


async def test_only_post_is_allowed(server):
    url = (await server.bind(collect_tools([word_count]), None)).mcp_server()["url"]
    async with httpx2.AsyncClient() as http:
        assert (await http.get(url)).status_code == 405
        assert (await http.delete(url)).status_code == 405


async def status_line(url: str, head: str) -> bytes:
    """Send a raw request head (no body) and return the response's status line."""
    reader, writer = await asyncio.open_connection("127.0.0.1", urlsplit(url).port)
    try:
        writer.write(head.encode())
        await writer.drain()
        return await reader.readline()
    finally:
        writer.close()
        await writer.wait_closed()


async def test_an_unknown_path_is_refused_without_waiting_for_the_body(server):
    url = (await server.bind(collect_tools([word_count]), None)).mcp_server()["url"]

    status = await status_line(url, "POST /mcp/wrong HTTP/1.1\r\nContent-Length: 5\r\n\r\n")

    assert status.startswith(b"HTTP/1.1 404")


async def test_a_body_over_the_limit_is_refused(server):
    url = (await server.bind(collect_tools([word_count]), None)).mcp_server()["url"]

    status = await status_line(url, f"POST {urlsplit(url).path} HTTP/1.1\r\nContent-Length: 99999999999\r\n\r\n")

    assert status.startswith(b"HTTP/1.1 413")
