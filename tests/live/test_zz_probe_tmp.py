"""TEMPORARY probe of when kiro-cli lists its models. Not to be merged."""

from __future__ import annotations

import asyncio
import time

import pytest

from pykiro import KiroClient

pytestmark = [pytest.mark.live, pytest.mark.anyio, pytest.mark.timeout(240)]


async def _run(tmp_path, hold_until: float | None):
    """hold_until: seconds after the first CONNECT until which every tunnel is held (None: no proxy)."""
    first: list[float] = []
    connects: list[str] = []
    t0 = time.monotonic()

    async def relay(reader, writer):
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()

    async def tunnel(reader, writer):
        try:
            host, port = (await reader.readuntil(b"\r\n\r\n")).split()[1].decode().rsplit(":", 1)
            now = time.monotonic()
            if not first:
                first.append(now)
            connects.append(f"{now - t0:.1f}s {host}")
            wait = first[0] + (hold_until or 0) - now
            if wait > 0:
                await asyncio.sleep(wait)
            ur, uw = await asyncio.open_connection(host, int(port))
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await asyncio.gather(relay(reader, uw), relay(ur, writer), return_exceptions=True)
        finally:
            writer.close()

    env = {}
    proxy = None
    if hold_until is not None:
        proxy = await asyncio.start_server(tunnel, "127.0.0.1", 0)
        url = f"http://127.0.0.1:{proxy.sockets[0].getsockname()[1]}"
        env = {"HTTPS_PROXY": url, "https_proxy": url}
    report = {}
    try:
        async with KiroClient(cwd=tmp_path, env=env) as client:
            report["started"] = round(time.monotonic() - t0, 1)
            session = await client.new_session()
            report["session_new"] = round(time.monotonic() - t0, 1)
            report["model_in_session_new"] = any(o.id == "model" for o in session.config_options)
            try:
                await asyncio.wait_for(session._wait_for_models(), 40)
                report["model_listed_at"] = round(time.monotonic() - t0, 1)
            except Exception as exc:
                report["model_listed_at"] = f"never: {type(exc).__name__}"
    finally:
        if proxy:
            proxy.close()
    report["connects"] = len(connects)
    report["first_connects"] = connects[:6]
    return report


async def test_probe(tmp_path):
    import os
    from pathlib import Path

    def holders() -> list[str]:
        found = []
        for root in (Path.home() / ".kiro", Path.home() / ".local", Path.home() / ".cache", Path.home() / ".config"):
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    path = Path(dirpath) / name
                    try:
                        if path.stat().st_size < 50_000_000 and b"claude-haiku-5.5" in path.read_bytes():
                            found.append(str(path.relative_to(Path.home())))
                    except OSError:
                        pass
        return found

    before = holders()
    first = await _run(tmp_path, None)
    after_first = holders()
    second = await _run(tmp_path, None)
    pytest.fail(
        f"PROBE before={before} first_has_model={first['model_in_session_new']} "
        f"after_first={after_first} second_has_model={second['model_in_session_new']}",
        pytrace=False,
    )
