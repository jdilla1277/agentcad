"""Focused tests for build123d execution-error recovery guidance."""

import pytest

from agentcad.commands.run import _execution_error_guidance


@pytest.mark.parametrize("runtime", ["build123d", "cadquery"])
def test_step_writer_guidance_applies_to_both_runtimes(runtime):
    guidance = _execution_error_guidance(
        "Script execution failed: name 'save_step' is not defined",
        runtime, "save_step(result, 'manual.step')\nshow_object(result)",
    )
    assert "show_object(result)" in guidance["suggestion"]
    assert "outputs.step" in guidance["suggestion"]


@pytest.mark.parametrize("message", [
    "Script execution failed: AttributeError: 'Report' object has no attribute 'write'",
    "Script execution failed: ValueError: invalid shape",
    "Script execution failed: FileNotFoundError: No such file or directory: 'input.step'",
])
def test_unrelated_errors_do_not_get_step_writer_guidance(message):
    assert _execution_error_guidance(message, "build123d", "show_object(result)") == {}


def test_method_iterability_guidance_requires_an_uncalled_part_method():
    message = "Script execution failed: TypeError: 'method' object is not iterable"

    unrelated = _execution_error_guidance(
        message,
        runtime="build123d",
        source="items = factory\nfor item in items:\n    pass\nshow_object(Box(1, 1, 1))\n",
    )
    called = _execution_error_guidance(
        message,
        runtime="build123d",
        source="solids = base.solids()\nshow_object(base)\n",
    )
    uncalled = _execution_error_guidance(
        message,
        runtime="build123d",
        source="for solid in base.solids:\n    pass\nshow_object(base)\n",
    )

    assert unrelated == {}
    assert called == {}
    assert "base.solids()" in uncalled["suggestion"]
    assert uncalled["more_at"] == "agentcad docs editing"


def test_part_guidance_is_scoped_to_build123d_runtime():
    message = (
        "Script execution failed: AttributeError: "
        "'Part' object has no attribute 'BoundingBox'"
    )

    assert _execution_error_guidance(message, "cadquery", "show_object(base)") == {}
    guidance = _execution_error_guidance(message, "build123d", "show_object(base)")
    assert "base.bounding_box()" in guidance["suggestion"]


def test_is_null_guidance_points_to_product_validity_surfaces():
    message = (
        "Script execution failed: AttributeError: "
        "'Part' object has no attribute 'IsNull'"
    )
    guidance = _execution_error_guidance(message, "build123d", "show_object(base)")

    assert "agentcad inspect" in guidance["suggestion"]
    assert "run metrics" in guidance["suggestion"]
    assert guidance["more_at"] == "agentcad docs editing"
