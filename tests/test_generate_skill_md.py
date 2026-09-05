"""Regression tests for the public skill-repository generator."""

from pathlib import Path
import subprocess
import sys
import tomllib

import yaml


ROOT = Path(__file__).resolve().parent.parent


def test_generate_skill_md_uses_canonical_guide_and_package_version():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_skill_md.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    _, frontmatter_text, body = result.stdout.split("---\n", maxsplit=2)
    frontmatter = yaml.safe_load(frontmatter_text)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

    assert frontmatter["name"] == "agentcad"
    assert frontmatter["version"] == project["version"]
    assert frontmatter["metadata"]["openclaw"]["requires"]["bins"] == ["agentcad"]
    assert body.startswith("# agentcad — CAD tool for AI agents\n")
    assert "## Core workflow" in body
