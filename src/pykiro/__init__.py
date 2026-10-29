"""pyKiro: use your Kiro subscription as an AI agent from Python.

Drives the Kiro CLI V3 engine (``kiro-cli acp --agent-engine=v3``) over the Agent Client Protocol. The API is
async; blocking code runs it with ``asyncio.run(...)``. App tools are plain typed functions passed as
``tools=[...]``.
"""

from .client import KiroClient, __version__, ask, ask_sync
from .content import Image, PromptContent, Resource
from .errors import KiroError, KiroProcessError, KiroProtocolError, ToolError
from .events import Event, Notice, TextDelta, ToolCall, TurnResult
from .permissions import PermissionOption, PermissionPolicy, PermissionRequest
from .profile import Inherit, Isolated, Profile
from .render import AnsiRenderer
from .session import ConfigOption, Session, SessionInfo

__all__ = [
    "AnsiRenderer",
    "ConfigOption",
    "Event",
    "Image",
    "Inherit",
    "Isolated",
    "KiroClient",
    "KiroError",
    "KiroProcessError",
    "KiroProtocolError",
    "Notice",
    "PermissionOption",
    "PermissionPolicy",
    "PermissionRequest",
    "Profile",
    "PromptContent",
    "Resource",
    "Session",
    "SessionInfo",
    "TextDelta",
    "ToolCall",
    "ToolError",
    "TurnResult",
    "__version__",
    "ask",
    "ask_sync",
]
