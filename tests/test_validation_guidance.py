"""Agent-visible failure evidence, intent checks, and repair workflows."""

import json
from pathlib import Path

import pytest

from agentcad.cli import cli
from agentcad.step_io import load_cad_shape
from agentcad.validation import validate_shape, _layer_structure
from agentcad.validation_guidance import structure_checks, with_structure_checks, validation_markers

FIXTURES = Path(__file__).parent / "fixtures" / "validation"
CATALOG = json.loads((FIXTURES / "catalog.json").read_text())["cases"]
QUIET = ["--no-preview", "--no-view", "--no-diff", "--no-daemon"]


def fixture(case):
    return FIXTURES / CATALOG[case]["file"]


def run_script(runner, directory, source, *, runtime="build123d", dry=False, extra=()):
    assert runner.invoke(cli, ["init", "--runtime", runtime]).exit_code == 0
    (directory / "part.py").write_text(source)
    result = runner.invoke(cli, ["run", "part.py", "--label", "part", *QUIET, *extra, *(["--dry-run"] if dry else [])])
    assert result.stdout, repr(result.exception)
    return result, json.loads(result.stdout)


def test_open_shell_evidence_matches_inspect_ids(runner):
    source = fixture("open_shell")
    shape = load_cad_shape(source)
    report = validate_shape(shape)
    result = runner.invoke(cli, ["inspect", str(source), "--ids", "--no-daemon"])
    assert result.exit_code == 0, result.output
    inventory = json.loads(result.stdout)
    edges = {e["id"]: e for e in inventory["edges"]}
    faces = {f["id"] for f in inventory["faces"]}
    for edge in report["layers"]["shell_closure"]["free_edges"]:
        assert edge["endpoints"] == edges[edge["id"]]["endpoints"]
        assert edge["face_ids"] and set(edge["face_ids"]) <= faces
    assert inventory["vertices"]
    repairs = report["repairs"]
    assert any(r["changes_intent"] for r in repairs)
    assert all(r.get("precondition") for r in repairs if not r["changes_intent"])
    assert len(validation_markers(shape, report)) == 4


def test_mesh_edge_ids_match_shared_topology():
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
    from agentcad.topo_ids import pick_edge_topods
    case = next(k for k, v in CATALOG.items() if v["expected_layer"] == "mesh_manifold"
                and "sewn" in k)
    shape = load_cad_shape(fixture(case))
    report = validate_shape(shape, evidence_limit=2)
    mesh = report["layers"]["mesh_manifold"]
    assert mesh["defect"]["kind"] == "non_manifold_edge"
    ancestry = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, ancestry)
    for defect in mesh["defects"]:
        assert defect["edge_ids"]
        assert len(defect["endpoints"]) == 2
        for edge_id in defect["edge_ids"]:
            assert ancestry.FindFromKey(pick_edge_topods(shape, edge_id)).Extent() >= 4


def test_pinch_vertex_is_identified_in_the_topology_inventory():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRepTools import BRepTools_ReShape
    from OCP.TopAbs import TopAbs_VERTEX
    from agentcad.topo_ids import _indexed_map, vertex_entries

    # Two closed bodies with one actual shared vertex, unlike two independent
    # bodies that happen to touch at identical coordinates (which must pass).
    a = BRepPrimAPI_MakeBox(10, 10, 10).Shape()
    b = BRepPrimAPI_MakeBox(gp_Pnt(10, 10, 10), 10, 10, 10).Shape()
    location = dict(x=10, y=10, z=10)
    ai = next(v["id"] for v in vertex_entries(a) if v["location"] == location)
    bi = next(v["id"] for v in vertex_entries(b) if v["location"] == location)
    reshape = BRepTools_ReShape()
    reshape.Replace(_indexed_map(b, TopAbs_VERTEX).FindKey(bi), _indexed_map(a, TopAbs_VERTEX).FindKey(ai))
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    builder.Add(compound, a)
    builder.Add(compound, reshape.Apply(b))
    report = validate_shape(compound)
    defect = report["layers"]["mesh_manifold"]["defect"]
    assert defect["kind"] == "pinch_vertex"
    vertices = {v["id"]: v["location"] for v in vertex_entries(compound)}
    assert defect["vertex_ids"]
    assert all(vertices[vid] == location for vid in defect["vertex_ids"])
    assert validation_markers(compound, report)[0]["points"] == [location]


