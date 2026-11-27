"""TEMPORARY probe of concurrent first starts of kiro-cli. Not to be merged."""

from __future__ import annotations

import asyncio
import collections

import pytest

from pykiro import KiroClient

pytestmark = [pytest.mark.live, pytest.mark.anyio, pytest.mark.timeout(240)]


async def _start(cwd, home) -> str:
    try:
        async with KiroClient(cwd=cwd, env={"HOME": str(home)}):
            return "ok"
    except Exception as exc:
        text = str(exc)
        return "SPAWN" if "failed to spawn" in text else f"{type(exc).__name__}: {text[:160]}"


async def test_probe(tmp_path):
    cold = collections.Counter()
    warm = collections.Counter()
    for i in range(4):
        home = tmp_path / f"cold{i}"
        home.mkdir()
        cold.update(await asyncio.gather(*(_start(tmp_path, home) for _ in range(8))))
    for i in range(4):
        home = tmp_path / f"warm{i}"
        home.mkdir()
        warm.update([await _start(tmp_path, home)])
        warm.update(await asyncio.gather(*(_start(tmp_path, home) for _ in range(8))))
    pytest.fail(f"PROBE cold={dict(cold)} warm={dict(warm)}", pytrace=False)
