"""Occupied material, contact, and small clearances are distinct diagnostics."""
import pytest
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Compound
from tests.test_part_connections import box, diagnose


def compound(*shapes):
    result = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


@pytest.mark.parametrize('offset,status,volume', [(5,'overlapping',500), (10,'touching',0), (10.005,'near',0), (10.05,'disconnected',0), (0,'overlapping',1000)])
def test_real_material_relation(offset, status, volume):
    _, parts, warnings = diagnose([box(0,size=10), box(offset,size=10)])
    for part in parts:
        assert part['connection']['status'] == status
        check = part['connection']['overlap_check']
        assert check['status'] == 'complete'
        assert sum(p['volume_mm3'] for p in check['overlaps']) == pytest.approx(volume)
    if volume:
        assert any('overlap' in w for w in warnings)


def test_contact_with_first_neighbor_does_not_hide_later_overlap():
    _, parts, _ = diagnose([box(0), box(-2), box(1)])
    assert parts[0]['connection']['nearest_part_id'] == 'p1'
    assert parts[0]['connection']['status'] == 'overlapping'
    assert parts[0]['connection']['overlap_check']['overlaps'][0]['part_id'] == 'p2'


def test_compound_internal_overlap_is_not_counted_twice():
    _, parts, _ = diagnose([compound(box(0), box(1)), box(0.5)])
    assert parts[0]['connection']['status'] == 'overlapping'
    assert parts[0]['connection']['overlap_check']['overlaps'][0]['volume_mm3'] == pytest.approx(8)


def test_floating_flag_does_not_hide_overlapping_material():
    _, parts, _ = diagnose([box(0), box(1)], floating=(0,1))
    assert all(p['connection']['status'] == 'overlapping' for p in parts)


def test_containment_is_overlap():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    inner = BRepPrimAPI_MakeBox(gp_Pnt(2,2,2), 1,1,1).Shape()
    _, parts, warnings = diagnose([box(0,size=10), inner])
    assert all(p['connection']['status'] == 'overlapping' for p in parts)
    assert parts[0]['connection']['overlap_check']['overlaps'][0]['volume_mm3'] == pytest.approx(1)
    assert not any('touches no other' in w for w in warnings)


def test_pair_budget_is_explicit(monkeypatch):
    import agentcad.part_overlap as overlap
    actual = overlap.bounded_overlaps
    monkeypatch.setattr(overlap, 'bounded_overlaps', lambda shapes,pairs: actual(shapes,pairs,max_pairs=1))
    _, parts, warnings = diagnose([box(0),box(2),box(1)])
    assert parts[0]['connection']['status'] == 'unavailable'
    assert parts[0]['connection']['overlap_check']['status'] == 'partial'
    assert any('incomplete overlap' in w for w in warnings)


def test_worker_timeout_never_reports_touching(monkeypatch):
    import subprocess
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0],kwargs['timeout'])
    monkeypatch.setattr(subprocess,'run',timeout)
    _, parts, _ = diagnose([box(0),box(1)])
    assert all(p['connection']['status'] == 'unavailable' for p in parts)
    assert parts[0]['connection']['overlap_check']['unavailable'][0]['reason'] == 'timeout'


def test_thin_penetration_above_volume_resolution_is_not_touching():
    _,parts,_ = diagnose([box(0), box(1.99999)])
    assert parts[0]['connection']['status'] == 'overlapping'


def test_surface_parts_do_not_establish_absence_of_solid_overlap():
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Pln, gp_Pnt, gp_Dir
    face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(0,0,1),gp_Dir(0,0,1)),0,2,0,2).Face()
    _,parts,_ = diagnose([box(0),face])
    assert all(p['connection']['status'] == 'unavailable' for p in parts)


def test_completed_pairs_survive_timeout(monkeypatch):
    import json
    import subprocess
    from pathlib import Path
    from agentcad.part_overlap import bounded_overlaps
    def timeout(argv,**kwargs):
        root=Path(argv[-1])
        (root/'results.jsonl').write_text(json.dumps({'pair':[0,1],'status':'complete','volume_mm3':4,'volume_tolerance_mm3':1e-6})+'\n{"pair":')
        raise subprocess.TimeoutExpired(argv,kwargs['timeout'])
    monkeypatch.setattr(subprocess,'run',timeout)
    result=bounded_overlaps([box(0),box(1),box(2)],[(0,1),(0,2)])
    assert result[(0,1)]['status']=='complete'
    assert result[(0,2)]['reason']=='timeout'


def test_worker_does_not_import_an_authored_package_named_agentcad(isolated_dir):
    local=isolated_dir/'agentcad'
    local.mkdir()
    (local/'__init__.py').write_text('raise RuntimeError("authored package must not be imported by diagnostic worker")')
    _,parts,_=diagnose([box(0),box(1)])
    assert all(p['connection']['status']=='overlapping' for p in parts)
