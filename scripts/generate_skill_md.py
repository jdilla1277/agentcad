#!/usr/bin/env python3
"""Generate SKILL.md for the public jdilla1277/agentcad-skill repo.

Source of truth:
- Body: GUIDE_BODY in src/agentcad/guide.py
- Base frontmatter: _BUILD123D_FRONTMATTER in src/agentcad/commands/skill.py
- Version: project.version in pyproject.toml

Adds marketplace-only fields (version, metadata.openclaw) so the result
satisfies both skills.sh (Vercel) and ClawHub (OpenClaw).

Usage: python scripts/generate_skill_md.py > SKILL.md
"""
from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _read_string_constant(source_path: Path, name: str) -> str:
    """Read a literal string constant without importing package dependencies."""
    module = ast.parse(source_path.read_text(), filename=str(source_path))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            continue
        value = ast.literal_eval(node.value)
        if isinstance(value, str):
            return value
    raise ValueError(f"{name} not found as a literal string in {source_path}")


def main() -> int:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    version = pyproject["project"]["version"]

    try:
        frontmatter_source = _read_string_constant(
            ROOT / "src" / "agentcad" / "commands" / "skill.py",
            "_BUILD123D_FRONTMATTER",
        )
        body = _read_string_constant(
            ROOT / "src" / "agentcad" / "guide.py",
            "GUIDE_BODY",
        )
    except (SyntaxError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    fm_match = re.match(r"^---\n(.*?)\n---\n", frontmatter_source, re.DOTALL)
    if not fm_match:
        print("error: _BUILD123D_FRONTMATTER has no YAML frontmatter", file=sys.stderr)
        return 1

    frontmatter = yaml.safe_load(fm_match.group(1)) or {}

    frontmatter["version"] = version
    frontmatter["metadata"] = {
        "openclaw": {
            "requires": {
                "bins": ["agentcad"],
                "anyBins": ["python3.12", "python3.11", "python3.10"],
            }
        }
    }

    sys.stdout.write("---\n")
    sys.stdout.write(
        yaml.safe_dump(frontmatter, sort_keys=False, default_flow_style=False).rstrip()
    )
    sys.stdout.write("\n---\n")
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
