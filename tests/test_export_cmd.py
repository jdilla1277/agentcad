import json
from pathlib import Path

from agentcad.cli import cli


SIMPLE_BOX_SCRIPT = """\
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10)
show_object(result)
"""


def _init_project(runner):
    runner.invoke(
        cli,
        ["init", "--name", "test_project", "--runtime", "cadquery"],
    )


def _create_step(runner, isolated_dir):
    """Create a version directory with a STEP file via agentcad run."""
    _init_project(runner)
    path = isolated_dir / "script.py"
    path.write_text(SIMPLE_BOX_SCRIPT)
    runner.invoke(cli, ["run", "script.py", "--output", "box"])
    return isolated_dir / "v1_box" / "output.step"


def _create_standalone_step(isolated_dir):
    """Create a standalone STEP file (not in a version directory)."""
    from cadquery import Workplane, exporters

    shape = Workplane("XY").box(10, 10, 10)
    step_path = isolated_dir / "model.step"
    exporters.export(shape, str(step_path))
    return step_path


def test_export_step_not_found(runner, isolated_dir):
    result = runner.invoke(cli, ["export", "nonexistent.step", "--format", "stl"])
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["command"] == "export"
    assert parsed["status"] == "error"


def test_export_stl_produces_file(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["export", str(step_path), "--format", "stl"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    stl_path = Path(parsed["outputs"]["stl"])
    assert stl_path.exists()
    assert stl_path.stat().st_size > 0


def test_export_stl_is_valid_mesh(runner, isolated_dir):
    """The OCCT STL writer must produce a structurally valid mesh file."""
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["export", str(step_path), "--format", "stl"])
    assert result.exit_code == 0
    stl_path = Path(json.loads(result.stdout)["outputs"]["stl"])
    raw = stl_path.read_bytes()
    if raw.startswith(b"solid"):
        text = raw.decode()
        assert text.count("facet normal") >= 12  # a box tessellates to >= 12 tris
        assert text.count("facet normal") == text.count("endfacet")
        assert text.rstrip().splitlines()[-1].startswith("endsolid")
    else:
        # binary STL: 80-byte header + uint32 count + 50 bytes per triangle
        import struct

        (count,) = struct.unpack_from("<I", raw, 80)
        assert count >= 12
        assert len(raw) == 84 + 50 * count


def test_export_glb_produces_file(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["export", str(step_path), "--format", "glb"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    glb_path = Path(parsed["outputs"]["glb"])
    assert glb_path.exists()
    assert glb_path.stat().st_size > 0


def test_export_multiple_formats(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["export", str(step_path), "--format", "stl,glb"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert "stl" in parsed["outputs"]
    assert "glb" in parsed["outputs"]
    assert Path(parsed["outputs"]["stl"]).exists()
    assert Path(parsed["outputs"]["glb"]).exists()


def test_export_json_response(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["export", str(step_path), "--format", "stl"])
    parsed = json.loads(result.stdout)
    assert parsed["command"] == "export"
    assert parsed["status"] == "success"
    assert "outputs" in parsed


def test_export_version_dir_updates_meta(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["export", str(step_path), "--format", "stl,glb"])
    assert result.exit_code == 0
    meta = json.loads((isolated_dir / "v1_box" / "meta.json").read_text())
    assert "stl" in meta["outputs"]
    assert "glb" in meta["outputs"]


def test_export_standalone_step(runner, isolated_dir):
    step_path = _create_standalone_step(isolated_dir)
    result = runner.invoke(cli, ["export", str(step_path), "--format", "stl"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    stl_path = Path(parsed["outputs"]["stl"])
    assert stl_path.exists()
    # File saved next to the STEP file, not in a subdirectory
    assert stl_path.parent == step_path.parent


def test_export_invalid_format_error(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["export", str(step_path), "--format", "fbx"])
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["command"] == "export"
    assert parsed["status"] == "error"


def test_export_empty_format_list_error(runner, isolated_dir):
    """`--format ","` trims down to no formats at all. It must error rather than
    report success with an empty outputs map."""
    # File contents are never read: format validation precedes the CAD import.
    step_path = isolated_dir / "model.step"
    step_path.write_text("")
    result = runner.invoke(cli, ["export", str(step_path), "--format", ","])
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["command"] == "export"
    assert parsed["status"] == "error"
    assert "No export formats specified" in parsed["message"]
    assert "Supported: stl, glb, obj" in parsed["message"]


def test_export_obj_produces_file(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["export", str(step_path), "--format", "obj"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    obj_path = Path(parsed["outputs"]["obj"])
    assert obj_path.exists()
    assert obj_path.stat().st_size > 0


def test_export_all_three_formats(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["export", str(step_path), "--format", "stl,glb,obj"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert Path(parsed["outputs"]["stl"]).exists()
    assert Path(parsed["outputs"]["glb"]).exists()
    assert Path(parsed["outputs"]["obj"]).exists()


# --- Daemon routing (#177) ---

def test_export_routes_through_daemon_when_available(runner, isolated_dir, monkeypatch):
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    daemon_output = json.dumps({
        "command": "export", "status": "success",
        "outputs": {"stl": "v1/output.stl"},
    })
    monkeypatch.setattr(
        "agentcad.daemon.send_request",
        lambda *a, **kw: {"type": "result", "exit_code": 0, "output": daemon_output},
    )
    result = runner.invoke(cli, ["export", "any.step", "--format", "stl"])
    assert result.exit_code == 0, result.stdout
    parsed = json.loads(result.stdout)
    assert parsed["via"] == "daemon"
    assert parsed["command"] == "export"


def test_export_direct_no_via_field(runner, isolated_dir):
    """Direct execution omits the `via` field."""
    step_path = _create_standalone_step(isolated_dir)
    result = runner.invoke(cli, ["export", str(step_path), "--format", "stl"])
    parsed = json.loads(result.stdout)
    assert "via" not in parsed


def test_export_no_daemon_flag_skips_routing(runner, isolated_dir, monkeypatch):
    step_path = _create_standalone_step(isolated_dir)
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    calls: list[tuple] = []
    def _track(*a, **kw):
        calls.append((a, kw))
        return None
    monkeypatch.setattr("agentcad.daemon.send_request", _track)
    result = runner.invoke(cli, ["export", str(step_path), "--format", "stl", "--no-daemon"])
    assert result.exit_code == 0, result.stdout
    parsed = json.loads(result.stdout)
    assert "via" not in parsed
    assert calls == [], f"send_request was called despite --no-daemon: {calls}"
