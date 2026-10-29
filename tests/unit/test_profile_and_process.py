"""Profiles, agent definitions, the child environment, executable discovery and start-up errors."""

from __future__ import annotations

import logging
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import pytest

from pykiro import Image, Isolated, KiroProcessError, Resource
from pykiro._process import StderrBuffer, build_env, find_executable, startup_error
from pykiro.content import to_blocks
from pykiro.profile import ProfileHome, agent_definition, validate_home


def test_agent_definition_in_kiros_custom_agents_format():
    agent = agent_definition("Be terse.", ("@app",), ["fs_read"], ["shell"])

    assert agent is not None
    assert agent["id"].startswith("pykiro-")
    assert agent["prompt"] == "Be terse."
    assert agent["tools"] == ["@app"]
    assert agent["permissions"]["rules"] == [
        {"capability": "fs_read", "effect": "allow"},
        {"capability": "shell", "effect": "deny"},
    ]


def test_agent_definition_defaults_and_none_without_settings():
    agent = agent_definition(None, None, (), ["shell"])

    assert agent is not None
    assert agent["prompt"] == ""
    assert agent["tools"] == ["*"]
    assert agent_definition(None, None, (), ()) is None


def test_agent_definition_id_follows_the_settings():
    a, again, other = (
        agent_definition("x", None, [], ["shell"]),
        agent_definition("x", ["*"], (), ("shell",)),
        agent_definition("y", ("*",), [], ["shell"]),
    )

    assert a is not None
    assert again is not None
    assert other is not None
    assert a["id"] == again["id"] != other["id"]


@pytest.mark.parametrize(
    ("system", "kiro_tools", "allow", "deny"),
    [
        ("", ("*",), (), "shell"),
        (0, None, (), ()),
        (None, ["@app", 1], (), ()),
        (None, None, [None], ()),
        (None, None, (), [b"shell"]),
    ],
    ids=["string-not-sequence", "system-not-string", "tool-not-string", "allow-not-string", "deny-not-string"],
)
def test_agent_settings_are_strings_and_sequences_of_strings(system, kiro_tools, allow, deny):
    with pytest.raises(TypeError):
        agent_definition(system, kiro_tools, allow, deny)


def test_unknown_names_go_to_kiro_unchanged():
    agent = agent_definition(None, ["@some_future_server/*"], ["capability_from_2027"], ())

    assert agent is not None
    assert agent["tools"] == ["@some_future_server/*"]
    assert agent["permissions"]["rules"] == [{"capability": "capability_from_2027", "effect": "allow"}]


