"""Live tests: the real kiro-cli with the developer's own login. They spend a few credits.

KIRO_LIVE_MODEL (default claude-haiku-5.5, a 0.06x-credit model) keeps the cost low, and KIRO_LIVE_EFFORT
(default low) keeps the turns fast.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile

import pytest

from pykiro import KiroClient, KiroError


async def _start_once() -> None:
    with tempfile.TemporaryDirectory() as cwd, contextlib.suppress(KiroError):  # the tests report a login problem
        async with KiroClient(cwd=cwd):
            pass


def pytest_configure(config: pytest.Config) -> None:
    # kiro-cli unpacks its engine the first time it starts. Workers that start at once on a fresh machine can fail
    # with "failed to spawn the v3 engine" (kiro-cli 3.6.2), so the controller starts it once before they do.
    if os.environ.get("KIRO_LIVE_TESTS") == "1" and not hasattr(config, "workerinput"):
        asyncio.run(_start_once())


@pytest.fixture
def model() -> str:
    return os.environ.get("KIRO_LIVE_MODEL", "claude-haiku-5.5")


@pytest.fixture
def effort() -> str:
    return os.environ.get("KIRO_LIVE_EFFORT", "low")
