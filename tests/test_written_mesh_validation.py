"""The verdict must describe the bytes delivered, without repairing the mesh."""

import json
from pathlib import Path

import pytest

from agentcad.cli import cli
from agentcad.step_io import load_cad_shape

FIXTURES = Path(__file__).parent / "fixtures" / "validation"


def independent_meshes(path):
    """Use trimesh's readers and topology checks, without its repair pipeline.

    Assemble primitive siblings ourselves: trimesh 5.1 merge_primitives raises
    KeyError('visual') on valid material-free GLBs. Keep scene instances apart.
    """
    import numpy as np
    import trimesh
    scene = trimesh.load(path, force='scene', process=False, merge_primitives=False)
    groups = {}
    for node in scene.graph.nodes_geometry:
        matrix, name = scene.graph[node]
        mesh = scene.geometry[name].copy()
        mesh.apply_transform(matrix)
        key = scene.graph.transforms.parents[node] if mesh.metadata.get('from_gltf_primitive') else node
        groups.setdefault(key, []).append(mesh)
    results = []
    for pieces in groups.values():
        mesh = trimesh.util.concatenate(pieces)
        positions, inverse = np.unique(mesh.vertices, axis=0, return_inverse=True)
        results.append(trimesh.Trimesh(vertices=positions, faces=inverse[mesh.faces], process=False))
    return results


