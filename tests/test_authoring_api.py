"""Stable explicit-import contract for AgentCAD-authored script helpers."""

import pytest

from agentcad import api
from agentcad.runners import build123d
from agentcad.validate import _AGENTCAD_API_NAMES, validate_script


def test_public_api_catalog_matches_validation_guidance():
    assert set(api.__all__) == set(_AGENTCAD_API_NAMES)


def test_explicit_import_script_captures_output():
    result = build123d.execute(
        "from agentcad.api import show_object, translate\n"
        "from build123d import Box, Compound\n"
        "moved = translate(Box(2, 3, 4), 5, 0, 0)\n"
        "show_object(Compound(moved))\n"
    )

    assert result.success
    assert result.topo_shape is not None


def test_module_qualified_show_object_is_recognized_and_captures():
    source = (
        "import agentcad.api as ac\n"
        "from build123d import Box\n"
        "ac.show_object(Box(1, 2, 3))\n"
    )

    assert build123d.validate(source) == []
    assert build123d.execute(source).success


@pytest.mark.parametrize(
    "source",
    [
        "import agentcad.api\nagentcad.api.show_object(Box(1, 2, 3))\n",
        "from agentcad import api\napi.show_object(Box(1, 2, 3))\n",
        (
            "from agentcad.api import show_object as emit\n"
            "emit(Box(1, 2, 3))\n"
        ),
    ],
)
def test_supported_api_import_forms_are_recognized(source):
    assert build123d.validate(source) == []
    assert build123d.execute(source).success


def test_unrelated_show_object_method_does_not_satisfy_capture_check():
    source = (
        "class Reporter:\n"
        "    def show_object(self, value):\n"
        "        return value\n"
        "reporter = Reporter()\n"
        "reporter.show_object(Box(1, 2, 3))\n"
    )

    errors = build123d.validate(source)

    assert len(errors) == 1
    assert errors[0]["check"] == "show_object_missing"


def test_injected_and_imported_names_are_the_same_callables():
    result = build123d.execute(
        "import agentcad.api as ac\n"
        "for helper_name in ac.__all__:\n"
        "    assert globals()[helper_name] is getattr(ac, helper_name)\n"
        "show_object(Box(1, 2, 3))\n"
    )

    assert result.success


def test_show_object_outside_run_has_actionable_error():
    with pytest.raises(RuntimeError, match="agentcad run"):
        api.show_object(object())


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "from agentcad import load_step\nshow_object(Box(1, 2, 3))\n",
            "from agentcad.api import load_step",
        ),
        (
            "from build123d import load_step\nshow_object(Box(1, 2, 3))\n",
            "from agentcad.api import load_step",
        ),
        (
            "from build123d import Vec\nshow_object(Box(1, 2, 3))\n",
            "from build123d import Vector",
        ),
        (
            "from build123d.io import load_step\nshow_object(Box(1, 2, 3))\n",
            "from agentcad.api import load_step",
        ),
        (
            "from build123d import save_step\nshow_object(Box(1, 2, 3))\n",
            "not part of the AgentCAD authoring workflow",
        ),
    ],
)
def test_known_wrong_imports_get_targeted_guidance(source, expected):
    errors = validate_script(source)

    assert len(errors) == 1
    assert errors[0]["check"] == "import_error"
    assert expected in errors[0]["message"]


def test_valid_explicit_api_import_passes_validation():
    source = (
        "from agentcad.api import load_step, safe_cut, translate, show_object\n"
        "show_object(Box(1, 2, 3))\n"
    )

    assert validate_script(source) == []
