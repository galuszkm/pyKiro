"""KiroClient start-up failures, with a one-line Python process standing in for a kiro-cli that breaks."""

from __future__ import annotations

import sys

import pytest

from pykiro import KiroClient, KiroProcessError, ask_sync

pytestmark = pytest.mark.anyio


async def test_exit_before_initialize_is_a_process_error_with_stderr(tmp_path):
    command = [sys.executable, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(7)"]
    with pytest.raises(KiroProcessError) as caught:
        await KiroClient(command=command, cwd=tmp_path).start()

    assert caught.value.exit_code == 7
    assert "boom" in caught.value.stderr_tail


async def test_no_answer_to_initialize_is_a_process_error(tmp_path):
    client = KiroClient(command=[sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path, startup_timeout=1)
    with pytest.raises(KiroProcessError, match="initialize"):
        await client.start()

    assert client.exit_code is not None  # the silent process was stopped, not left behind


async def test_a_missing_cwd_is_a_value_error(tmp_path):
    with pytest.raises(ValueError, match="missing"):
        await KiroClient(command=[sys.executable, "-c", "pass"], cwd=tmp_path / "missing").start()


def test_ask_sync_runs_without_an_event_loop(tmp_path) -> None:
    with pytest.raises(KiroProcessError, match="not found"):
        ask_sync("hi", executable=tmp_path / "missing")
