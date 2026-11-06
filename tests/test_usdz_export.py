"""Optional USDZ export: the missing-extra contract, the writer, and the checker.

The missing-extra tests run on every profile (they hide OpenUSD in-process).
Everything that writes or reopens a real USDZ needs ``agentcad[usdz]`` and
skips without it.
"""

import importlib.util
import io
import json
import shutil
import struct
import subprocess
import sys
import types
import zipfile
import zlib
from pathlib import Path

import pytest

from agentcad.cli import cli
from agentcad.step_io import load_cad_shape

FIXTURES = Path(__file__).parent / "fixtures" / "validation"
HAS_USD = importlib.util.find_spec("pxr") is not None
needs_usd = pytest.mark.skipif(not HAS_USD, reason='needs pip install "agentcad[usdz]"')

PARTS_SCRIPT = (
    'plate = Box(60, 40, 8) - Pos(20, 0, 0) * Cylinder(4, 20)\n'
    'post = Pos(0, 0, 24) * Cylinder(6, 40)\n'
    'show_object(plate, name="base plate", options={"color": "steelblue"})\n'
    'show_object(post, name="post", options={"color": "#ff0000"})\n'
)


def _hide_pxr(monkeypatch, replacement):
    # Earlier tests may have imported real pxr submodules, and `from pxr
    # import X` falls back to sys.modules["pxr.X"], so remove those entries.
    for name in [m for m in sys.modules if m.startswith("pxr.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "pxr", replacement)


def _no_pxr(monkeypatch):
    _hide_pxr(monkeypatch, None)


def _copy_fixture(directory, name="closed_box"):
    target = directory / "part.step"
    shutil.copyfile(FIXTURES / f"{name}.step", target)
    return target


def _run(runner, *args):
    return runner.invoke(cli, ["run", *args, "--no-preview", "--no-view", "--no-diff", "--no-daemon"])


# ---- missing / broken optional dependency (every profile) -----------------

def test_export_without_extra_fails_before_any_output(runner, isolated_dir, monkeypatch):
    source = _copy_fixture(isolated_dir)
    _no_pxr(monkeypatch)
    import agentcad.step_io as step_io
    monkeypatch.setattr(step_io, "load_cad_shape", lambda *_: pytest.fail("geometry loaded"))
    result = runner.invoke(cli, ["export", str(source), "--format", "stl,usdz", "--no-daemon"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "error"
    assert data["error_kind"] == "optional_dependency_missing"
    assert data["failed_format"] == "usdz"
    assert data["install_command"] == 'pip install "agentcad[usdz]"'
    assert 'pip install "agentcad[usdz]"' in data["message"]
    assert "agentcad daemon restart" in data["message"]
    assert data["next_actions"][0] == 'pip install "agentcad[usdz]"'
    assert [p.name for p in isolated_dir.glob("part.*")] == ["part.step"]


def test_run_without_extra_does_not_execute_or_allocate(runner, isolated_dir, monkeypatch):
    assert runner.invoke(cli, ["init"]).exit_code == 0
    manifest = (isolated_dir / "agentcad.json").read_text()
    (isolated_dir / "part.py").write_text(
        'open("executed.txt", "w").write("x")\nshow_object(Box(10, 10, 10))\n'
    )
    _no_pxr(monkeypatch)
    result = _run(runner, "part.py", "--label", "first", "--export", "glb,usdz")
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["error_kind"] == "optional_dependency_missing"
    assert data["failed_format"] == "usdz"
    assert data["artifact_created"] is False
    assert not (isolated_dir / "executed.txt").exists()
    assert not list(isolated_dir.glob("v*_first"))
    assert (isolated_dir / "agentcad.json").read_text() == manifest


def test_broken_openusd_is_reported_distinctly(runner, isolated_dir, monkeypatch):
    source = _copy_fixture(isolated_dir)
    _hide_pxr(monkeypatch, types.ModuleType("pxr"))  # a pxr without its native submodules
    result = runner.invoke(cli, ["export", str(source), "--format", "usdz", "--no-daemon"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data.get("error_kind") == "optional_dependency_broken", data
    assert data["failed_format"] == "usdz"
    assert "--force-reinstall" in data["install_command"]
    assert data["diagnostic"] and len(data["diagnostic"]) <= 500


def test_mcp_export_without_extra_matches_cli(isolated_dir, monkeypatch):
    from agentcad.mcp.server import export
    source = _copy_fixture(isolated_dir)
    _no_pxr(monkeypatch)
    data = export(str(source), "usdz", str(isolated_dir))
    assert data["status"] == "error"
    assert data["error_kind"] == "optional_dependency_missing"
    assert data["install_command"] == 'pip install "agentcad[usdz]"'


def test_non_usdz_paths_never_import_openusd(tmp_path):
    code = (
        "import sys\n"
        "import agentcad.cli, agentcad.export, agentcad.written_mesh, agentcad.usdz\n"
        "from agentcad.commands.export_cmd import usdz_preflight\n"
        "assert usdz_preflight('export', 'stl,glb,obj') is None\n"
        "assert not any(m == 'pxr' or m.startswith('pxr.') for m in sys.modules), 'pxr imported'\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=tmp_path)


def test_unsupported_format_message_lists_optional_usdz(runner, isolated_dir):
    source = _copy_fixture(isolated_dir)
    result = runner.invoke(cli, ["export", str(source), "--format", "fbx"])
    message = json.loads(result.stdout)["message"]
    assert 'stl, glb, obj, usdz (usdz needs pip install "agentcad[usdz]")' in message
    blank = json.loads(runner.invoke(cli, ["export", str(source), "--format", ","]).stdout)
    assert "usdz" in blank["message"]


def test_usdz_file_is_a_display_format_for_import(runner, isolated_dir):
    path = isolated_dir / "model.usdz"
    path.write_bytes(b"PK\x03\x04" + b"\0" * 60)
    data = json.loads(runner.invoke(cli, ["import", str(path)]).stdout)
    assert data["status"] == "display_format"
    assert data["format_detected"] == "usdz"


# ---- writer ---------------------------------------------------------------

_OPEN_STAGES = []  # prims expire with their stage; keep test stages alive


def _stage(path):
    from pxr import Usd
    stage = Usd.Stage.Open(str(path))
    _OPEN_STAGES.append(stage)
    return stage


def _meshes(stage):
    from pxr import UsdGeom
    return [UsdGeom.Mesh(p) for p in stage.Traverse() if p.GetTypeName() == "Mesh"]


def _bound_color(mesh):
    from pxr import UsdShade
    material, _ = UsdShade.MaterialBindingAPI(mesh.GetPrim()).ComputeBoundMaterial()
    shader = material.ComputeSurfaceSource()[0]
    return tuple(round(c, 3) for c in shader.GetInput("diffuseColor").Get())


@needs_usd
def test_export_writes_validated_millimetre_y_up_package(runner, isolated_dir):
    from pxr import UsdGeom
    source = _copy_fixture(isolated_dir)
    result = runner.invoke(cli, ["export", str(source), "--format", "usdz", "--no-daemon"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "success"
    path = Path(data["outputs"]["usdz"])
    assert path.name == "part.usdz" and path.is_file()
    report = data["mesh_validation"]["usdz"]
    assert report["is_valid"] is True, report
    assert report["triangle_count"] == 12
    assert report["watertight"] and report["manifold"]
    package = report["layers"]["file_parse"]["usdz"]
    assert package["members"] == ["model.usdc"]
    assert package["up_axis"] == "Y" and package["meters_per_unit"] == 0.001
    assert package["compliance"]["status"] == "pass"
    assert package["compliance"]["errors"] == []
    assert data["warnings"] == []
    stage = _stage(path)
    assert UsdGeom.GetStageUpAxis(stage) == "Y"
    assert UsdGeom.GetStageMetersPerUnit(stage) == 0.001
    assert str(stage.GetDefaultPrim().GetPath()) == "/Model"
    assert "agentcad" in stage.GetRootLayer().customLayerData["creator"]
    assert all(m.GetSubdivisionSchemeAttr().Get() == "none" for m in _meshes(stage))


@needs_usd
def test_tall_z_model_is_tall_on_usd_y_at_true_size(tmp_path):
    from build123d import Box
    from agentcad.usdz import export_usdz
    path = tmp_path / "tall.usdz"
    export_usdz(Box(10, 20, 40).wrapped, path)
    (mesh,) = _meshes(_stage(path))
    low, high = mesh.GetExtentAttr().Get()
    assert tuple(high - low) == (10, 40, 20)  # X stays, CAD Z -> USD Y, CAD Y -> USD Z


@needs_usd
def test_run_preserves_part_names_colors_and_metadata(runner, isolated_dir):
    assert runner.invoke(cli, ["init"]).exit_code == 0
    (isolated_dir / "part.py").write_text(PARTS_SCRIPT)
    result = _run(runner, "part.py", "--label", "ar", "--export", "stl,glb,obj,usdz")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["outputs"]["usdz"].endswith("v1_ar/output.usdz")
    assert set(data["mesh_validation"]) == {"stl", "glb", "obj", "usdz"}
    assert data["mesh_validation"]["usdz"]["is_valid"] is True, data["mesh_validation"]["usdz"]
    assert data["mesh_validation"]["usdz"]["triangle_count"] == data["mesh_validation"]["glb"]["triangle_count"]
    meta = json.loads((isolated_dir / "v1_ar" / "meta.json").read_text())
    assert meta["outputs"]["usdz"] == "v1_ar/output.usdz"
    assert meta["mesh_validation"]["usdz"] == data["mesh_validation"]["usdz"]
    meshes = {str(m.GetPath()): m for m in _meshes(_stage(isolated_dir / "v1_ar" / "output.usdz"))}
    assert set(meshes) == {"/Model/base_plate", "/Model/post"}
    assert meshes["/Model/base_plate"].GetPrim().GetCustomDataByKey("agentcad:part_id") == "base_plate"
    assert _bound_color(meshes["/Model/base_plate"]) == (0.27, 0.51, 0.71)  # steelblue
    assert _bound_color(meshes["/Model/post"]) == (1.0, 0.0, 0.0)


@needs_usd
@pytest.mark.parametrize("case", ["closed_box", "disjoint_solids", "loose_face_compound",
                                  "touching_edge_solids", "open_shell"])
def test_step_only_export_matches_glb_bodies(tmp_path, case):
    """USDZ keeps the same bodies and loose faces as GLB, one mesh per solid."""
    from agentcad.export import export_glb
    from agentcad.usdz import export_usdz
    from agentcad.written_mesh import validate_written_mesh
    shape = load_cad_shape(FIXTURES / f"{case}.step")
    export_glb(shape, str(tmp_path / "m.glb"))
    export_usdz(shape, tmp_path / "m.usdz")
    glb, usdz = (validate_written_mesh(tmp_path / f"m.{ext}") for ext in ("glb", "usdz"))
    for key in ("is_valid", "triangle_count", "watertight", "manifold"):
        assert usdz[key] == glb[key], (key, usdz, glb)
    colors = [_bound_color(m) for m in _meshes(_stage(tmp_path / "m.usdz"))]
    assert len(colors) == len(set(colors))


@needs_usd
def test_sanitized_duplicate_part_names_stay_unique_and_traceable(tmp_path):
    from build123d import Box, Pos
    from agentcad.usdz import export_usdz
    a, b = Box(5, 5, 5).wrapped, (Pos(10, 0, 0) * Box(5, 5, 5)).wrapped
    parts = [{"id": "1 bracket/left", "topo_shape": a, "color": None},
             {"id": "1 bracket/left", "topo_shape": b, "color": "not-a-color"},
             {"id": "Materials", "topo_shape": b, "color": None}]
    export_usdz(a, tmp_path / "m.usdz", parts=parts)
    meshes = _meshes(_stage(tmp_path / "m.usdz"))
    names = [m.GetPrim().GetName() for m in meshes]
    assert len(set(names)) == 3 and "Materials" not in names
    assert [m.GetPrim().GetCustomDataByKey("agentcad:part_id") for m in meshes] == [
        "1 bracket/left", "1 bracket/left", "Materials"]
    assert meshes[0].GetPrim().GetDisplayName() == "1 bracket/left"


@needs_usd
def test_package_leaks_no_paths_and_leaves_no_temp_files(tmp_path):
    from build123d import Box
    from agentcad.usdz import export_usdz
    out = tmp_path / "private-dir-name"
    out.mkdir()
    export_usdz(Box(1, 2, 3).wrapped, out / "m.usdz")
    data = (out / "m.usdz").read_bytes()
    assert b"private-dir-name" not in data and b".agentcad-usdz" not in data
    assert [p.name for p in out.iterdir()] == ["m.usdz"]


@needs_usd
def test_same_geometry_gives_identical_bytes(tmp_path):
    from build123d import Box
    from agentcad.usdz import export_usdz
    from agentcad.written_mesh import validate_written_mesh
    export_usdz(Box(4, 5, 6).wrapped, tmp_path / "a.usdz")
    export_usdz(Box(4, 5, 6).wrapped, tmp_path / "b.usdz")
    assert (tmp_path / "a.usdz").read_bytes() == (tmp_path / "b.usdz").read_bytes()
    # OpenUSD stamps the wall clock; pinned stamps keep later runs identical too.
    with zipfile.ZipFile(tmp_path / "a.usdz") as archive:
        assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in archive.infolist())
    assert validate_written_mesh(tmp_path / "a.usdz")["is_valid"] is True


@needs_usd
def test_export_warns_when_replacing_run_part_aware_file(runner, isolated_dir):
    assert runner.invoke(cli, ["init"]).exit_code == 0
    (isolated_dir / "part.py").write_text(PARTS_SCRIPT)
    assert _run(runner, "part.py", "--label", "ar", "--export", "usdz").exit_code == 0
    step = isolated_dir / "v1_ar" / "output.step"
    data = json.loads(runner.invoke(cli, ["export", str(step), "--format", "stl,usdz",
                                          "--no-daemon"]).stdout)
    (warning,) = data["warnings"]
    assert warning.startswith("USDZ replaced this version's run-written file")
    assert "--export usdz" in warning


@needs_usd
def test_packaging_failure_publishes_nothing(runner, isolated_dir, monkeypatch):
    from pxr import UsdUtils
    source = _copy_fixture(isolated_dir)
    monkeypatch.setattr(UsdUtils, "CreateNewUsdzPackage", lambda *a: False)
    result = runner.invoke(cli, ["export", str(source), "--format", "stl,usdz", "--no-daemon"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["failed_format"] == "usdz"
    assert "could not package" in data["message"]
    assert set(data["outputs"]) == {"stl"} and data["mesh_validation"]["stl"]["is_valid"] is True
    assert sorted(p.name for p in isolated_dir.glob("part.*")) == ["part.step", "part.stl"]
    assert not list(isolated_dir.glob(".agentcad-usdz-*"))


# ---- checker --------------------------------------------------------------

def _aligned_zip(path, members):
    """Write a stored, 64-byte aligned zip the way USDZ requires."""
    out = io.BytesIO()
    central = []
    for name, payload in members:
        encoded = name.encode()
        offset = out.tell()
        pad = (-(offset + 30 + len(encoded) + 4)) % 64
        extra = struct.pack("<HH", 0x1986, pad) + b"\0" * pad
        crc = zlib.crc32(payload)
        out.write(struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0, crc,
                              len(payload), len(payload), len(encoded), len(extra)))
        out.write(encoded + extra + payload)
        central.append((encoded, crc, len(payload), offset))
    start = out.tell()
    for encoded, crc, size, offset in central:
        out.write(struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 0, 0, 0, crc,
                              size, size, len(encoded), 0, 0, 0, 0, 0, offset) + encoded)
    out.write(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(central), len(central),
                          out.tell() - start, start, 0))
    path.write_bytes(out.getvalue())
    return path


TETRA = """#usda 1.0
(
    defaultPrim = "Model"
    metersPerUnit = 0.001
    upAxis = "Y"
)
def Xform "Model"
{
    def Mesh "tet"
    {
        int[] faceVertexCounts = [3, 3, 3, 3]
        int[] faceVertexIndices = [0, 2, 1, 0, 1, 3, 1, 2, 3, 2, 0, 3]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
        uniform token subdivisionScheme = "none"
    }
}
"""


def _check(tmp_path, text, name="m.usdz"):
    from agentcad.written_mesh import validate_written_mesh
    return validate_written_mesh(_aligned_zip(tmp_path / name, [("model.usda", text.encode())]))


@needs_usd
def test_hand_packaged_tetra_is_valid(tmp_path):
    report = _check(tmp_path, TETRA)
    assert report["is_valid"] is True, report
    assert report["triangle_count"] == 4


@needs_usd
@pytest.mark.parametrize("change,expected", [
    (('defaultPrim = "Model"', ''), "defaultPrim"),
    (("metersPerUnit = 0.001", "metersPerUnit = 0.01"), "metersPerUnit"),
    (('upAxis = "Y"', 'upAxis = "Z"'), "upAxis"),
    (("1, 2, 3, 2, 0, 3]", "1, 2, 3, 2, 0, 9]"), "out of range"),
    (("[3, 3, 3, 3]", "[3, 3, 3]"), "do not match"),
    (('def Xform "Model"', 'def Xform "Model" (references = @missing.usda@)'), "unresolved"),
])
def test_stage_defects_fail_parsing(tmp_path, change, expected):
    report = _check(tmp_path, TETRA.replace(*change))
    assert report["is_valid"] is False, report
    assert report["first_failure"] == "file_parse"
    assert expected in report["message"]


@needs_usd
def test_external_file_reference_fails(tmp_path):
    (tmp_path / "outside.usda").write_text('#usda 1.0\ndef Xform "X" {}\n')
    text = TETRA.replace('def Xform "Model"',
                         f'def Xform "Model" (references = @{tmp_path / "outside.usda"}@)')
    report = _check(tmp_path, text)
    assert report["is_valid"] is False
    assert "external file" in report["message"]


@needs_usd
@pytest.mark.parametrize("change,expected", [
    (('"none"', '"catmullClark"'), "subdivisionScheme"),
    (("[3, 3, 3, 3]", "[4, 4, 4]"), "non-triangle"),
    (('def Mesh "tet"', 'def Sphere "ball" {}\n    def Mesh "tet"'), "not supported"),
    (("point3f[] points = [", "point3f[] points.timeSamples = {0: ["),
     "Animated"),
])
def test_unsupported_usd_features_are_unknown(tmp_path, change, expected):
    text = TETRA.replace(*change)
    if "timeSamples" in text:
        text = text.replace("(0, 0, 1)]", "(0, 0, 1)]}")
    report = _check(tmp_path, text)
    assert report["is_valid"] is None, report
    assert report["status"] == "error"
    assert expected in report["message"]


@needs_usd
def test_open_mesh_fails_manifold_and_mirroring_flips_winding(tmp_path):
    open_mesh = _check(tmp_path, TETRA.replace("[3, 3, 3, 3]", "[3, 3, 3]")
                       .replace(", 2, 0, 3]", "]"), "open.usdz")
    assert open_mesh["is_valid"] is False and open_mesh["first_failure"] == "mesh_manifold"
    assert open_mesh["watertight"] is False
    mirrored = TETRA.replace('def Mesh "tet"\n    {',
                             'def Mesh "tet"\n    {\n        double3 xformOp:scale = (-1, 1, 1)\n'
                             '        uniform token[] xformOpOrder = ["xformOp:scale"]')
    # Mirroring alone keeps a closed, consistently wound surface.
    assert _check(tmp_path, mirrored, "mirror.usdz")["is_valid"] is True


@needs_usd
@pytest.mark.parametrize("mutate,expected", [
    (lambda data: data[: len(data) // 2], "zip container"),
    ("deflate", "compressed"),
    ("unaligned", "64-byte aligned"),
    (lambda data: data[:120] + bytes([data[120] ^ 0xFF]) + data[121:], "CRC"),
])
def test_corrupt_packages_fail(tmp_path, mutate, expected):
    from build123d import Box
    from agentcad.usdz import export_usdz
    from agentcad.written_mesh import validate_written_mesh
    good = tmp_path / "good.usdz"
    export_usdz(Box(3, 3, 3).wrapped, good)
    bad = tmp_path / "bad.usdz"
    if callable(mutate):
        bad.write_bytes(mutate(good.read_bytes()))
    else:
        method = zipfile.ZIP_DEFLATED if mutate == "deflate" else zipfile.ZIP_STORED
        with zipfile.ZipFile(good) as src, zipfile.ZipFile(bad, "w", method) as dst:
            for info in src.infolist():
                dst.writestr(info.filename, src.read(info))
    report = validate_written_mesh(bad)
    assert report["is_valid"] is False, report
    assert report["first_failure"] == "file_parse"
    assert expected in report["message"]


@needs_usd
def test_usdz_timeout_is_unknown(tmp_path):
    from agentcad.written_mesh import validate_written_mesh
    path = _aligned_zip(tmp_path / "m.usdz", [("model.usda", TETRA.encode())])
    report = validate_written_mesh(path, timeout_s=0.000001)
    assert report["is_valid"] is None and report["status"] == "timeout"
    assert path.exists()


@needs_usd
def test_checker_without_openusd_is_unknown(tmp_path, monkeypatch):
    from agentcad.written_mesh import _validate
    path = _aligned_zip(tmp_path / "m.usdz", [("model.usda", TETRA.encode())])
    _no_pxr(monkeypatch)
    report = _validate(path, lambda _: None)
    assert report["is_valid"] is None and report["status"] == "error"
    assert "agentcad[usdz]" in report["message"]


def test_help_docs_and_mcp_name_the_optional_extra(runner):
    from agentcad.mcp import server
    install = 'pip install "agentcad[usdz]"'
    for args in (["export", "--help"], ["run", "--help"], ["--help"]):
        output = " ".join(runner.invoke(cli, args).output.split())
        assert "usdz" in output and install in output, args

    def docs(section):
        return " ".join(json.loads(runner.invoke(cli, ["docs", section]).stdout)["content"].split())
    assert install in docs("export") and "optional_dependency_missing" in docs("export")
    assert "stl, glb, obj, usdz" in docs("schema")
    for tool in (server.export, server.run):
        assert install in " ".join((getattr(tool, "__doc__", "") or "").split()), tool
