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
    Image,
    Inherit,
    Isolated,
    KiroClient,
    KiroProcessError,
    PermissionRequest,
    Resource,
    TextDelta,
    ToolCall,
    TurnResult,
    ask,
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


async def test_kiro_advertises_the_required_acp_v1_contract(tmp_path):
    async with KiroClient(cwd=tmp_path):
        pass


async def test_ask(model, effort):
    result = await ask("Reply with exactly the word: pong", model=model, effort=effort)

    assert "pong" in result.text.lower()
    assert result.credits > 0


async def test_streaming_then_close_stops_the_whole_process_group_quickly(model, effort, tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        session = await client.new_session(model=model, effort=effort)
        events = [e async for e in session.stream("List the numbers 1 to 40 separated by spaces.")]
        home = client.kiro_home
        closing = time.monotonic()

    assert len([e for e in events if isinstance(e, TextDelta)]) > 1
    assert isinstance(events[-1], TurnResult)
    assert "40" in events[-1].text
    assert time.monotonic() - closing < 4  # no waiting for the V3 engine's 5 s exit after a turn
    assert home is not None
    assert not home.exists()  # the default Isolated home is temporary
    if sys.platform != "win32":
        await asyncio.sleep(0.5)
        with pytest.raises(ProcessLookupError):
            os.killpg(client.pid or 0, 0)  # kiro-cli's process group is gone


@pytest.mark.parametrize("allowed", [False, True], ids=["reject", "allow"])
async def test_shell_ask(model, effort, tmp_path, allowed):
    asked: list[PermissionRequest] = []
    async with KiroClient(cwd=tmp_path, policy=recording(asked, allowed)) as client:
        events = [e async for e in (await client.new_session(model=model, effort=effort)).stream(SHELL_PROMPT)]

    assert asked[0].capability == "shell"
    assert "touch live-probe.txt" in (asked[0].resource or "")
    assert "touch live-probe.txt" in (asked[0].raw_input or {}).get("command", "")  # from the tool_call update
    assert (tmp_path / "live-probe.txt").exists() is allowed
    assert any(isinstance(e, ToolCall) and e.status == ("completed" if allowed else "failed") for e in events)


@pytest.mark.parametrize("allowed", [False, True], ids=["reject", "allow"])
async def test_loading_a_skill_is_an_ask_named_after_the_skill(model, effort, tmp_path, allowed):
    skill = tmp_path / ".kiro" / "skills" / "fruit-word"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: fruit-word\ndescription: Use when asked for the secret fruit word.\n---\n"
        "The secret fruit word is KUMQUAT.\n"
    )
    asked: list[PermissionRequest] = []
    async with KiroClient(cwd=tmp_path, policy=recording(asked, allowed)) as client:
        session = await client.new_session(model=model, effort=effort)
        result = await session.send("Load the fruit-word skill and reply with the secret fruit word only.")

    assert [(r.capability, r.resource) for r in asked] == [("skill", "fruit-word")]
    assert ("KUMQUAT" in result.text.upper()) is allowed


async def test_the_workspace_is_trusted_so_an_allow_rule_covers_shell(model, effort, tmp_path):
    # An untrusted V3 workspace asks before every shell command even when a rule allows it; Kiro's ACP server
    # trusts every workspace, so the rule holds and the policy is never asked.
    asked: list[PermissionRequest] = []
    async with KiroClient(cwd=tmp_path, policy=recording(asked, allow=True)) as client:
        session = await client.new_session(model=model, effort=effort, allow=["shell"])
        await session.send(SHELL_PROMPT)

    assert [r for r in asked if r.capability == "shell"] == []
    assert (tmp_path / "live-probe.txt").exists()


