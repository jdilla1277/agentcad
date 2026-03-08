import json
import shutil
from pathlib import Path

from cadtool.cli import cli
from cadtool.manifest import MANIFEST_FILE


SIMPLE_BOX_SCRIPT = """\
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10)
show_object(result)
"""


def _init_project(runner):
    runner.invoke(cli, ["init", "--name", "test_project"])


def _write_script(directory, content=SIMPLE_BOX_SCRIPT, filename="script.py"):
    path = directory / filename
    path.write_text(content)
    return path


def test_run_no_manifest_error(runner, isolated_dir):
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["command"] == "run"
    assert parsed["status"] == "error"
    assert "cadtool.json" in parsed["message"]


def test_run_script_not_found_error(runner, isolated_dir):
    _init_project(runner)
    result = runner.invoke(cli, ["run", "missing.py", "--output", "v1"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["command"] == "run"
    assert parsed["status"] == "error"
    assert "missing.py" in parsed["message"]


def test_run_creates_version_directory(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert (isolated_dir / "v1").is_dir()


def test_run_copies_script_to_version_dir(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    copied = isolated_dir / "v1" / "script.py"
    assert copied.exists()
    assert copied.read_text() == SIMPLE_BOX_SCRIPT


def test_run_produces_step_file(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    step = isolated_dir / "v1" / "output.step"
    assert step.exists()
    assert step.stat().st_size > 0


def test_run_success_json(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["command"] == "run"
    assert parsed["status"] == "success"
    assert parsed["version"] == 1
    assert parsed["label"] == "v1"
    assert "step" in parsed["outputs"]
    assert "script" in parsed["outputs"]


def test_run_creates_meta_json(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    meta_path = isolated_dir / "v1" / "meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["version"] == 1
    assert meta["label"] == "v1"
    assert meta["status"] == "success"
    assert "created" in meta
    assert "script" in meta
    assert "outputs" in meta


def test_run_updates_manifest_versions(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert len(manifest["versions"]) == 1
    entry = manifest["versions"][0]
    assert entry["version"] == 1
    assert entry["label"] == "v1"
    assert entry["status"] == "success"
    assert manifest["current"] == "v1"


def test_run_auto_increments_version(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "first"])
    result = runner.invoke(cli, ["run", "script.py", "--output", "second"])
    parsed = json.loads(result.output)
    assert parsed["version"] == 2
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert len(manifest["versions"]) == 2


def test_run_label_in_directory_name(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "my_label"])
    assert (isolated_dir / "v1_my_label").is_dir()
    assert (isolated_dir / "v1_my_label" / "output.step").exists()


def test_run_script_error_no_directory_created(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content="this is not valid python(")
    result = runner.invoke(cli, ["run", "script.py", "--output", "broken"])
    assert result.exit_code == 1
    assert not (isolated_dir / "v1_broken").exists()
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert len(manifest["versions"]) == 0
