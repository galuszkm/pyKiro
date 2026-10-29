"""Prompt content: text, images and embedded text documents."""

from __future__ import annotations

import base64
import mimetypes
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias


@dataclass(frozen=True)
class Image:
    """An image in the prompt: its bytes and MIME type."""

    data: bytes
    mime_type: str = "image/png"

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> Image:
        """Read an image file; the MIME type is guessed from its name (``image/png`` when it can't be)."""
        file = Path(path)
        return cls(file.read_bytes(), mimetypes.guess_type(file.name)[0] or "image/png")


@dataclass(frozen=True)
class Resource:
    """A text document embedded in the prompt, e.g. a file the agent should read."""

    uri: str
    text: str
    mime_type: str = "text/plain"

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> Resource:
        """Embed a UTF-8 text file under its ``file://`` URI.

        The MIME type is guessed from its name (``text/plain`` when it can't be).
        """
        file = Path(path).resolve()
        return cls(
            file.as_uri(),
            file.read_text(encoding="utf-8"),
            mimetypes.guess_type(file.name)[0] or "text/plain",
        )


PromptContent: TypeAlias = str | Image | Resource | Sequence[str | Image | Resource]


def to_blocks(content: PromptContent) -> list[dict[str, Any]]:
    """ACP content blocks for a prompt.

    Raises:
        TypeError: ``content`` is not a string, Image, Resource or a sequence of them (a mapping or a set is not).
        ValueError: The sequence is empty.
    """
    if isinstance(content, str | Image | Resource):
        items: list[Any] = [content]
    elif isinstance(content, Sequence):
        items = list(content)
    else:
        raise TypeError(f"a prompt is a string, Image, Resource or a sequence of them, got {type(content).__name__}")
    if not items:
        raise ValueError("empty prompt")
    blocks: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Image):
            blocks.append({"type": "image", "mimeType": item.mime_type, "data": base64.b64encode(item.data).decode()})
        elif isinstance(item, Resource):
            resource = {"uri": item.uri, "text": item.text, "mimeType": item.mime_type}
            blocks.append({"type": "resource", "resource": resource})
        elif isinstance(item, str):
            blocks.append({"type": "text", "text": item})
        else:
            raise TypeError(f"unsupported prompt content: {item!r}")
    return blocks