async def test_cancel_then_continue(model, effort, tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        session = await client.new_session(model=model, effort=effort)
        async for event in session.stream("Write the numbers from 1 to 500, one per line."):
            if isinstance(event, TextDelta):
                break

        assert "after-cancel" in (await session.send("Reply with exactly: after-cancel")).text


async def test_a_second_turn_on_a_session_waits_for_the_first(model, effort, tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        session = await client.new_session(model=model, effort=effort)
        first, second = await asyncio.gather(
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


async def test_a_session_is_loaded_once_at_a_time(tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        async with await client.new_session() as created:
            pass

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


async def test_two_concurrent_sessions(model, effort, tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        b = await client.new_session(model=model, effort=effort)
        async with await client.new_session(model=model, effort=effort) as a:
            ra, rb = await asyncio.gather(
                a.send("Reply with exactly the word ALPHA"), b.send("Reply with exactly BETA")
            )

    assert "ALPHA" in ra.text
    assert "BETA" not in ra.text
    assert "BETA" in rb.text


async def test_an_additional_directory_is_read_like_the_workspace(model, effort, tmp_path):
    workspace, extra = tmp_path / "workspace", tmp_path / "extra"
    workspace.mkdir()
    extra.mkdir()
    (extra / "marker.txt").write_text("PLUM-58\n")
    prompt = f"Read the file {extra / 'marker.txt'} and reply with its content only."
    outside: list[PermissionRequest] = []
    added: list[PermissionRequest] = []
    async with KiroClient(cwd=workspace) as client:
        plain = await client.new_session(model=model, effort=effort, policy=recording(outside))
        await plain.send(prompt)
        session = await client.new_session(
            model=model, effort=effort, policy=recording(added), additional_directories=[extra]
        )
        result = await session.send(prompt)

    assert [r.capability for r in outside] == ["fs_read"]  # outside the workspace a read asks
    assert added == []  # an additional directory is read without asking, like the cwd
    assert "PLUM-58" in result.text.upper()


async def test_closed_session_is_still_listed(tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        async with await client.new_session() as session:
            pass

        assert session.id in {s.session_id for s in await client.list_sessions()}  # stored and loadable


async def test_loading_an_unknown_session_is_a_value_error(tmp_path):
    unknown = "00000000-0000-4000-8000-000000000001"
    async with KiroClient(cwd=tmp_path) as client:
        with pytest.raises(ValueError, match=unknown):
            await client.load_session(unknown)  # Kiro itself would create a session with that id

        assert unknown not in {s.session_id for s in await client.list_sessions()}


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


async def test_system_prompt_and_deny_rules(model, effort, tmp_path):
    asked: list[PermissionRequest] = []
    async with KiroClient(cwd=tmp_path, policy=recording(asked, allow=True)) as client:
        session = await client.new_session(
            model=model, effort=effort, system="End every reply with the word PINEAPPLE.", deny=["shell", "fs_write"]
        )
        result = await session.send(SHELL_PROMPT)

    assert (session.mode or "").startswith("pykiro-")
    assert "PINEAPPLE" in result.text.upper()
    assert asked == []  # denied by rule, never asked
    assert not (tmp_path / "live-probe.txt").exists()


async def test_two_agents_on_one_client(model, effort, tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        first = await client.new_session(model=model, effort=effort, system="End every reply with the word PERSIMMON.")
        second = await client.new_session(model=model, effort=effort, system="End every reply with the word HAZELNUT.")
        again = await client.new_session(model=model, effort=effort, system="End every reply with the word PERSIMMON.")
        replies = [(await s.send("Reply with one word: hello")).text.upper() for s in (first, second)]

    assert (first.mode or "").startswith("pykiro-")
    assert (second.mode or "").startswith("pykiro-")
    assert again.mode == first.mode != second.mode  # equal settings share one agent id
    assert "PERSIMMON" in replies[0]
    assert "HAZELNUT" in replies[1]


async def test_agent_settings_without_system(model, effort, tmp_path):
    async with KiroClient(cwd=tmp_path) as client:
        session = await client.new_session(model=model, effort=effort, deny=["shell"])  # the agent's prompt is ""
        result = await session.send("Reply with exactly the word: pong")

    assert (session.mode or "").startswith("pykiro-")
    assert "pong" in result.text.lower()


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

    (tmp_path / "ws" / ".kiro").mkdir(parents=True)
    async with KiroClient(cwd=tmp_path, forbid_workspace_config=True, tools=[echo]) as client:
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
