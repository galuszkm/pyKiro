"""The kiro-cli process: finding the executable, its environment, stderr capture and shutdown."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import os
import shutil
import signal
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .errors import KiroProcessError

logger = logging.getLogger(__name__)

# The launch Kiro documents for ACP clients; "cli" (the default on 2.34.0) uses kiro-cli's own login, while
# "client" would send the engine's token requests to us.
ACP_ARGS = ("acp", "--agent-engine=v3", "--auth-method=cli")
STREAM_LIMIT = 16 * 1024 * 1024
# How long close() lets kiro-cli exit on its own after stdin closes. After a turn the V3 engine ignores stdin EOF
# and kiro-cli would only kill it after 5 s (observed on kiro-cli 2.34.0), so the process is terminated instead.
EXIT_GRACE = 1.0


def find_executable(explicit: str | os.PathLike[str] | None = None) -> str:
    """The absolute path of the explicit kiro-cli, else of kiro-cli on PATH.

    Raises:
        KiroProcessError: Nothing was found.
    """
    found = shutil.which(explicit if explicit is not None else "kiro-cli")
    if found is None:
        where = f"at {explicit}" if explicit is not None else "on PATH"
        raise KiroProcessError(
            f"kiro-cli not found {where}. Install it (https://kiro.dev/docs/getting-started/installation)"
            " or pass executable=<full path>."
        )
    return str(Path(found).absolute())  # a relative path would resolve against the child's cwd


def _add_loopback(value: str | None) -> str:
    hosts = [h.strip() for h in (value or "").split(",") if h.strip()]
    hosts += [h for h in ("127.0.0.1", "localhost") if h not in hosts]
    return ",".join(hosts)


def build_env(kiro_home: Path | None, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The full parent environment plus our overrides.

    The loopback hosts are added to NO_PROXY so Kiro reaches the app's tool server without a proxy.
    """
    env = {**os.environ, **(extra or {})}
    if kiro_home is not None:
        env["KIRO_HOME"] = str(kiro_home)
    env["NO_PROXY"] = _add_loopback(env.get("NO_PROXY"))
    if sys.platform != "win32":  # Windows environment names are case-insensitive
        env["no_proxy"] = _add_loopback(env.get("no_proxy"))
    return env


class StderrBuffer:
    """The last lines of kiro-cli's stderr. Read continuously so the pipe never fills up."""

    def __init__(self, max_lines: int = 400) -> None:
        self.lines: collections.deque[str] = collections.deque(maxlen=max_lines)

    def tail(self, n: int = 40) -> str:
        """The last ``n`` lines; nothing for ``n <= 0``."""
        return "".join(list(self.lines)[-n:]) if n > 0 else ""

    def contains(self, text: str) -> bool:
        """Whether any buffered line contains ``text`` (case-insensitive)."""
        return any(text.lower() in line.lower() for line in self.lines)

    async def pump(self, stream: asyncio.StreamReader) -> None:
        """Read ``stream`` until EOF."""
        while line := await stream.readline():
            text = line.decode("utf-8", errors="replace")
            self.lines.append(text)
            logger.debug("kiro-cli stderr: %s", text.rstrip())


def startup_error(exit_code: int | None, stderr: StderrBuffer) -> KiroProcessError:
    """Explain a process that exited before answering initialize."""
    if stderr.contains("you are not logged in"):
        return KiroProcessError(
            "Not logged in to Kiro: run `kiro-cli login` (or `kiro-cli login --use-device-flow`), or set KIRO_API_KEY."
        )
    return KiroProcessError(
        "kiro-cli exited before answering initialize", exit_code=exit_code, stderr_tail=stderr.tail()
    )


async def spawn(argv: Sequence[str], env: Mapping[str, str], cwd: str) -> asyncio.subprocess.Process:
    """Start the agent in its own process group (POSIX session, Windows process group).

    On POSIX, signalling the group stops kiro-cli and kiro-cli-chat; the Node.js engine runs in its own group and
    exits on its own once they are gone (observed on kiro-cli 2.34.0).
    """
    group: dict[str, Any]
    if sys.platform == "win32":
        import subprocess  # nosec B404

        group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        group = {"start_new_session": True}
    pipe = asyncio.subprocess.PIPE
    return await asyncio.create_subprocess_exec(
        *argv, stdin=pipe, stdout=pipe, stderr=pipe, env=dict(env), cwd=cwd, limit=STREAM_LIMIT, **group
    )


async def _kill_tree(proc: asyncio.subprocess.Process, sig: int) -> None:
    with contextlib.suppress(OSError):
        if sys.platform == "win32":
            import subprocess  # nosec B404

            # Windows has no process-group signals; taskkill /T stops the tree (fixed arguments, no shell)
            await asyncio.to_thread(
                subprocess.run, ["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False
            )
        else:
            os.killpg(proc.pid, sig)


async def shutdown(proc: asyncio.subprocess.Process) -> int:
    """Close stdin; if kiro-cli hasn't exited within EXIT_GRACE, stop it.

    On POSIX its process group gets SIGTERM, then SIGKILL after another EXIT_GRACE; on Windows both steps are a
    forced ``taskkill /T``.

    Returns:
        The exit code (negative for a signal on POSIX).
    """
    if proc.stdin is not None:
        proc.stdin.close()
    with contextlib.suppress(TimeoutError):
        return await asyncio.wait_for(proc.wait(), EXIT_GRACE)
    logger.debug("pid=<%s> | kiro-cli still running, terminating its process group", proc.pid)
    await _kill_tree(proc, signal.SIGTERM)
    with contextlib.suppress(TimeoutError):
        return await asyncio.wait_for(proc.wait(), EXIT_GRACE)
    logger.warning("pid=<%s> | kiro-cli ignored SIGTERM, killing its process group", proc.pid)
    await _kill_tree(proc, getattr(signal, "SIGKILL", signal.SIGTERM))
    return await proc.wait()