def test_worker_preserves_mesh_evidence_ids(monkeypatch):
    import agentcad.validation as module
    case = next(k for k, v in CATALOG.items() if v["expected_layer"] == "mesh_manifold" and "sewn" in k)
    local = validate_shape(load_cad_shape(fixture(case)))
    monkeypatch.setattr(module, "MESH_INPROCESS_FACE_LIMIT", 0)
    worker = validate_shape(load_cad_shape(fixture(case)))
    assert worker["layers"]["mesh_manifold"]["worker"] == "subprocess"
    assert worker["layers"]["mesh_manifold"]["defects"] == local["layers"]["mesh_manifold"]["defects"]


@pytest.mark.parametrize("dry", [False, True])
@pytest.mark.parametrize("profile", ["deliverable", "kernel"])
def test_run_expectation_failure_never_becomes_success(runner, isolated_dir, dry, profile):
    result, data = run_script(runner, isolated_dir,
        'show_object(Compound(children=[Box(2,2,2), Pos(4,0,0)*Box(2,2,2)]), '
        'name="bracket", options={"expect_solids":1})', dry=dry,
        extra=["--validation-profile", profile])
    assert result.exit_code == 1, result.output
    assert data["status"] == "invalid_geometry"
    assert data["validation"]["first_failure"] == "structure"
    checks = data["validation"]["layers"]["structure"]["expectations"]
    assert checks[0]["expected"] == 1 and checks[0]["actual"] == 2
    assert "got 2" in data["validation"]["message"]
    manifest = json.loads((isolated_dir / "agentcad.json").read_text())
    assert not any(v["status"] == "success" for v in manifest["versions"])
    assert not manifest.get("current")
    if dry:
        assert manifest["versions"] == []
    assert data["parts"][0]["structure"]["status"] == "fail"
    assert not list(isolated_dir.glob("v*/output.step"))


def test_per_part_expectations_and_floating_override(runner, isolated_dir):
    result, data = run_script(runner, isolated_dir,
        'show_object(Box(2,2,2), id="body", options={"expect_solids":1})\n'
        'show_object(Pos(10,0,0)*Box(2,2,2), id="cap", '
        'options={"expect_solids":1, "expect_shells":1, "floating":True})', dry=True)
    assert result.exit_code == 0, result.output
    assert data["validation"]["is_valid"] is True
    assert data["validation"]["layers"]["structure"]["solid_count"] == 2
    body, cap = data["parts"]
    assert body["connection"]["status"] == "disconnected"
    assert body["connection"]["distance_mm"] == 8
    assert cap["connection"]["status"] == "floating"
    assert any("'body' touches no other part" in w for w in data["warnings"])
    assert not any("'cap' touches no other part" in w for w in data["warnings"])


def test_touching_named_parts_do_not_warn(runner, isolated_dir):
    result, data = run_script(runner, isolated_dir,
        'show_object(Box(2,2,2), name="body")\n'
        'show_object(Pos(2,0,0)*Box(2,2,2), name="cap")', dry=True)
    assert result.exit_code == 0, result.output
    assert all(p["connection"]["status"] == "touching" for p in data["parts"])
    assert not any("touches no other part" in w for w in data.get("warnings", []))


@pytest.mark.parametrize("runtime,source", [
    ("build123d", 'show_object(Box(2,2,2), options={"expect_solids":True})'),
    ("cadquery", 'show_object(cq.Workplane("XY").box(2,2,2), options={"expect_solids":True})'),
])
def test_invalid_expectation_is_actionable(runner, isolated_dir, runtime, source):
    if runtime == "cadquery":
        pytest.importorskip("cadquery")
    result, data = run_script(runner, isolated_dir, source, runtime=runtime, dry=True)
    assert result.exit_code != 0
    assert "expect_solids must be a non-negative integer" in json.dumps(data)


