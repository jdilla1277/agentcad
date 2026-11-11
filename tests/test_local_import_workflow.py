"""Scripts can use nearby helpers through the real CLI in either runtime."""
# collect-on-default-profile
import importlib.util
import json

import pytest

from agentcad.cli import cli


@pytest.mark.parametrize("runtime", ["build123d", "cadquery"])
def test_broken_local_helper_reports_its_actual_traceback(runner, isolated_dir, runtime):
    if importlib.util.find_spec(runtime) is None:
        pytest.skip("optional runtime is absent")
    (isolated_dir / "local_part.py").write_text("import totally_missing_dependency_xyz\ndef make_part(): return None\n")
    (isolated_dir / "model.py").write_text("from local_part import make_part\nshow_object(make_part())\n")
    assert runner.invoke(cli, ["init", "--runtime", runtime, "--no-agent-setup"]).exit_code == 0
    response = runner.invoke(cli, ["run", "model.py", "--label", "broken", "--no-preview", "--no-view", "--no-diff", "--no-daemon"])
    assert response.exit_code != 0
    assert "totally_missing_dependency_xyz" in response.stdout
    assert "local_part.py" in response.stdout
    assert "module 'local_part' not found" not in response.stdout


@pytest.mark.parametrize("runtime", ["build123d", "cadquery"])
def test_script_directory_and_project_package_imports(runner, isolated_dir, runtime):
    if importlib.util.find_spec(runtime) is None:
        pytest.skip("optional runtime is absent")
    package = isolated_dir / "parts"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "dimensions.py").write_text("WIDTH = 40\n")
    scripts = isolated_dir / "scripts"
    scripts.mkdir()
    if runtime == "build123d":
        body = "from build123d import *\nfrom parts.dimensions import WIDTH\ndef make_part():\n    base = Box(WIDTH, 30, 6)\n    return fillet(base.edges().filter_by(Axis.Z), 2) - Cylinder(4, 20)\n"
    else:
        body = 'import cadquery as cq\nfrom parts.dimensions import WIDTH\ndef make_part():\n    return cq.Workplane("XY").box(WIDTH,30,6).edges("|Z").fillet(2).faces(">Z").workplane().hole(8)\n'
    (scripts / "local_part.py").write_text(body)
    (scripts / "model.py").write_text("from local_part import make_part\nshow_object(make_part())\n")
    assert runner.invoke(cli, ["init", "--runtime", runtime, "--no-agent-setup"]).exit_code == 0
    response = runner.invoke(cli, ["run", "scripts/model.py", "--label", "local", "--no-preview", "--no-view", "--no-diff", "--no-daemon"])
    assert response.exit_code == 0, response.output
    data = json.loads(response.stdout)
    assert data["status"] == "success"
    assert data["metrics"]["dimensions"]["x"] == pytest.approx(40)
    assert data["metrics"]["is_valid"] is True
