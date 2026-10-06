"""Packaging checks for the skills-only OpenAI AgentCAD plugin."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "agentcad"
SKILL = PLUGIN / "skills" / "agentcad"


def _skill_frontmatter() -> dict[str, object]:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, frontmatter, _ = text.split("---", 2)
    return yaml.safe_load(frontmatter)


def test_plugin_manifest_declares_a_portable_skills_only_package() -> None:
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))

    assert manifest["$schema"] == (
        "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    )
    assert manifest["name"] == "agentcad"
    assert manifest["license"] == "Apache-2.0"
    assert manifest["extensions"]["com.openai"]["interface"]["displayName"] == (
        "AgentCAD"
    )
    assert (SKILL / "SKILL.md").is_file()
    assert not (PLUGIN / "mcp.json").exists()


def test_skill_metadata_and_openai_ui_are_wired_together() -> None:
    metadata = _skill_frontmatter()
    ui = yaml.safe_load(
        (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
    )["interface"]

    assert metadata["name"] == "agentcad"
    assert "open-source AgentCAD CLI" in metadata["description"]
    assert ui["display_name"] == "AgentCAD"
    assert "$agentcad" in ui["default_prompt"]


def test_local_marketplace_resolves_to_the_plugin() -> None:
    marketplace_path = ROOT / ".agents" / "plugins" / "marketplace.json"
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    entry = marketplace["plugins"][0]

    assert entry["name"] == "agentcad"
    assert entry["source"]["source"] == "local"
    assert entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    assert entry["category"]
    assert (ROOT / entry["source"]["path"]).resolve() == PLUGIN.resolve()


def test_cloud_reference_matches_supported_python_range() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    cloud_reference = (SKILL / "references" / "cloud-environment.md").read_text(
        encoding="utf-8"
    )

    assert 'requires-python = ">=3.10,<3.13"' in pyproject
    assert "Python 3.10, 3.11, or 3.12" in (SKILL / "SKILL.md").read_text(
        encoding="utf-8"
    )
    for executable in ("python3.10", "python3.11", "python3.12"):
        assert executable in cloud_reference
