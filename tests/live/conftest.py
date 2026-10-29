"""Live tests: the real kiro-cli with the developer's own login. They spend a few credits.

KIRO_LIVE_MODEL (default claude-haiku-5.5, a 0.06x-credit model) keeps the cost low, and KIRO_LIVE_EFFORT
(default low) keeps the turns fast.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def model() -> str:
    return os.environ.get("KIRO_LIVE_MODEL", "claude-haiku-5.5")


@pytest.fixture
def effort() -> str:
    return os.environ.get("KIRO_LIVE_EFFORT", "low")