@pytest.mark.parametrize("profile", [Isolated, object(), "isolated", None], ids=["class", "object", "string", "none"])
def test_anything_but_a_profile_instance_is_refused_before_a_home_is_made(profile, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    with pytest.raises(TypeError):
        ProfileHome(profile)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_a_new_isolated_home_is_private_and_an_existing_one_keeps_its_mode(tmp_path):
    old_umask = os.umask(0o022)
    try:
        created = ProfileHome(Isolated(tmp_path / "new"))
        (tmp_path / "shared").mkdir(mode=0o755)
        existing = ProfileHome(Isolated(tmp_path / "shared"))
    finally:
        os.umask(old_umask)

    assert created.path is not None
    assert existing.path is not None
    assert stat.S_IMODE(created.path.stat().st_mode) == 0o700
    assert stat.S_IMODE(existing.path.stat().st_mode) == 0o755


def test_a_temporary_home_that_cant_be_removed_is_logged_not_raised(monkeypatch, caplog):
    home = ProfileHome(Isolated())
    assert home.path is not None

    def fail(path: object) -> None:
        raise PermissionError(f"busy: {path}")

    monkeypatch.setattr(shutil, "rmtree", fail)
    with caplog.at_level(logging.WARNING, logger="pykiro.profile"):
        home.cleanup()
    monkeypatch.undo()
    shutil.rmtree(home.path)

    assert [r.levelname for r in caplog.records] == ["WARNING"]


@pytest.mark.parametrize("bad", [Path(Path.home().anchor), Path.home(), Path.home().parent, Path.home() / ".kiro"])
def test_isolated_home_rejects_paths_kiro_would_ignore(bad):
    with pytest.raises(ValueError, match="KIRO_HOME"):
        validate_home(bad)


def test_isolated_home_accepts_other_directories(tmp_path):
    assert validate_home(tmp_path / "home") == (tmp_path / "home").resolve()


def test_child_env_keeps_parent_env_and_bypasses_proxy_for_loopback(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "corp.example,localhost")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")

    env = build_env(Path("/tmp/kh"), {"EXTRA": "1"})

    assert env["HTTPS_PROXY"] == "http://proxy:3128"
    assert env["EXTRA"] == "1"
    assert env["KIRO_HOME"] == "/tmp/kh"
    assert env["NO_PROXY"] == "corp.example,localhost,127.0.0.1"


def test_find_executable_takes_the_explicit_path_then_path(tmp_path, monkeypatch):
    exe = tmp_path / ("kiro-cli.exe" if sys.platform == "win32" else "kiro-cli")
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    assert Path(find_executable(exe)) == exe
    monkeypatch.setenv("PATH", str(tmp_path))
    assert Path(find_executable()).name == exe.name
    monkeypatch.setenv("PATH", "")
    with pytest.raises(KiroProcessError):
        find_executable()
    with pytest.raises(KiroProcessError):
        find_executable(tmp_path / "missing")


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX executable script")
def test_a_relative_executable_is_made_absolute_for_a_child_in_another_cwd(tmp_path, monkeypatch):
    (tmp_path / "bin").mkdir()
    exe = tmp_path / "bin" / "kiro-cli"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.chdir(tmp_path)

    assert find_executable(Path("bin") / "kiro-cli") == str(exe)


def test_a_stderr_tail_of_zero_lines_is_empty():
    stderr = StderrBuffer()
    stderr.lines.extend(["a\n", "b\n"])

    assert stderr.tail(1) == "b\n"
    assert stderr.tail(0) == ""
    assert stderr.tail(-1) == ""


def test_startup_failure_explains_a_missing_login_and_keeps_stderr():
    logged_out, broken = StderrBuffer(), StderrBuffer()
    logged_out.lines.append("error: You are not logged in, please log in with kiro-cli login\n")
    broken.lines.append("panic\n")

    assert "kiro-cli login" in str(startup_error(1, logged_out))
    error = startup_error(101, broken)
    assert error.exit_code == 101
    assert "panic" in error.stderr_tail


def test_prompt_content_blocks():
    blocks = to_blocks(["look:", Image(b"\x89PNG"), Resource("file:///a.txt", "A")])

    assert [b["type"] for b in blocks] == ["text", "image", "resource"]
    assert blocks[1]["data"] == "iVBORw=="
    assert blocks[2]["resource"]["text"] == "A"
    assert [b["text"] for b in to_blocks(("one", "two"))] == ["one", "two"]


@pytest.mark.parametrize(
    "content", [{"question": "answer"}, {"a", "b"}, (s for s in ["a"]), 42], ids=["mapping", "set", "generator", "int"]
)
def test_a_prompt_that_is_not_a_sequence_is_refused(content):
    with pytest.raises(TypeError):
        to_blocks(content)


class _CustomPath:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __fspath__(self) -> str:
        return str(self.path)

    def __str__(self) -> str:
        return "not-a-path.png"


@pytest.mark.parametrize(("name", "mime"), [("photo.jpg", "image/jpeg"), ("photo.unknown-ext", "image/png")])
def test_an_image_mime_type_is_guessed_from_the_files_name(tmp_path, name, mime):
    (tmp_path / name).write_bytes(b"\xff\xd8")

    assert Image.from_file(_CustomPath(tmp_path / name)).mime_type == mime
