"""The docs chapters: every code fragment a chapter's story quotes is in that chapter's full program.

Ruff formats every Python block in ``docs/*.md`` that parses, so a fragment cut out of the middle of a statement can
turn into different code (``deny=["shell"],`` became the tuple assignment ``deny = (["shell"],)``). Quoting whole
statements keeps a fragment equal to the program; this test catches the ones that don't, or that drifted away from
it. A snippet that isn't part of the program says "variation" in the paragraph before it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).parents[2] / "docs"
PYTHON_BLOCK = re.compile(r"^```python\n(.*?)^```", re.S | re.M)
CHAPTERS = sorted(p for p in DOCS.glob("[0-9][0-9]_*.md") if "## The full code" in p.read_text(encoding="utf-8"))


def code_lines(code: str) -> list[str]:
    """The block's lines without indentation, blank lines or ``...`` elisions."""
    return [s for line in code.splitlines() if (s := line.strip()) and s != "..."]


def appears_in_order(fragment: list[str], program: list[str]) -> bool:
    remaining = iter(program)
    return all(line in remaining for line in fragment)


@pytest.mark.parametrize("chapter", CHAPTERS, ids=lambda path: path.stem)
def test_quoted_fragments_come_from_the_full_program(chapter):
    text = chapter.read_text(encoding="utf-8")
    blocks = list(PYTHON_BLOCK.finditer(text))
    program = next(b for b in blocks if b.start() > text.index("## The full code"))

    for block in blocks:
        paragraph = text[text.rfind("\n\n", 0, block.start() - 1) : block.start()]
        if block is program or "variation" in paragraph.lower():
            continue
        assert appears_in_order(code_lines(block[1]), code_lines(program[1])), block[1]


def test_a_fragment_that_is_not_in_the_program_is_caught():
    program = code_lines('ask_sync(\n    PROMPT,\n    deny=["shell"],\n)\n')

    assert appears_in_order(code_lines('ask_sync(\n    deny=["shell"],\n    ...\n'), program)
    assert not appears_in_order(code_lines('deny = (["shell"],)\n'), program)
