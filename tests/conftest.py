"""Shared test infrastructure: the async backend and live-test skipping."""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("KIRO_LIVE_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set KIRO_LIVE_TESTS=1 (spends credits)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
