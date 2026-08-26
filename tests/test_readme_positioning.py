from pathlib import Path


README = (Path(__file__).parents[1] / "README.md").read_text()


def test_readme_leads_with_build123d_default():
    assert "writes build123d Python scripts by default" in README
    assert "Your agent writes CadQuery or build123d" not in README
    assert "agentcad init --name phone-stand" in README


def test_readme_keeps_cadquery_as_explicit_compatibility_mode():
    assert "## CadQuery compatibility" in README
    assert "agentcad init --name legacy-model --runtime cadquery" in README
    assert "agentcad docs quickstart --runtime cadquery" in README
    assert "agentcad run legacy.py --label legacy --runtime cadquery" in README


def test_readme_preview_claim_matches_cli_behavior():
    assert "four-view PNG + turntable GIF" not in README
    assert "on-demand turntable GIF" in README
