"""Integrity checks for the M71 validation fixtures.

Each case in ``catalog.json`` records the structural facts the downstream
validity gate cares about (kernel consistency, closed shells, solid count)
and the verdict the gate is expected to give. This test pins those facts on
the checked-in STEP files with the kernel directly, so Slice 1's validator
can be measured against a corpus whose ground truth does not move.

What the kernel can establish locally: ``BRepCheck`` validity, per-shell
closure, and solid count. What it cannot: the mesh-manifold layer, which is
exactly the gap the sewn-cube and bow-tie prism cases exist to expose. Those
cases are pinned as kernel-valid and shell-closed with ``expected_gate: fail``
and ``expected_layer: mesh_manifold``; the internal parity oracle confirmed
the official verdict on 2026-09-04.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
from OCP.BRepCheck import BRepCheck_Analyzer, BRepCheck_NoError, BRepCheck_Shell
from OCP.TopAbs import TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from agentcad.metrics import compute_metrics
from agentcad.step_io import load_cad_shape


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "validation"
CATALOG = json.loads((FIXTURE_DIR / "catalog.json").read_text())
CASES = sorted(CATALOG["cases"])


def _count(shape, kind):
    count = 0
    explorer = TopExp_Explorer(shape, kind)
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def _shell_closure(shape):
    closed = []
    explorer = TopExp_Explorer(shape, TopAbs_SHELL)
    while explorer.More():
        shell = TopoDS.Shell_s(explorer.Current())
        closed.append(BRepCheck_Shell(shell).Closed() == BRepCheck_NoError)
        explorer.Next()
    return closed


def _load(case):
    return load_cad_shape(FIXTURE_DIR / CATALOG["cases"][case]["file"])


def _fixture_files(root: Path):
    return sorted(p.name for p in root.iterdir() if p.suffix in (".step", ".brep"))


def test_catalog_lists_every_checked_in_step_and_nothing_else():
    checked_in = _fixture_files(FIXTURE_DIR)
    cataloged = sorted(entry["file"] for entry in CATALOG["cases"].values())
    assert checked_in == cataloged


def test_every_case_declares_a_gate_expectation():
    for name, entry in CATALOG["cases"].items():
        assert entry["expected_gate"] in ("pass", "fail"), name
        if entry["expected_gate"] == "fail":
            assert entry["expected_layer"] in (
                "brep_check", "shell_closure", "mesh_manifold",
            ), name
        else:
            assert entry.get("expected_layer") is None, name


@pytest.mark.parametrize("case", CASES)
def test_kernel_facts_match_catalog(case):
    entry = CATALOG["cases"][case]
    shape = _load(case)

    assert BRepCheck_Analyzer(shape).IsValid() is entry["brep_check_valid"], case
    assert _count(shape, TopAbs_SOLID) == entry["solid_count"], case
    assert _count(shape, TopAbs_FACE) == entry["face_count"], case
    assert _shell_closure(shape) == entry["shells_closed"], case


@pytest.mark.parametrize("case", CASES)
def test_catalog_expectation_is_consistent_with_kernel_facts(case):
    """The kernel-visible layers must agree with the declared failing layer.

    A case that fails at brep_check must be kernel-invalid. A case that fails
    at shell_closure must be kernel-valid with an open shell. A case that
    fails at mesh_manifold must pass both earlier layers, which is what makes
    it a gap the current product cannot see.
    """
    entry = CATALOG["cases"][case]
    layer = entry.get("expected_layer")
    if layer == "brep_check":
        assert entry["brep_check_valid"] is False
    elif layer == "shell_closure":
        assert entry["brep_check_valid"] is True
        assert False in entry["shells_closed"]
    elif layer == "mesh_manifold":
        assert entry["brep_check_valid"] is True
        assert all(entry["shells_closed"])
    else:
        assert entry["brep_check_valid"] is True
        assert all(entry["shells_closed"])


@pytest.mark.parametrize("case", CASES)
def test_current_kernel_only_is_valid_is_recorded(case):
    """Pin what today's ``is_valid`` says, so the Slice 1 flip is visible.

    ``current_is_valid`` documents the pre-M71 verdict. When Slice 1 redefines
    ``is_valid`` as the full verdict this assertion moves to the new contract
    and the catalog field is renamed; until then it must not drift silently.
    """
    entry = CATALOG["cases"][case]
    metrics = compute_metrics(_load(case))
    assert metrics["is_valid"] is entry["current_is_valid"], case


def test_gap_cases_pass_today_and_are_expected_to_fail_the_gate():
    """The whole point of the corpus: these pass AgentCAD and fail downstream."""
    gap = sorted(
        name for name, entry in CATALOG["cases"].items()
        if entry["current_is_valid"] and entry["expected_gate"] == "fail"
    )
    assert gap == [
        "loose_face_compound",
        "nonmanifold_edge_prism",
        "nonmanifold_sewn_cubes",
        "open_shell",
    ]


def test_over_strictness_guards_are_expected_to_pass():
    guards = sorted(
        name for name, entry in CATALOG["cases"].items()
        if entry["family"] == "over_strictness_guard"
    )
    assert guards == ["touching_edge_solids", "touching_vertex_solids"]
    assert all(CATALOG["cases"][g]["expected_gate"] == "pass" for g in guards)


def test_checked_in_fixtures_stay_small_enough_for_the_public_repo():
    for path in FIXTURE_DIR.iterdir():
        assert path.stat().st_size < 200_000, path.name


def test_checked_in_steps_match_the_deterministic_generator(tmp_path):
    script = Path(__file__).parents[1] / "scripts" / "generate_validation_fixtures.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    checked_in = _fixture_files(FIXTURE_DIR)
    generated = _fixture_files(tmp_path)
    assert generated == checked_in
    for filename in checked_in:
        assert (tmp_path / filename).read_bytes() == (
            FIXTURE_DIR / filename
        ).read_bytes(), filename
