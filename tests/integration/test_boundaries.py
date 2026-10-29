"""Import boundaries: what the core may import, and that only _acp.py touches the ACP SDK."""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "pykiro"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def test_only_acp_module_imports_the_sdk():
    users = sorted(str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if "acp" in imported_modules(p))
    assert users == ["_acp.py"]


def test_core_has_no_mcp_or_http_dependency():
    banned = {"mcp", "mcp_types", "starlette", "uvicorn", "httpx", "aiohttp"}
    for path in SRC.rglob("*.py"):
        assert not (imported_modules(path) & banned), path


def test_import_works_with_optional_packages_blocked():
    code = textwrap.dedent("""
        import sys
        class Block:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in {"mcp", "mcp_types", "uvicorn", "starlette", "httpx"}:
                    raise ImportError(f"blocked: {name}")
        sys.meta_path.insert(0, Block())
        import pykiro, pykiro.tools
        print("ok")
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
