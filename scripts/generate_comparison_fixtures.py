#!/usr/bin/env python3
"""Generate the small public comparison fixtures used by Milestone 3.

The original 7/5-blade impeller pair is large and has no recorded public
redistribution license.  These fixtures are intentionally synthetic, small,
and generated from source so they can safely live in the public repository.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import cadquery as cq
from cadquery import exporters


FIXED_STEP_TIMESTAMP = "2000-01-01T00:00:00"


def _box_bore_pair():
    reference = cq.Workplane("XY").box(40, 40, 20)
    bore = cq.Workplane("XY").circle(6).extrude(20, both=True)
    return reference.val(), reference.cut(bore).val()


def _coincident_torus_pair():
    """A curved control whose surviving torus surface is exactly coincident."""
    reference = cq.Solid.makeTorus(20, 5)
    cutter = (
        cq.Workplane("XY")
        .box(12, 20, 12)
        .translate((20, 0, 0))
        .val()
    )
    return reference, reference.cut(cutter)


def _overlapping_compound_pair():
    """A single solid vs two compound members with a real overlap."""
    reference = cq.Workplane("XY").box(20, 20, 20).val()
    boss = (
        cq.Workplane("XY")
        .box(6, 6, 6)
        .translate((0, 0, 11))
        .val()
    )
    candidate = cq.Compound.makeCompound([reference, boss])
    return reference, candidate


def _covered_rotor(blade_count: int):
    """Make blades below a plate so the meaningful change is hidden from top."""
    plate = cq.Workplane("XY").circle(30).extrude(4).translate((0, 0, 4))
    hub = cq.Workplane("XY").circle(8).extrude(16).translate((0, 0, -8))
    result = plate.union(hub)
    seed = cq.Workplane("XY").box(22, 5, 8).translate((18, 0, 0))
    for index in range(blade_count):
        blade = seed.rotate(
            (0, 0, 0),
            (0, 0, 1),
            index * 360 / blade_count,
        )
        result = result.union(blade)
    return result.val()


def comparison_fixture_shapes():
    box, bored_box = _box_bore_pair()
    torus, cut_torus = _coincident_torus_pair()
    solid, overlapping_compound = _overlapping_compound_pair()
    return {
        "box.step": box,
        "bored_box.step": bored_box,
        "coincident_torus.step": torus,
        "coincident_torus_cut.step": cut_torus,
        "single_solid.step": solid,
        "overlapping_compound.step": overlapping_compound,
        "covered_rotor_7.step": _covered_rotor(7),
        "covered_rotor_5.step": _covered_rotor(5),
    }


def shared_location_pair():
    """Return two location variants that deliberately share one TShape.

    STEP serialization necessarily breaks this relationship, so this one
    representative case remains an in-memory fixture for the exact engine.
    """
    import math

    from OCP.TopLoc import TopLoc_Location
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    reference = cq.Workplane("XY").box(20, 10, 5).val().wrapped
    transform = gp_Trsf()
    transform.SetRotation(
        gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)),
        math.radians(12),
    )
    return reference, reference.Moved(TopLoc_Location(transform))


def _normalize_step_header(path: Path):
    content = path.read_text()
    normalized, count = re.subn(
        r"(FILE_NAME\([^,]+,)'[^']*'",
        rf"\1'{FIXED_STEP_TIMESTAMP}'",
        content,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"Could not normalize STEP timestamp in {path}")
    normalized = "\n".join(line.rstrip() for line in normalized.splitlines()) + "\n"
    path.write_text(normalized)


def generate(output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, shape in comparison_fixture_shapes().items():
        destination = output_dir / filename
        exporters.export(shape, str(destination))
        _normalize_step_header(destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            Path(__file__).resolve().parents[1]
            / "tests"
            / "fixtures"
            / "comparison"
        ),
    )
    args = parser.parse_args()
    generate(args.output_dir)


if __name__ == "__main__":
    main()
