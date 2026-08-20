"""Regression coverage for independently transformed imported geometry."""

from pathlib import Path

import pytest
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from agentcad.helpers import rotate, safe_intersection
from agentcad.step_io import load_cad_shape


FIXTURES = Path(__file__).parent / "fixtures" / "comparison"


def _volume(shape):
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def test_rotated_imported_pattern_is_independent_and_boolean_safe():
    """A seven-fold imported rotor is unchanged by one-pitch rotation.

    This is the realistic form of the shared-topology failure: transform a
    repeated feature copied from STEP, then compare it with its source. The
    overlap must be the full rotor, not a plausible-looking handful of slivers.
    """
    source = load_cad_shape(FIXTURES / "covered_rotor_7.step")
    source_volume = _volume(source)

    rotated = rotate(source, "Z", 360 / 7)

    assert not source.IsPartner(rotated)
    assert _volume(source) == pytest.approx(source_volume)
    assert _volume(rotated) == pytest.approx(source_volume)

    result = safe_intersection(source, rotated)
    assert BRepCheck_Analyzer(result).IsValid()
    assert _volume(result) == pytest.approx(source_volume, abs=0.01)