def test_cadquery_structure_gate(runner, isolated_dir):
    pytest.importorskip("cadquery")
    result, data = run_script(runner, isolated_dir,
        'show_object(cq.Workplane("XY").box(2,2,2), options={"expect_solids":2})',
        runtime="cadquery", dry=True)
    assert result.exit_code == 1
    assert data["validation"]["first_failure"] == "structure"


@pytest.mark.parametrize("expected,passed", [(1, True), (11, False)])
def test_structure_only_spec(runner, isolated_dir, expected, passed):
    spec = isolated_dir / "spec.json"
    spec.write_text(json.dumps({"expect_solids": expected, "expect_shells": 1}))
    result = runner.invoke(cli, ["check-spec", str(fixture("closed_box")), str(spec), "--no-daemon"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["passed"] is passed
    assert data["validation"]["is_valid"] is passed
    assert data["validity"]["is_valid"] is passed
    assert data["structure_checks"][0]["actual"] == 1


@pytest.mark.parametrize("value", [True, -1, 1.5, "1", None])
def test_invalid_structure_spec(runner, isolated_dir, value):
    spec = isolated_dir / "spec.json"
    spec.write_text(json.dumps({"expect_solids": value}))
    result = runner.invoke(cli, ["check-spec", str(fixture("closed_box")), str(spec), "--no-daemon"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "invalid_spec"


def test_geometry_failure_is_not_masked_by_counts():
    report = validate_shape(load_cad_shape(fixture("open_shell")))
    updated = with_structure_checks(report, structure_checks(report["layers"]["structure"], {"expect_solids": 1}))
    assert updated["first_failure"] == "shell_closure"
    assert updated["layers"]["structure"]["status"] == "fail"
    assert report["layers"]["structure"]["status"] == "pass"


def test_real_workflow_solids_have_volume_and_bounds(real_world_step):
    structure = _layer_structure(load_cad_shape(real_world_step))
    assert structure["solids"]
    for solid in structure["solids"]:
        assert solid["volume"] > 0
        assert all(solid["bbox"][axis][1] > solid["bbox"][axis][0] for axis in "xyz")


def test_validation_view_embeds_actual_failure_markers(runner, isolated_dir):
    import shutil
    source = isolated_dir / "open.step"
    shutil.copyfile(fixture("open_shell"), source)
    result = runner.invoke(cli, ["view", str(source), "--validation"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["mode"] == "validation" and data["is_valid"] is False
    from urllib.parse import urlparse, unquote
    html = Path(unquote(urlparse(data["url"]).path)).read_text()
    assert '"validation_markers": [{"kind": "edge"' in html
    assert 'sew_coincident_faces' in html
    assert "marker.points.map(cadPointToViewer)" in html


@pytest.mark.parametrize("case", [k for k, v in CATALOG.items() if v["expected_gate"] == "fail"])
def test_validation_render_contains_red_edge_pixels(runner, isolated_dir, case):
    import shutil
    import numpy as np
    from PIL import Image
    source = isolated_dir / fixture(case).name
    shutil.copyfile(fixture(case), source)
    result = runner.invoke(cli, ["render", str(source), "--view", "iso", "--highlight", "validation", "--no-daemon"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["is_valid"] is False and data["highlight_count"] > 0
    pixels = np.asarray(Image.open(data["renders"]["iso"]).convert("RGB")).astype(int)
    red = (pixels[:,:,0] > pixels[:,:,1] + 80) & (pixels[:,:,0] > pixels[:,:,2] + 80)
    assert red.sum() > 30


def test_kernel_invalid_brep_has_face_ids_and_view(runner, isolated_dir):
    import shutil
    source = isolated_dir / "invalid.brep"
    shutil.copyfile(fixture("bowtie_prism_invalid"), source)
    result = runner.invoke(cli, ["view", str(source), "--validation"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)["validation"]
    assert report["first_failure"] == "brep_check"
    assert {e["id"] for e in report["layers"]["brep_check"]["entities"] if e["kind"] == "face"} == {5, 6}


def test_mcp_validation_view_returns_same_evidence(isolated_dir):
    import shutil
    from agentcad.mcp.server import view
    source = isolated_dir / "open.step"
    shutil.copyfile(fixture("open_shell"), source)
    data = view(str(source), str(isolated_dir), validation=True)
    assert data["mode"] == "validation"
    assert data["validation"]["layers"]["shell_closure"]["free_edge_count"] == 4


def test_missing_part_count_is_undetermined_not_invalid():
    report = validate_shape(load_cad_shape(fixture("closed_box")))
    updated = with_structure_checks(report, structure_checks({}, {"expect_solids": 1}, part_id="part"))
    assert updated["is_valid"] is None
    assert updated["first_failure"] is None
    assert updated["undetermined_layer"] == "structure"


@pytest.mark.browser
def test_validation_browser_shows_markers_and_repairs(runner, isolated_dir):
    import os
    import shutil
    if os.environ.get("AGENTCAD_BROWSER_SMOKE") != "1":
        pytest.skip("set AGENTCAD_BROWSER_SMOKE=1")
    from playwright.sync_api import sync_playwright
    source = isolated_dir / "open.step"
    shutil.copyfile(fixture("open_shell"), source)
    result = runner.invoke(cli, ["view", str(source), "--validation"])
    assert result.exit_code == 0, result.output
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(json.loads(result.stdout)["url"])
            page.wait_for_function("window.agentcadViewer && window.agentcadViewer.debugState().ready")
            assert page.locator("#review-heading").inner_text() == "Validation"
            assert page.locator("#review-validity").inner_text() == "Invalid"
            assert page.locator("#review-measure-rows").inner_text().find("sew_coincident_faces") >= 0
            page.locator("#review-measure-rows .review-row").first.click()
            page.wait_for_function("window.agentcadViewer.debugState().validation_marker_count === 4")
            red = page.evaluate('''() => {
              const source = document.querySelector('#canvas');
              const canvas = document.createElement('canvas');
              canvas.width = source.width; canvas.height = source.height;
              const ctx = canvas.getContext('2d'); ctx.drawImage(source, 0, 0);
              const pixels = ctx.getImageData(0,0,canvas.width,canvas.height).data;
              let red = 0;
              for(let i=0;i<pixels.length;i+=4)
                if(pixels[i] > pixels[i+1]+60 && pixels[i] > pixels[i+2]+60) red++;
              return red;
            }''')
            assert red > 30
            assert not errors
            # The ordinary run viewer also surfaces advisory part separation.
            result, build = run_script(runner, isolated_dir,
                'show_object(Box(20,20,4), id="body")\n'
                'show_object(Pos(20,0,0)*Box(2,2,2), id="decoration", '
                'options={"floating":True})', extra=["--preview"])
            assert result.exit_code == 0, result.output
            page.goto((isolated_dir / build["viewer"]).as_uri())
            page.wait_for_function("window.agentcadViewer && window.agentcadViewer.debugState().ready")
            page.click("#btn-parts")
            assert "touches no other part" in page.locator('#parts-list [data-part-id="body"]').inner_text()
            assert "Intentionally floating" in page.locator('#parts-list [data-part-id="decoration"]').inner_text()
            spec = isolated_dir / 'one-body.json'
            spec.write_text('{"expect_solids":1}')
            checked = runner.invoke(cli, ['view', build['outputs']['step'], '--spec', str(spec)])
            assert checked.exit_code == 0, checked.output
            page.goto(json.loads(checked.stdout)['url'])
            page.wait_for_function("window.agentcadViewer && window.agentcadViewer.debugState().ready")
            assert 'expected 1, got 2' in page.locator('#review-spec-rows').inner_text()
            assert not errors
        finally:
            browser.close()
