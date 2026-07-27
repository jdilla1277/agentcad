"""Guard the package-index copy that agents see before installation."""

import tomllib
from pathlib import Path


PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"


def test_package_metadata_leads_with_build123d():
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    description = project["description"]
    keywords = project["keywords"]

    assert "build123d" in description
    assert "CadQuery compatibility" in description
    assert "Write CadQuery scripts" not in description
    assert keywords.index("build123d") < keywords.index("cadquery")
