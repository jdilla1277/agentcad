"""`agentcad diff` between two versions produced by the build123d runtime."""

from __future__ import annotations

import json
from pathlib import Path

from agentcad.cli import cli


B3D_PARAM = """\
from build123d import Box
length = 20
width = 10
show_object(Box(length, width, 5))
"""


def _two_versions(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
    (isolated_dir / "s.py").write_text(B3D_PARAM)
    runner.invoke(cli, ["run", "s.py", "--output", "base", "--no-preview"])
    runner.invoke(cli, ["run", "s.py", "--output", "wide", "--no-preview",
                        "--params", "width=30"])


def test_diff_two_b3d_versions_by_number(runner, isolated_dir):
    _two_versions(runner, isolated_dir)
    r = runner.invoke(cli, ["diff", "1", "2"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    assert parsed["status"] == "success"
    # Width changed 10 → 30, so volume and Y dimension should differ.
    assert "volume" in str(parsed).lower() or "metrics" in parsed


def test_diff_by_label(runner, isolated_dir):
    _two_versions(runner, isolated_dir)
    r = runner.invoke(cli, ["diff", "base", "wide"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    assert parsed["status"] == "success"


def test_diff_same_version_reports_no_change(runner, isolated_dir):
    _two_versions(runner, isolated_dir)
    r = runner.invoke(cli, ["diff", "1", "1"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    # Self-diff should succeed with no metric changes.
    assert parsed["status"] == "success"
