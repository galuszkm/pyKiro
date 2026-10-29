"""Exceptions: Kiro's side of things, and the one an app tool raises to tell the agent why it failed.

Misuse raises ValueError, TypeError or RuntimeError instead.
"""

from __future__ import annotations

from typing import Any


class KiroError(Exception):
    """Base class; also raised for an incompatible ACP contract, an invalid structured result or close()."""


class KiroProcessError(KiroError):
    """kiro-cli is missing, not logged in, or exited or failed unexpectedly.

    ``exit_code`` is the process exit code if it exited (else None); ``stderr_tail`` is the last lines kiro-cli
    wrote to stderr.
    """

    def __init__(self, message: str, *, exit_code: int | None = None, stderr_tail: str = "") -> None:
        if exit_code is not None:
            message += f" (exit code {exit_code})"
        if stderr_tail:
            message += f"\n--- kiro-cli stderr ---\n{stderr_tail}"
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail


class KiroProtocolError(KiroError):
    """Kiro answered a request with a JSON-RPC error.

    ``code``, ``message`` and ``data`` are Kiro's JSON-RPC error (``data`` is None when Kiro sent none).
    """

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"{message} (JSON-RPC {code})")
        self.code = code
        self.message = message
        self.data = data


class ToolError(Exception):
    """Raise it from an app tool to fail the call with a message the agent sees, e.g. ``ToolError("no such sku")``.

    Any other exception a tool raises reaches the agent only as a generic failure; its details (which can carry
    paths, URLs or credentials) are logged locally instead.
    """
