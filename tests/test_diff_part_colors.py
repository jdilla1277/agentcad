"""Actual comparison PNGs preserve saved colors on both sides."""
import importlib.util
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from agentcad.cli import cli


def run(runner, source, label, *, diff=False, runtime="build123d"):
    Path("model.py").write_text(source)
    args = ["run", "model.py", "--label", label, "--no-preview", "--no-view", "--no-daemon", "--runtime", runtime]
    if not diff:
        args.append("--no-diff")
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def colored_script(first, second, runtime):
    if runtime == "cadquery":
        return f'''
a = cq.Workplane("XY").box(12, 12, 6).translate((-20, 0, 0))
b = cq.Workplane("XY").box(12, 12, 6).translate((20, 0, 0))
show_object(a, name="left", options={{"color":"{first}"}})
show_object(b, name="right", options={{"color":"{second}"}})
'''
    return f'''
show_object(Box(12, 12, 6).translate((-20, 0, 0)), id="left", options={{"color":"{first}"}})
show_object(Box(12, 12, 6).translate((20, 0, 0)), id="right", options={{"color":"{second}"}})
'''


def color_counts(image):
    rgb = np.asarray(image.convert("RGB"), dtype=float)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return {
        "red": int(((r > 70) & (r > 1.5 * g) & (r > 1.5 * b)).sum()),
        "blue": int(((b > 70) & (b > 1.5 * r) & (b > 1.5 * g)).sum()),
        "green": int(((g > 70) & (g > 1.5 * r) & (g > 1.5 * b)).sum()),
        "yellow": int(((r > 70) & (g > 70) & (r > 1.5 * b) & (g > 1.5 * b)).sum()),
    }


@pytest.mark.parametrize("runtime", ["build123d", pytest.param("cadquery", marks=pytest.mark.skipif(
    importlib.util.find_spec("cadquery") is None, reason="optional CadQuery runtime is absent",
))])
def test_saved_previous_and_current_colors_appear_in_written_diff(runner, isolated_dir, runtime):
    assert runner.invoke(cli, ["init", "--no-agent-setup", "--runtime", runtime]).exit_code == 0
    run(runner, colored_script("red", "blue", runtime), "before", runtime=runtime)
    # Metadata order is not a geometry identity. Reverse it to prevent a
    # positional implementation from applying colors to the wrong shapes.
    meta = Path("v1_before/meta.json")
    saved = json.loads(meta.read_text())
    saved["parts"].reverse()
    meta.write_text(json.dumps(saved))
    after = run(runner, colored_script("#00ff00", "#ffff00", runtime), "after", diff=True, runtime=runtime)
    image = Image.open(after["diff"]["side_by_side"])
    middle = image.width // 2
    before_colors = color_counts(image.crop((0, 36, middle, image.height)))
    after_colors = color_counts(image.crop((middle, 36, image.width, image.height)))
    assert before_colors["red"] > 200 and before_colors["blue"] > 200, before_colors
    assert before_colors["green"] == 0 and before_colors["yellow"] == 0, before_colors
    assert after_colors["green"] > 200 and after_colors["yellow"] > 200, after_colors
    assert after_colors["red"] == 0 and after_colors["blue"] == 0, after_colors
    assert Path(after["diff"]["overlay"]).is_file()
    assert not any("Could not render" in warning for warning in after.get("warnings", []))


@pytest.mark.parametrize("legacy", ["no_parts", "no_colors", "incomplete"])
def test_legacy_metadata_keeps_complete_comparison_rendering(runner, isolated_dir, legacy):
    assert runner.invoke(cli, ["init", "--no-agent-setup"]).exit_code == 0
    run(runner, colored_script("red", "blue", "build123d"), "before")
    path = Path("v1_before/meta.json")
    meta = json.loads(path.read_text())
    if legacy == "no_parts":
        meta.pop("parts")
    elif legacy == "no_colors":
        for part in meta["parts"]:
            part.pop("color")
    else:
        meta["parts"] = meta["parts"][:1]
    path.write_text(json.dumps(meta))
    result = run(runner, colored_script("#00ff00", "#ffff00", "build123d"), "after", diff=True)
    assert result["status"] == "success"
    assert Path(result["diff"]["side_by_side"]).is_file()
    assert Path(result["diff"]["overlay"]).is_file()
    if legacy == "incomplete":
        assert any("Previous part colors were not restored" in warning for warning in result.get("warnings", []))
