#!/usr/bin/env python3
"""Generate SKILL.md for the public jdilla1277/agentcad-skill repo.

Source of truth:
- Body + base frontmatter: SKILL_CONTENT in app/src/agentcad/commands/skill.py
- Version: project.version in app/pyproject.toml

Adds marketplace-only fields (version, metadata.openclaw) so the result
satisfies both skills.sh (Vercel) and ClawHub (OpenClaw).

Usage: python scripts/generate_skill_md.py > SKILL.md
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    pyproject = tomllib.loads((ROOT / "app" / "pyproject.toml").read_text())
    version = pyproject["project"]["version"]

    skill_py = (ROOT / "app" / "src" / "agentcad" / "commands" / "skill.py").read_text()
    m = re.search(r'SKILL_CONTENT\s*=\s*"""\\?\n?(.*?)"""', skill_py, re.DOTALL)
    if not m:
        print("error: SKILL_CONTENT not found in skill.py", file=sys.stderr)
        return 1
    content = m.group(1)

    fm_match = re.match(r"^---\n(.*?)\n---\n(.*)", content, re.DOTALL)
    if not fm_match:
        print("error: SKILL_CONTENT has no YAML frontmatter", file=sys.stderr)
        return 1

    frontmatter = yaml.safe_load(fm_match.group(1)) or {}
    body = fm_match.group(2)

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
