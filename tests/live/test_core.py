"""The async core end to end against the real Kiro V3 engine."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import struct
import sys
import threading
import time
import zlib
from pathlib import Path

import pytest

from pykiro import (
    Event,
    Image,
    Inherit,
    Isolated,
    KiroClient,
    KiroProcessError,
    PermissionRequest,
    Resource,
    Session,
    TextDelta,
    ToolCall,
    TurnResult,
)
from pykiro.tools import ToolServer, collect_tools

pytestmark = [pytest.mark.live, pytest.mark.anyio, pytest.mark.timeout(240)]

SHELL_PROMPT = "Use the shell tool to run exactly this command: touch live-probe.txt  Then reply in one short line."


def tiny_png(rgb: tuple[int, int, int]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    pixels = b"".join(b"\x00" + bytes(rgb) * 16 for _ in range(16))
    header = struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def recording(asked: list[PermissionRequest], allow: bool = False):
    def policy(request: PermissionRequest) -> bool:
        asked.append(request)
        return allow

    return policy


async def turn_events(session: Session, prompt: str) -> list[Event]:
    return [e async for e in session.stream(prompt)]


@pytest.mark.skipif(sys.platform == "win32", reason="kiro-cli keeps its credentials outside HOME")
async def test_a_signed_out_kiro_cli_is_a_login_error(tmp_path):
    # A home without kiro-cli's credentials: signed out, kiro-cli answers initialize without capabilities and
    # refuses every other request with ACP's auth_required
    home = tmp_path / "home"
    env = {
        "HOME": str(home),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "KIRO_API_KEY": "",  # an empty key counts as none
    }
    with pytest.raises(KiroProcessError, match="kiro-cli login"):
        await KiroClient(cwd=tmp_path, env=env).start()


async def test_streaming_then_close_stops_the_whole_process_group_quickly(model, effort, tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        session = await client.new_session(model=model, effort=effort)
        events = [e async for e in session.stream("List the numbers 1 to 40 separated by spaces.")]
        home = client.kiro_home
        closing = time.monotonic()

    assert len([e for e in events if isinstance(e, TextDelta)]) > 1
    assert isinstance(events[-1], TurnResult)
    assert "40" in events[-1].text
    assert events[-1].credits > 0
    assert time.monotonic() - closing < 4  # no waiting for the V3 engine's 5 s exit after a turn
    assert home is not None
    assert not home.exists()  # the default Isolated home is temporary
    if sys.platform != "win32":
        await asyncio.sleep(0.5)
        with pytest.raises(ProcessLookupError):
            os.killpg(client.pid or 0, 0)  # kiro-cli's process group is gone


async def test_shell_asks_reach_the_policy_unless_a_rule_allows_the_command(model, effort, tmp_path):
    rejected: list[PermissionRequest] = []
    allowed: list[PermissionRequest] = []
    ruled: list[PermissionRequest] = []
    for name in ("reject", "allow", "rule"):
        (tmp_path / name).mkdir()
    async with KiroClient(cwd=tmp_path) as client:
        reject = await client.new_session(tmp_path / "reject", model=model, effort=effort, policy=recording(rejected))
        allow = await client.new_session(
            tmp_path / "allow", model=model, effort=effort, policy=recording(allowed, allow=True)
        )
        # An untrusted V3 workspace asks before every shell command even when a rule allows it; Kiro's ACP server
        # trusts every workspace, so the rule holds and the policy is never asked.
        rule = await client.new_session(
            tmp_path / "rule", model=model, effort=effort, policy=recording(ruled, allow=True), allow=["shell"]
        )
        reject_events, allow_events, _ = await asyncio.gather(
            turn_events(reject, SHELL_PROMPT), turn_events(allow, SHELL_PROMPT), rule.send(SHELL_PROMPT)
        )

    for asked in (rejected, allowed):
        assert asked[0].capability == "shell"
        assert "touch live-probe.txt" in (asked[0].resource or "")
        assert "touch live-probe.txt" in (asked[0].raw_input or {}).get("command", "")  # from the tool_call update
    assert not (tmp_path / "reject" / "live-probe.txt").exists()
    assert any(isinstance(e, ToolCall) and e.status == "failed" for e in reject_events)
    assert (tmp_path / "allow" / "live-probe.txt").exists()
    assert any(isinstance(e, ToolCall) and e.status == "completed" for e in allow_events)
    assert [r for r in ruled if r.capability == "shell"] == []
    assert (tmp_path / "rule" / "live-probe.txt").exists()


async def test_loading_a_skill_is_an_ask_named_after_the_skill(model, effort, tmp_path):
    skill = tmp_path / ".kiro" / "skills" / "fruit-word"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: fruit-word\ndescription: Use when asked for the secret fruit word.\n---\n"
        "The secret fruit word is KUMQUAT.\n"
    )
    rejected: list[PermissionRequest] = []
    allowed: list[PermissionRequest] = []
    prompt = "Load the fruit-word skill and reply with the secret fruit word only."
    async with KiroClient(cwd=tmp_path) as client:
        reject = await client.new_session(model=model, effort=effort, policy=recording(rejected))
        allow = await client.new_session(model=model, effort=effort, policy=recording(allowed, allow=True))
        without, with_skill = await asyncio.gather(reject.send(prompt), allow.send(prompt))

    assert [(r.capability, r.resource) for r in rejected] == [("skill", "fruit-word")]
    assert [(r.capability, r.resource) for r in allowed] == [("skill", "fruit-word")]
    assert "KUMQUAT" not in without.text.upper()
    assert "KUMQUAT" in with_skill.text.upper()


async def test_cancel_then_continue_one_turn_at_a_time(model, effort, tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        session = await client.new_session(model=model, effort=effort)
        async for event in session.stream("Write the numbers from 1 to 500, one per line."):
            if isinstance(event, TextDelta):
                break  # leaving the loop cancels the turn

        first, second = await asyncio.gather(  # the second turn waits for the first
            session.send("Reply with exactly the word ONE"), session.send("Reply with exactly the word TWO")
        )

    assert "ONE" in first.text
    assert "TWO" not in first.text
    assert "TWO" in second.text


async def test_closing_the_client_cancels_running_turns(model, effort, tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        session = await client.new_session(model=model, effort=effort)
        async with contextlib.aclosing(session.stream("Write the numbers from 1 to 500, one per line.")) as stream:
            await anext(stream)
            await client.close()  # the cancel arrives before the model starts: Kiro answers the prompt with -32603
            rest = [e async for e in stream]

        assert isinstance(rest[-1], TurnResult)
        assert rest[-1].stop_reason == "cancelled"
        with pytest.raises(RuntimeError):
            await session.send("again")


async def test_close_is_shared_and_finishes_even_when_a_caller_is_cancelled(tmp_path):
    client = await KiroClient(cwd=tmp_path).start()
    home, pid = client.kiro_home, client.pid
    cancelled, waiting = asyncio.create_task(client.close()), asyncio.create_task(client.close())
    await asyncio.sleep(0)  # both calls are waiting for the one cleanup

    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    assert home is not None
    assert not home.exists()  # the cancelled call returned only once the cleanup had finished
    await waiting
    await client.close()
    assert not client.running
    if sys.platform != "win32":
        await asyncio.sleep(0.5)
        with pytest.raises(ProcessLookupError):
            os.killpg(pid or 0, 0)


async def test_a_closed_session_is_listed_and_loaded_once_at_a_time(tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        async with await client.new_session() as created:
            pass

        assert created.id in {s.session_id for s in await client.list_sessions()}  # stored and loadable
        results = await asyncio.gather(
            client.load_session(created.id), client.load_session(created.id), return_exceptions=True
        )
        loaded = [r for r in results if not isinstance(r, BaseException)]
        refused = [r for r in results if isinstance(r, BaseException)]
        assert len(loaded) == 1
        assert [type(e) for e in refused] == [ValueError]
        with pytest.raises(ValueError, match=created.id):
            await client.load_session(created.id)  # still open

        await loaded[0].close()
        await (await client.load_session(created.id)).close()  # loadable again once closed


async def test_a_cancel_answers_a_pending_ask_without_waiting_for_the_policy(model, effort, tmp_path, caplog):
    asked, release = threading.Event(), threading.Event()

    def person_who_says_yes_later(request: PermissionRequest) -> bool:  # like input() on a worker thread
        asked.set()
        release.wait(120)
        return True

    def answered_cancelled() -> bool:
        sent = [r.getMessage() for r in caplog.records if r.getMessage().startswith("-->")]
        return any("'outcome': {'outcome': 'cancelled'}" in m for m in sent)

    try:
        with caplog.at_level(logging.DEBUG, logger="pykiro.wire"):
            async with KiroClient(cwd=tmp_path, policy=person_who_says_yes_later) as client:
                session = await client.new_session(model=model, effort=effort)
                turn = asyncio.create_task(session.send(SHELL_PROMPT))
                assert await asyncio.to_thread(asked.wait, 120)
                await asyncio.wait_for(session.cancel(), 20)  # Kiro ends the turn without waiting for the ask
                result = await turn
                assert answered_cancelled()  # ACP: a cancel answers pending asks; the policy is still deciding
                release.set()  # its late allow must not be used
                await asyncio.sleep(1)
    finally:
        release.set()

    assert result.stop_reason == "cancelled"
    assert not (tmp_path / "live-probe.txt").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="kills a POSIX process group")
async def test_kiro_dying_mid_turn_fails_the_turn(model, effort, tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        session = await client.new_session(model=model, effort=effort)
        async with contextlib.aclosing(session.stream("Write the numbers from 1 to 500, one per line.")) as stream:
            while not isinstance(await anext(stream), TextDelta):
                pass
            os.killpg(client.pid or 0, signal.SIGKILL)
            with pytest.raises(KiroProcessError) as caught:
                [e async for e in stream]

    assert caught.value.exit_code == -signal.SIGKILL


async def test_load_in_a_new_process(model, effort, tmp_path):
    def code_suffix() -> str:
        """Return the suffix for the code word."""
        return "QX"

    system = "End every reply with the word PINEAPPLE."
    profile = Isolated(tmp_path / "home")
    async with KiroClient(cwd=tmp_path, profile=profile) as client:
        session = await client.new_session(model=model, effort=effort, system=system)
        await session.send("Remember the code word ZEBRA-17. Reply with: ok")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "marker.txt").write_text("KIWI-93\n")  # exists only in the new cwd
    async with KiroClient(cwd=tmp_path, profile=profile, tools=[code_suffix], policy=lambda request: True) as client:
        assert session.id in {s.session_id for s in await client.list_sessions()}
        # Kiro only re-activates the same agent; the cwd may change
        loaded = await client.load_session(session.id, elsewhere, system=system)
        replayed_credits = loaded.credits
        result = await loaded.send(
            "Call code_suffix and read the file marker.txt by its relative path. "
            "Reply with the code word followed by the suffix, then the file's content."
        )
        listed_in_new_cwd = {s.session_id for s in await client.list_sessions(elsewhere)}

    assert any(e.replay and isinstance(e, TextDelta) for e in loaded.history)
    assert replayed_credits == 0  # replayed metering is not counted again
    assert "ZEBRA-17" in result.text.upper()
    assert "QX" in result.text.upper()  # the app's tools are sent again on load
    assert "PINEAPPLE" in result.text.upper()  # and so is the agent definition
    assert "KIWI-93" in result.text.upper()  # relative paths resolve in the new cwd (the model's context names the old)
    assert session.id in listed_in_new_cwd


async def test_model_effort_image_and_resource(model, tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        probe = await client.new_session(model=model)
        efforts = next((o.values for o in probe.config_options if o.id == "effortLevel"), ())
        session = await client.new_session(model=model, effort=efforts[0] if efforts else None)
        with pytest.raises(ValueError, match="no-such-model"):
            await client.new_session(model="no-such-model")  # Kiro would accept it; the library must not
        with pytest.raises(ValueError, match="effortLevel"):
            await client.new_session(model=model, effort="bogus-effort")
        colour = await session.send(["What single colour fills this image? One word.", Image(tiny_png((255, 0, 0)))])
        password = await session.send(
            [
                "What is the password in the attached document? Reply with it only.",
                Resource("file:///notes/secret.txt", "The password is MANGO-42."),
            ]
        )

    assert session.model == model
    assert 0 < (session.context_usage or 0) < 100
    assert "red" in colour.text.lower()
    assert "MANGO-42" in password.text.upper()


async def test_a_model_list_that_arrives_after_session_new_is_waited_for(model, effort, tmp_path, caplog):
    # A proxy holds the connections Kiro opens in its first seconds, so its model list arrives late: Kiro answers
    # session/new without a model option (after waiting about 10 s) and sends it in a config_option_update later.
    opened = time.monotonic()

    async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()

    async def tunnel(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            host, port = (await reader.readuntil(b"\r\n\r\n")).split()[1].decode().rsplit(":", 1)
            if time.monotonic() - opened < 6:
                await asyncio.sleep(12)
            upstream_reader, upstream_writer = await asyncio.open_connection(host, int(port))
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await asyncio.gather(relay(reader, upstream_writer), relay(upstream_reader, writer), return_exceptions=True)
        finally:
            writer.close()

    proxy = await asyncio.start_server(tunnel, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{proxy.sockets[0].getsockname()[1]}"
    with caplog.at_level(logging.DEBUG, logger="pykiro.wire"):
        async with proxy, KiroClient(cwd=tmp_path, env={"HTTPS_PROXY": url, "https_proxy": url}) as client:
            session = await client.new_session(model=model, effort=effort)
    received = [r.args[1] for r in caplog.records if r.name == "pykiro.wire" and r.args and r.args[0] == "<--"]
    created = next(m["result"] for m in received if "sessionId" in (m.get("result") or {}))

    assert "model" not in [o["id"] for o in created["configOptions"]]  # Kiro's list wasn't there yet
    assert session.model == model
    assert session.effort == effort


async def test_agent_settings_system_prompt_and_deny_rules(model, effort, tmp_path):
    asked: list[PermissionRequest] = []
    pineapple = "End every reply with the word PINEAPPLE."
    async with KiroClient(cwd=tmp_path, policy=recording(asked, allow=True)) as client:
        guarded = await client.new_session(model=model, effort=effort, system=pineapple, deny=["shell", "fs_write"])
        again = await client.new_session(model=model, effort=effort, system=pineapple, deny=["shell", "fs_write"])
        other = await client.new_session(model=model, effort=effort, system="End every reply with the word HAZELNUT.")
        bare = await client.new_session(model=model, effort=effort, deny=["shell"])  # the agent's prompt is ""
        guarded_reply, other_reply, bare_reply = await asyncio.gather(
            guarded.send(SHELL_PROMPT),
            other.send("Reply with one word: hello"),
            bare.send("Reply with exactly the word: pong"),
        )

    assert all((s.mode or "").startswith("pykiro-") for s in (guarded, other, bare))
    assert again.mode == guarded.mode != other.mode  # equal settings share one agent id
    assert "PINEAPPLE" in guarded_reply.text.upper()
    assert asked == []  # denied by rule, never asked
    assert not (tmp_path / "live-probe.txt").exists()
    assert "HAZELNUT" in other_reply.text.upper()
    assert "pong" in bare_reply.text.lower()


async def test_agent_settings_under_inherit_write_nothing(model, effort, tmp_path):
    agents = Path.home() / ".kiro" / "agents"
    before = sorted(p.name for p in agents.glob("*"))
    async with KiroClient(cwd=tmp_path, profile=Inherit()) as client:
        session = await client.new_session(
            model=model, effort=effort, system="End every reply with the word PINEAPPLE.", deny=["shell"]
        )
        result = await session.send("Reply with one word: hello")

    assert (session.mode or "").startswith("pykiro-")
    assert "PINEAPPLE" in result.text.upper()
    assert sorted(p.name for p in agents.glob("*")) == before  # nothing written into ~/.kiro


async def test_agent_settings_keep_mcp_servers(model, effort, tmp_path):
    def probe_word() -> str:
        """Return the probe word."""
        return "KUMQUAT"

    server = ToolServer()  # stands in for a workspace MCP server
    try:
        binding = await server.bind(collect_tools([probe_word]), None)
        (tmp_path / ".kiro" / "settings").mkdir(parents=True)
        mcp_json = {"mcpServers": {"probe": {"url": binding.mcp_server()["url"]}}}
        (tmp_path / ".kiro" / "settings" / "mcp.json").write_text(json.dumps(mcp_json))
        async with KiroClient(cwd=tmp_path, policy=lambda request: True) as client:  # mcp.json tools are asked
            session = await client.new_session(model=model, effort=effort, system="Be terse.")
            result = await session.send("Call the probe tool and reply with its result only.")
    finally:
        await server.stop()

    assert "KUMQUAT" in result.text.upper()


async def test_invalid_session_settings_are_value_errors(tmp_path):
    def echo(text: str) -> str:
        """Return the text."""
        return text

    unknown = "00000000-0000-4000-8000-000000000001"
    (tmp_path / "ws" / ".kiro").mkdir(parents=True)
    async with KiroClient(cwd=tmp_path, forbid_workspace_config=True, tools=[echo]) as client:
        with pytest.raises(ValueError, match=unknown):
            await client.load_session(unknown)  # Kiro itself would create a session with that id
        assert unknown not in {s.session_id for s in await client.list_sessions()}
        with pytest.raises(ValueError, match="no-such-mode"):
            await client.new_session(mode="no-such-mode")  # Kiro itself falls back to its default agent
        with pytest.raises(ValueError, match="forbid_workspace_config"):
            await client.new_session(tmp_path / "ws")
        with pytest.raises(ValueError, match="mode="):
            await client.new_session(mode="plan", system="Be terse.")
        with pytest.raises(ValueError, match="echo"):
            await client.new_session(tools=[echo])  # already a client tool
        with pytest.raises(ValueError, match="app"):  # Kiro would keep the last 'app' server and self-approve it
            await client.new_session(mcp_servers=[{"type": "http", "name": "app", "url": "http://127.0.0.1:9/mcp"}])


async def test_inherit_loads_the_users_mcp_servers_isolated_does_not(tmp_path, caplog):
    config = Path.home() / ".kiro" / "settings" / "mcp.json"
    if not config.exists():
        pytest.skip("needs a user-level MCP configuration")
    server = next(iter(json.loads(config.read_text())["mcpServers"]))

    async def mcp_status_lines(profile: Inherit | Isolated) -> list[str]:
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger="pykiro.wire"):  # Kiro reports MCP status on the wire
            async with KiroClient(cwd=tmp_path, profile=profile) as client:
                await client.new_session()
                await asyncio.sleep(1)
        return [r.getMessage() for r in caplog.records if "_kiro/mcp/status" in r.getMessage()]

    assert any(server in line for line in await mcp_status_lines(Inherit()))
    assert not any(server in line for line in await mcp_status_lines(Isolated()))