@pytest.mark.parametrize("fmt", ["stl", "glb", "obj"])
@pytest.mark.parametrize("case,valid", [("closed_box", True), ("open_shell", False)])
def test_export_reports_source_and_written_mesh(runner, isolated_dir, fmt, case, valid):
    import shutil
    source = isolated_dir / "part.step"
    shutil.copyfile(FIXTURES / f"{case}.step", source)
    result = runner.invoke(cli, ["export", str(source), "--format", fmt, "--no-daemon"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "success"  # written, not gated
    assert data["is_valid"] is valid
    mesh = data["mesh_validation"][fmt]
    assert mesh["is_valid"] is valid
    assert mesh["watertight"] is valid
    assert mesh["manifold"] is valid
    assert mesh["triangle_count"] == (12 if valid else 10)
    assert mesh["layers"]["file_parse"]["status"] == "pass"
    assert mesh["checked_file"] == data["outputs"][fmt]
    assert Path(mesh["checked_file"]).exists()


def test_reader_checks_modified_output_not_original_shape(tmp_path):
    from agentcad.export import export_obj
    from agentcad.written_mesh import validate_written_mesh
    path = tmp_path / "box.obj"
    export_obj(load_cad_shape(FIXTURES / "closed_box.step"), path)
    before = validate_written_mesh(path)
    assert before["is_valid"] is True
    lines = path.read_text().splitlines()
    removed = next(i for i, line in enumerate(lines) if line.startswith("f "))
    path.write_text("\n".join(lines[:removed] + lines[removed + 1:]) + "\n")
    after = validate_written_mesh(path)
    assert after["is_valid"] is False
    assert after["triangle_count"] == 11
    assert after["sha256"] != before["sha256"]
    assert after["layers"]["mesh_manifold"]["open_edge_count"] == 3


def test_malformed_file_is_not_a_pass(tmp_path):
    from agentcad.written_mesh import validate_written_mesh
    path = tmp_path / "truncated.glb"
    path.write_bytes(b"glTF")
    result = validate_written_mesh(path)
    assert result["is_valid"] is False
    assert result["first_failure"] == "file_parse"
    assert result["triangle_count"] is None


def test_timeout_is_unknown_and_keeps_written_file(tmp_path):
    from agentcad.written_mesh import validate_written_mesh
    path = tmp_path / "box.obj"
    path.write_text("v 0 0 0\n")
    result = validate_written_mesh(path, timeout_s=0.000001)
    assert result["is_valid"] is None
    assert result["status"] == "timeout"
    assert path.exists()


def test_step_comparison_reports_layer_and_count_changes():
    from agentcad.export_validation import compare_step_reports
    before = {"is_valid": True, "layers": {"brep_check": {"status": "pass"},
              "structure": {"status": "pass", "solid_count": 2}}}
    after = {"is_valid": True, "layers": {"brep_check": {"status": "pass"},
             "structure": {"status": "pass", "solid_count": 1}}}
    check = compare_step_reports(before, after)
    assert check["matches"] is False
    assert check["status"] == "fail"
    assert check["differences"] == [{"field": "solid_count", "before": 2, "after": 1}]
    assert compare_step_reports(before, before)["matches"] is True
    after['layers']['brep_check']['status'] = 'fail'
    assert {'field': 'layers.brep_check', 'before': 'pass', 'after': 'fail'} in compare_step_reports(before, after)['differences']


@pytest.mark.parametrize("fmt", ["stl", "obj", "glb"])
@pytest.mark.parametrize("case", [key for key, entry in
    json.loads((FIXTURES / 'catalog.json').read_text())['cases'].items()
    if entry['file'].endswith('.step')])
def test_independent_reader_parity(tmp_path, fmt, case):
    import agentcad.export as exporters
    from agentcad.written_mesh import validate_written_mesh
    catalog = json.loads((FIXTURES / 'catalog.json').read_text())['cases']
    source = load_cad_shape(FIXTURES / catalog[case]['file'])
    path = tmp_path / f'model.{fmt}'
    getattr(exporters, f'export_{fmt}')(source, str(path))
    report = validate_written_mesh(path)
    geometries = independent_meshes(path)
    assert geometries
    assert report['triangle_count'] == sum(len(mesh.faces) for mesh in geometries)
    assert report['watertight'] == all(mesh.is_watertight for mesh in geometries), report
    assert report['layers']['mesh_manifold']['winding_consistent'] == all(
        mesh.is_winding_consistent for mesh in geometries), report
    if case == 'closed_box':
        assert report['is_valid'] is True
    if case == 'open_shell':
        assert report['is_valid'] is False
    if case == 'loose_face_compound':
        assert report['triangle_count'] == 14  # cube plus loose rectangular face
        assert report['is_valid'] is False  # do not discard the face to get a pass


@pytest.mark.parametrize('fmt', ['stl', 'glb', 'obj'])
def test_real_manifold_written_mesh(tmp_path, real_world_step, fmt):
    import agentcad.export as exporters
    from agentcad.written_mesh import validate_written_mesh
    path = tmp_path / f'manifold.{fmt}'
    getattr(exporters, f'export_{fmt}')(load_cad_shape(real_world_step), str(path))
    report = validate_written_mesh(path)
    meshes = independent_meshes(path)
    assert report['triangle_count'] == sum(len(mesh.faces) for mesh in meshes)
    assert report['watertight'] == all(mesh.is_watertight for mesh in meshes)
    assert report['is_valid'] is True, report


@pytest.mark.parametrize('runtime,source', [
    ('build123d', 'plate=Box(40,20,4)-Cylinder(3,10)\n'
     'show_object(Compound(children=[plate, Pos(35,0,0)*Box(4,4,8)]), name="bracket")'),
    ('cadquery', 'plate=cq.Workplane("XY").box(40,20,4).faces(">Z").workplane().hole(6)\n'
     'cap=cq.Workplane("XY").box(4,4,8).translate((35,0,0))\n'
     'show_object(cq.Compound.makeCompound([plate.val(),cap.val()]), name="bracket")'),
])
def test_run_exports_persist_checked_meshes_and_step_comparison(runner, isolated_dir, runtime, source):
    if runtime == 'cadquery':
        pytest.importorskip('cadquery')
    assert runner.invoke(cli, ['init', '--runtime', runtime]).exit_code == 0
    (isolated_dir / 'part.py').write_text(source)
    result = runner.invoke(cli, ['run', 'part.py', '--label', 'bracket', '--export', 'stl,glb,obj',
                                 '--no-preview', '--no-view', '--no-diff', '--no-daemon'])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    comparison = data['validation']['step_round_trip']
    assert comparison['matches'] is True, comparison
    assert comparison['before']['solid_count'] == comparison['after']['solid_count'] == 2
    assert set(data['mesh_validation']) == {'stl', 'glb', 'obj'}
    assert all(m['is_valid'] is True for m in data['mesh_validation'].values()), data['mesh_validation']
    meta = json.loads((isolated_dir / 'v1_bracket' / 'meta.json').read_text())
    assert meta['mesh_validation'] == data['mesh_validation']
    assert meta['validation']['step_round_trip'] == comparison


def test_mesh_topology_defects_are_not_repaired():
    from agentcad.written_mesh import _check
    points = [(0,0,0), (1,0,0), (0,1,0), (0,0,1)]
    tetra = [(0,2,1), (0,1,3), (1,2,3), (2,0,3)]
    assert _check([(points, tetra)])['manifold'] is True
    reversed_face = [(0,1,2), *tetra[1:]]
    assert _check([(points, reversed_face)])['inconsistent_winding_edge_count'] == 3
    duplicate = _check([(points, tetra + [tetra[0]])])
    assert duplicate['triangle_count'] == 5 and duplicate['duplicate_triangle_count'] == 1
    assert duplicate['manifold'] is False
    degenerate = _check([(points, tetra + [(0,0,1)])])
    assert degenerate['degenerate_triangle_count'] == 1
    # Two closed surfaces with just one shared point: closed edges, pinched vertex.
    more = points + [(-1,0,0), (0,-1,0), (0,0,-1)]
    other = [(0,5,4), (0,4,6), (4,5,6), (5,0,6)]
    pinch = _check([(more, tetra + other)])
    assert pinch['watertight'] is True and pinch['manifold'] is False
    assert pinch['pinch_vertex'] == [0,0,0]


def test_unknown_step_report_does_not_claim_agreement():
    from agentcad.export_validation import compare_step_reports
    report = {'is_valid': None, 'layers': {'mesh_manifold': {'status': 'timeout'},
              'structure': {'status': 'pass', 'solid_count': 1}}}
    assert compare_step_reports(report, report)['matches'] is None


@pytest.mark.parametrize('case', ['touching_edge_solids', 'touching_vertex_solids'])
def test_source_cad_pass_does_not_certify_identity_losing_meshes(runner, isolated_dir, case):
    import shutil
    source = isolated_dir / 'touch.step'
    shutil.copyfile(FIXTURES / f'{case}.step', source)
    result = runner.invoke(cli, ['export', str(source), '--format', 'stl,glb,obj', '--no-daemon'])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data['is_valid'] is True
    assert data['mesh_validation']['glb']['is_valid'] is True
    assert data['mesh_validation']['obj']['is_valid'] is False
    assert data['mesh_validation']['stl']['is_valid'] is False
    assert len(data['warnings']) == 2


def test_run_keeps_successful_step_when_written_mesh_is_bad(runner, isolated_dir, monkeypatch):
    import agentcad.export as exporters
    def broken_obj(shape, path):
        Path(path).write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
    monkeypatch.setattr(exporters, 'export_obj', broken_obj)
    runner.invoke(cli, ['init'])
    (isolated_dir / 'part.py').write_text('show_object(Box(20,20,4)-Cylinder(3,10))')
    result = runner.invoke(cli, ['run', 'part.py', '--label', 'plate', '--export', 'obj',
                                 '--no-preview', '--no-view', '--no-diff', '--no-daemon'])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data['validation']['is_valid'] is True
    assert data['mesh_validation']['obj']['is_valid'] is False
    assert any('OBJ was written' in warning for warning in data['warnings'])
    assert Path(data['outputs']['step']).exists()
    assert Path(data['outputs']['obj']).exists()
    assert json.loads((isolated_dir / 'agentcad.json').read_text())['current']


def test_parse_checkpoint_survives_timeout(tmp_path, monkeypatch):
    import subprocess
    import agentcad.written_mesh as module
    path = tmp_path / 'mesh.obj'
    path.write_text('v 0 0 0\n')
    def timeout(argv, **kwargs):
        partial = module._initial(path)
        partial.update(triangle_count=123, sha256='retained-hash')
        partial['layers']['file_parse'] = {'status': 'pass'}
        Path(argv[-1]).write_text(json.dumps(partial))
        raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
    monkeypatch.setattr(module.subprocess, 'run', timeout)
    report = module.validate_written_mesh(path)
    assert report['layers']['file_parse']['status'] == 'pass'
    assert report['layers']['mesh_manifold']['status'] == 'timeout'
    assert report['undetermined_layer'] == 'mesh_manifold'
    assert report['triangle_count'] == 123 and report['sha256'] == 'retained-hash'
    assert report['is_valid'] is None


def test_internal_worker_failure_is_unknown(tmp_path, monkeypatch):
    import agentcad.written_mesh as module
    path = tmp_path / 'mesh.obj'
    path.write_text('v 0 0 0\n')
    def fail(meshes):
        raise AssertionError('injected implementation failure')
    monkeypatch.setattr(module, '_check', fail)
    report = module._validate(path, lambda report: None)
    assert report['is_valid'] is None and report['status'] == 'error'


def test_binary_stl_with_solid_header_and_invalid_indices(tmp_path):
    import struct
    from agentcad.written_mesh import validate_written_mesh
    path = tmp_path / 'binary.stl'
    path.write_bytes(b'solid'.ljust(80, b' ') + struct.pack('<I12fH', 1,
                     0,0,1, 0,0,0, 1,0,0, 0,1,0, 0))
    report = validate_written_mesh(path)
    assert report['triangle_count'] == 1 and report['watertight'] is False
    bad = tmp_path / 'bad.obj'
    bad.write_text('v 0 0 0\nf -1 2 3\n')
    assert validate_written_mesh(bad)['is_valid'] is False
    bad.write_text('v nan 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
    assert validate_written_mesh(bad)['is_valid'] is False


def rewrite_glb(path, edit):
    import struct
    data = path.read_bytes()
    length = struct.unpack_from('<I', data, 12)[0]
    doc = json.loads(data[20:20+length])
    edit(doc)
    encoded = json.dumps(doc).encode()
    encoded += b' ' * (-len(encoded) % 4)
    chunks = struct.pack('<I4s', len(encoded), b'JSON') + encoded + data[20+length:]
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, len(chunks)+12) + chunks)


def test_glb_scene_instances_transforms_and_accessor_bounds(tmp_path):
    from agentcad.export import export_glb
    from agentcad.written_mesh import validate_written_mesh, _glb
    path = tmp_path / 'instances.glb'
    export_glb(load_cad_shape(FIXTURES / 'closed_box.step'), str(path))
    def duplicate(doc):
        doc['nodes'][0]['translation'] = [40,0,0]
        doc['nodes'].append({'mesh': doc['nodes'][0]['mesh'], 'scale': [-1,1,1]})
        doc['scenes'][0]['nodes'].append(len(doc['nodes'])-1)
    rewrite_glb(path, duplicate)
    report = validate_written_mesh(path)
    assert report['is_valid'] is True and report['triangle_count'] == 24
    assert report['layers']['mesh_manifold']['component_count'] == 2
    parsed = _glb(path.read_bytes())
    assert min(point[0] for point in parsed[0][0]) == 30
    assert min(point[0] for point in parsed[1][0]) == -10
    rewrite_glb(path, lambda doc: doc['accessors'][0].update(byteOffset=10**9))
    assert validate_written_mesh(path)['first_failure'] == 'file_parse'


def test_glb_external_buffers_are_not_read(tmp_path):
    from agentcad.export import export_glb
    from agentcad.written_mesh import validate_written_mesh
    path = tmp_path / 'external.glb'
    export_glb(load_cad_shape(FIXTURES / 'closed_box.step'), str(path))
    rewrite_glb(path, lambda doc: doc['buffers'][0].update(uri='https://example.invalid/mesh.bin'))
    report = validate_written_mesh(path)
    assert report['is_valid'] is None and report['status'] == 'error'
    assert 'external files are not read' in report['message']


def test_mcp_export_returns_written_validation(isolated_dir):
    from agentcad.mcp.server import export
    import shutil
    source = isolated_dir / 'box.step'
    shutil.copyfile(FIXTURES / 'closed_box.step', source)
    result = export(str(source), 'obj', str(isolated_dir))
    assert result['is_valid'] is True
    assert result['mesh_validation']['obj']['is_valid'] is True


def test_step_round_trip_on_real_imported_shape(real_world_step, tmp_path):
    from agentcad.validation import validate_shape
    from agentcad.step_io import write_step_shape
    from agentcad.export_validation import compare_step_reports
    before_shape = load_cad_shape(real_world_step)
    before = validate_shape(before_shape)
    path = tmp_path / 'roundtrip.step'
    write_step_shape(before_shape, path)
    after = validate_shape(load_cad_shape(path))
    result = compare_step_reports(before, after)
    assert result['matches'] is True, result


def test_export_writer_failure_keeps_prior_outputs_and_checks(runner, isolated_dir, monkeypatch):
    import agentcad.export as exporters
    import shutil
    version = isolated_dir / 'v1_fixture'
    version.mkdir()
    source = version / 'output.step'
    shutil.copyfile(FIXTURES / 'closed_box.step', source)
    (version / 'meta.json').write_text('{}')
    def fail(*args, **kwargs):
        raise RuntimeError('injected GLB writer failure')
    monkeypatch.setattr(exporters, 'export_glb', fail)
    result = runner.invoke(cli, ['export', str(source), '--format', 'obj,glb', '--no-daemon'])
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data['status'] == 'error' and data['failed_format'] == 'glb'
    assert set(data['outputs']) == {'obj'}
    assert data['mesh_validation']['obj']['is_valid'] is True
    meta = json.loads((version / 'meta.json').read_text())
    assert meta['mesh_validation'] == data['mesh_validation']
    assert meta['outputs']['obj']


def test_export_docs_distinguish_write_success_from_mesh_validity(runner):
    result = runner.invoke(cli, ['docs', 'export'])
    assert result.exit_code == 0
    assert 'success means files were written' in result.output
    assert 'mesh_validation' in result.output
    assert '30-second' in result.output and 'unknown (null)' in result.output


def test_step_round_trip_detects_a_body_lost_during_export(tmp_path):
    from agentcad.validation import validate_shape
    from agentcad.step_io import write_step_shape
    from agentcad.export_validation import compare_step_reports
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    source = load_cad_shape(FIXTURES / 'disjoint_solids.step')
    before = validate_shape(source)
    path = tmp_path / 'lost-body.step'
    # Simulate a writer retaining only the first member of a compound.
    write_step_shape(TopExp_Explorer(source, TopAbs_SOLID).Current(), path)
    after = validate_shape(load_cad_shape(path))
    assert before['is_valid'] is after['is_valid'] is True
    report = compare_step_reports(before, after)
    assert report['matches'] is False
    assert {'field': 'solid_count', 'before': 2, 'after': 1} in report['differences']
