"""Assembly intake evidence, using only the default build123d/OCP stack."""

import math
from pathlib import Path

import pytest

from agentcad.native_io import silence_native_stdout
from agentcad.step_assembly import read_step_assembly
from agentcad.step_io import load_cad_shape, write_step_shape
from agentcad.topo_ids import face_entries, edge_entries


def _nested_step(path, *, coincident=False, tilted=False):
    """One reusable asymmetric bracket, twice under a rotated parent."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS_Compound
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    document = TDocStd_Document(TCollection_ExtendedString("XCAF"))
    tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())

    def named(label, name):
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name, True))
        return label

    def assembly(name):
        compound = TopoDS_Compound()
        BRep_Builder().MakeCompound(compound)
        return named(tool.AddShape(compound, True), name)

    bracket = named(
        tool.AddShape(BRepPrimAPI_MakeBox(10, 20, 5).Shape(), False),
        "Bracket / pièce fournisseur",
    )
    module = assembly("Rotated module")
    named(tool.AddComponent(module, bracket, TopLoc_Location()), "left instance")
    translation = gp_Trsf()
    if tilted:
        translation.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)), math.pi / 2)
    translation.SetTranslationPart(gp_Vec(0 if coincident else 40, 0, 0))
    right_location = TopLoc_Location() if coincident else TopLoc_Location(translation)
    named(tool.AddComponent(module, bracket, right_location), "right instance")
    root = assembly("Supplier root")
    placement = gp_Trsf()
    placement.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.pi / 2)
    placement.SetTranslationPart(gp_Vec(100, 50, 0))
    named(tool.AddComponent(root, module, TopLoc_Location(placement)), "module instance")
    tool.UpdateAssemblies()
    with silence_native_stdout():
        writer = STEPCAFControl_Writer()
        writer.SetNameMode(True)
        assert writer.Transfer(document, STEPControl_AsIs)
        assert writer.Write(str(path)) == IFSelect_RetDone
    return path


def _leaves(document):
    return [o for o in document.occurrences if o.mapping_status != "assembly"]


def test_repeated_definition_keeps_distinct_nested_occurrences(tmp_path):
    document = read_step_assembly(_nested_step(tmp_path / "assembly.step"))
    definitions = {d.source_id: d for d in document.definitions}
    parts = [d for d in document.definitions if d.kind == "part"]
    leaves = _leaves(document)
    assert len(parts) == 1
    assert parts[0].name == "Bracket / pièce fournisseur"
    assert parts[0].name_status == "document_label"
    assert document.hierarchy_status == "assembly_structure"
    assert len(leaves) == 2
    assert leaves[0].definition_id == leaves[1].definition_id == parts[0].source_id
    assert leaves[0].source_id != leaves[1].source_id
    assert leaves[0].parent_id == leaves[1].parent_id
    parents = {o.source_id: o for o in document.occurrences}
    parent = parents[leaves[0].parent_id]
    assert definitions[parent.definition_id].name == "Rotated module"
    assert definitions[parents[parent.parent_id].definition_id].name == "Supplier root"
    for leaf, y in zip(leaves, (50, 90)):
        assert leaf.mapping_status == "source_exact"
        assert [r[3] for r in leaf.placement] == pytest.approx([100, y, 0])
        for row, expected in zip(leaf.placement, [(0, -1, 0), (1, 0, 0), (0, 0, 1)]):
            assert row[:3] == pytest.approx(expected)
        assert len(leaf.solid_ids) == 1
        assert len(leaf.face_ids) == 6
        assert len(leaf.edge_ids) == 12
    assert not set(leaves[0].face_ids) & set(leaves[1].face_ids)
    assert not set(leaves[0].edge_ids) & set(leaves[1].edge_ids)


def test_source_members_match_inspection_then_normalized_reload_and_edit(tmp_path):
    from build123d import Part
    from agentcad.runners.build123d import _fillet_edges, _load_step, _pick_face, _pick_edge

    source = _nested_step(tmp_path / "assembly.step")
    document = read_step_assembly(source)
    normalized = tmp_path / "output.step"
    write_step_shape(document.shape, normalized)
    reloaded = load_cad_shape(normalized)
    base = _load_step(str(normalized))
    # This fixture proves correspondence, not a blanket guarantee that source
    # traversal IDs survive arbitrary normalization. The durable manifest must
    # ultimately be issued from normalized geometry.
    assert face_entries(document.shape) == face_entries(reloaded)
    assert face_entries(reloaded) == face_entries(base.wrapped)
    assert edge_entries(reloaded) == edge_entries(base.wrapped)
    leaves = _leaves(document)
    faces = {f["id"]: f for f in face_entries(reloaded)}
    for leaf, y in zip(leaves, (50, 90)):
        for face_id in leaf.face_ids:
            selected = _pick_face(base, face_id)
            assert 80 - 1e-6 <= selected.center().X <= 100 + 1e-6
            assert y - 1e-6 <= selected.center().Y <= y + 10 + 1e-6
            assert selected.area == pytest.approx(faces[face_id]["area"])
        for edge_id in leaf.edge_ids:
            assert y - 1e-6 <= _pick_edge(base, edge_id).center().Y <= y + 10 + 1e-6

    edited = _fillet_edges(base, [leaves[0].edge_ids[0]], 0.5)
    assert isinstance(edited, Part)
    assert edited.is_valid
    before = sorted(base.solids(), key=lambda s: s.center().Y)
    after = sorted(edited.solids(), key=lambda s: s.center().Y)
    assert len(before) == len(after) == 2
    assert after[0].volume < before[0].volume
    # Independent occupied-space checks protect the other instance, including
    # differences that equal volume or bounding boxes alone would miss.
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from agentcad.topo_ids import _solid_volume

    removed = BRepAlgoAPI_Cut(before[1].wrapped, after[1].wrapped).Shape()
    added = BRepAlgoAPI_Cut(after[1].wrapped, before[1].wrapped).Shape()
    assert _solid_volume(removed) == pytest.approx(0, abs=1e-6)
    assert _solid_volume(added) == pytest.approx(0, abs=1e-6)
    write_step_shape(edited.wrapped, tmp_path / "edited.step")
    assert not load_cad_shape(tmp_path / "edited.step").IsNull()


def test_coincident_geometry_does_not_merge_distinct_source_occurrences(tmp_path):
    document = read_step_assembly(_nested_step(tmp_path / "coincident.step", coincident=True))
    leaves = _leaves(document)
    assert len(leaves) == 2
    assert leaves[0].source_id != leaves[1].source_id
    assert leaves[0].placement == leaves[1].placement
    # This reader retains separate topology even for coincident references.
    # Equal dimensions/placement alone must not collapse the source instances.
    assert not set(leaves[0].face_ids) & set(leaves[1].face_ids)
    assert all(o.mapping_status == "source_exact" for o in leaves)


def test_child_and_parent_rotations_compose_in_assembly_order(tmp_path):
    document = read_step_assembly(_nested_step(tmp_path / "tilted.step", tilted=True))
    left, right = _leaves(document)
    assert left.definition_id == right.definition_id
    expected = [(0, 0, 1, 100), (1, 0, 0, 90), (0, 1, 0, 0)]
    for row, target in zip(right.placement, expected):
        assert row == pytest.approx(target)
    faces = {f["id"]: f for f in face_entries(document.shape)}
    for face_id in right.face_ids:
        point = faces[face_id]["centroid"]
        assert 100 - 1e-6 <= point["x"] <= 105 + 1e-6
        assert 90 - 1e-6 <= point["y"] <= 100 + 1e-6
        assert 0 - 1e-6 <= point["z"] <= 20 + 1e-6


@pytest.mark.parametrize("name", ["cadgenbench_101_agentcad.step", "pump_manifold.step"])
def test_real_import_geometry_and_numeric_selectors_agree(name):
    from agentcad.runners.build123d import _load_step

    path = Path(__file__).parents[1] / "tests" / "fixtures" / "real_world" / name
    document = read_step_assembly(path)
    existing = load_cad_shape(path)
    assert face_entries(document.shape) == face_entries(existing)
    assert edge_entries(document.shape) == edge_entries(existing)
    assert face_entries(existing) == face_entries(_load_step(str(path)).wrapped)
    assert _leaves(document)
    assert all(o.mapping_status == "source_exact" for o in _leaves(document))


def test_multiple_free_roots_are_all_retained(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.gp import gp_Pnt

    path = tmp_path / "roots.step"
    with silence_native_stdout():
        writer = STEPControl_Writer()
        writer.Transfer(BRepPrimAPI_MakeBox(1, 2, 3).Shape(), STEPControl_AsIs)
        writer.Transfer(BRepPrimAPI_MakeBox(gp_Pnt(10, 0, 0), 4, 5, 6).Shape(), STEPControl_AsIs)
        assert writer.Write(str(path)) == IFSelect_RetDone
    document = read_step_assembly(path)
    leaves = _leaves(document)
    assert len(leaves) == 2
    assert all(o.parent_id is None for o in leaves)
    assert len({i for o in leaves for i in o.solid_ids}) == 2


@pytest.mark.parametrize("contents", ["not a STEP file", "ISO-10303-21;\nEND-ISO-10303-21;"])
def test_malformed_input_names_the_file(tmp_path, contents):
    path = tmp_path / "bad.step"
    path.write_text(contents)
    with pytest.raises(ValueError, match="bad.step"):
        read_step_assembly(path)


def test_missing_input_is_actionable(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        read_step_assembly(tmp_path / "missing.step")


def test_flat_step_labels_are_not_claimed_as_supplier_hierarchy(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    path = tmp_path / "flat.step"
    write_step_shape(BRepPrimAPI_MakeBox(1, 2, 3).Shape(), path)
    document = read_step_assembly(path)
    assert document.hierarchy_status == "flat_roots"
    assert document.definitions[0].name_status == "generated_pattern"
    assert document.definitions[0].name.startswith("Open CASCADE STEP translator")


def test_source_units_are_preserved_while_geometry_uses_millimeters(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from agentcad.topo_ids import solid_entries

    path = tmp_path / "meters.step"
    write_step_shape(BRepPrimAPI_MakeBox(1, 2, 3).Shape(), path)
    text = path.read_text()
    assert "SI_UNIT(.MILLI.,.METRE.)" in text
    # Change the fixture's declared unit while retaining its coordinates:
    # a 1 x 2 x 3 metre box must load as 1000 x 2000 x 3000 millimetres.
    path.write_text(text.replace("SI_UNIT(.MILLI.,.METRE.)", "SI_UNIT($,.METRE.)"))
    document = read_step_assembly(path)
    assert document.source_length_units == ("metre",)
    assert document.length_unit == "mm"
    assert solid_entries(document.shape)[0]["volume"] == pytest.approx(1000 * 2000 * 3000)


@pytest.mark.parametrize("ambient_unit", ["MM", "M", "INCH"])
def test_geometry_and_nested_placements_ignore_ambient_target_units(tmp_path, ambient_unit):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.Interface import Interface_Static
    from OCP.STEPCAFControl import STEPCAFControl_Controller
    from agentcad.topo_ids import solid_entries

    STEPCAFControl_Controller.Init_s()
    original = Interface_Static.CVal_s("xstep.cascade.unit")
    try:
        # Create known-mm inputs before changing the reader's ambient setting.
        assert Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")
        box_path = tmp_path / "millimeters.step"
        write_step_shape(BRepPrimAPI_MakeBox(1, 2, 3).Shape(), box_path)
        assembly_path = _nested_step(tmp_path / "nested.step")
        assert Interface_Static.SetCVal_s("xstep.cascade.unit", ambient_unit)

        box = read_step_assembly(box_path)
        assert box.length_unit == "mm"
        assert box.source_length_units == ("millimetre",)
        box_bounds = solid_entries(box.shape)[0]["bbox"]
        for axis, expected in zip("xyz", ([0, 1], [0, 2], [0, 3])):
            assert box_bounds[axis] == pytest.approx(expected)

        assembly = read_step_assembly(assembly_path)
        assert assembly.length_unit == "mm"
        leaves = _leaves(assembly)
        assert len(leaves) == 2
        solids = {s["id"]: s for s in solid_entries(assembly.shape)}
        for leaf, expected_y in zip(leaves, (50, 90)):
            assert [row[3] for row in leaf.placement] == pytest.approx([100, expected_y, 0])
            bounds = solids[leaf.solid_ids[0]]["bbox"]
            for axis, expected in zip("xyz", ([80, 100], [expected_y, expected_y + 10], [0, 5])):
                assert bounds[axis] == pytest.approx(expected)

        # The reader must not reconfigure other CAD code in this process.
        assert Interface_Static.CVal_s("xstep.cascade.unit") == ambient_unit
    finally:
        assert Interface_Static.SetCVal_s("xstep.cascade.unit", original)
