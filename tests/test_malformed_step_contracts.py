"""Subprocess-level contract: render / export / view never leak Python
tracebacks (or OCCT diagnostics) on malformed STEP input.

Caught by the 2bc friction-test: load_cad_shape was silencing the OCCT
fd-1 leak correctly, but raised ValueError that escaped render/export/view
unhandled — meaning malformed STEPs produced empty stdout + a Python
traceback on stderr instead of a JSON error envelope. Tests are at the
subprocess layer because CliRunner can't see fd-level output the same
way a real shell does.
"""
import json
import subprocess
import sys
from pathlib import Path

import cadquery as cq
import pytest
from cadquery import exporters


@pytest.fixture
def truncated_step(tmp_path):
    """A STEP file with the right magic but cut off mid-parse — triggers
    OCCT's parser to fail. The previous behavior was to leak a red ANSI
    line to stdout (1a's bug for inspect, latent in render/export/view
    until 2c's silencer fix); the regression caught here is that the
    silencer alone leaves an unhandled ValueError on those three commands."""
    real = tmp_path / "_full.step"
    box = cq.Workplane("XY").box(10, 10, 10)
    exporters.export(box, str(real))
    truncated = tmp_path / "truncated.step"
    truncated.write_text(real.read_text()[: len(real.read_text()) // 2])
    return truncated


def _agentcad_bin() -> Path:
    return Path(sys.executable).parent / "agentcad"


def _run(*args, cwd=None):
    return subprocess.run(
        [str(_agentcad_bin()), *args],
        capture_output=True, text=True, cwd=str(cwd) if cwd else None,
    )


def _assert_clean_json_envelope(result, expected_command: str):
    """No Python traceback, no OCCT raw-text leak, parseable JSON on stdout."""
    assert "Traceback" not in result.stderr, \
        f"Python traceback leaked to stderr:\n{result.stderr}"
    assert "**** ERR" not in result.stdout, \
        f"OCCT diagnostic leaked to stdout:\n{result.stdout}"
    parsed = json.loads(result.stdout)
    assert parsed["command"] == expected_command
    assert parsed["status"] in {"malformed", "error"}
    assert "message" in parsed


class TestRenderMalformedContract:
    def test_render_truncated_step_returns_clean_json(self, truncated_step):
        result = _run("render", str(truncated_step), "--view", "iso")
        _assert_clean_json_envelope(result, "render")


class TestExportMalformedContract:
    def test_export_truncated_step_returns_clean_json(self, truncated_step):
        result = _run("export", str(truncated_step), "--format", "glb")
        _assert_clean_json_envelope(result, "export")


class TestViewMalformedContract:
    def test_view_truncated_step_returns_clean_json(self, truncated_step):
        # `agentcad view` opens a browser on success; on a malformed file
        # it should fail before reaching the browser launch with a clean
        # JSON envelope. We don't pass --no-browser (the flag may not
        # exist) — we just verify the JSON contract.
        result = _run("view", str(truncated_step))
        _assert_clean_json_envelope(result, "view")
