"""Text artifacts must be written as UTF-8 regardless of platform default.

Windows defaults text file writes to the active code page (cp1252 on most
machines). The viewer template and the guide body contain non-ASCII
characters (arrows, em dashes, middle dots), so an encoding-less write either
crashes with UnicodeEncodeError or, where the code page can represent every
character, silently produces files that are no longer valid UTF-8. These
tests force the legacy default and assert the artifacts still land as UTF-8.
"""

import locale

import pytest

from agentcad.commands.instructions import install_instructions, instructions_status
from agentcad.commands.skill import install_skill, skill_status
from agentcad.commands.view import _render_unified


@pytest.fixture
def cp1252_default(monkeypatch):
    """Simulate the legacy Windows default encoding for text file I/O."""
    monkeypatch.setattr(
        locale, "getpreferredencoding", lambda do_setlocale=True: "cp1252"
    )
    if hasattr(locale, "getencoding"):
        monkeypatch.setattr(locale, "getencoding", lambda: "cp1252")


def test_viewer_html_is_written_as_utf8(tmp_path, cp1252_default):
    out = tmp_path / "viewer.html"
    _render_unified(out, None, label_a="contour \u2192 mold")
    data = out.read_bytes()
    data.decode("utf-8")
    assert "\u2192".encode("utf-8") in data


def test_instruction_files_are_written_as_utf8(tmp_path, cp1252_default):
    install_instructions(tmp_path, "all", "build123d")
    for name in ("AGENTS.md", "CLAUDE.md"):
        data = (tmp_path / name).read_bytes()
        data.decode("utf-8")
        assert "\u2014".encode("utf-8") in data
    assert instructions_status(tmp_path, "build123d")["state"] == "current"


def test_skill_file_is_written_as_utf8(tmp_path, cp1252_default):
    install_skill(tmp_path, "build123d")
    data = (tmp_path / ".claude" / "skills" / "agentcad" / "SKILL.md").read_bytes()
    data.decode("utf-8")
    assert "\u2014".encode("utf-8") in data
    assert skill_status(tmp_path, "build123d")["state"] == "current"


def test_legacy_cp1252_instruction_file_is_healed(tmp_path, cp1252_default):
    (tmp_path / "AGENTS.md").write_bytes("Notes \u2014 keep me\n".encode("cp1252"))
    install_instructions(tmp_path, "agents", "build123d")
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "Notes \u2014 keep me" in text
    assert "<!-- agentcad:start -->" in text


def test_status_reports_legacy_cp1252_file_without_crashing(tmp_path, cp1252_default):
    (tmp_path / "AGENTS.md").write_bytes("Notes \u2014 keep me\n".encode("cp1252"))
    assert instructions_status(tmp_path, "build123d")["state"] == "missing"
