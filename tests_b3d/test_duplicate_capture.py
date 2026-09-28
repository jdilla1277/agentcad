"""Issue #200: repeated node identity must not escape as anytree.TreeError."""

import json

import pytest

from agentcad.cli import cli
from agentcad.metrics import compute_metrics
from agentcad.runners import build123d as runner


DUPLICATE_OUTPUTS = [
    "show_object(part)\nshow_object(part)",
    "show_object(part, name='first')\nshow_object(part, name='second')",
    "show_object(part, id='first')\nshow_object(part, id='second')",
    "show_object(part)\nshow_object(ShapeList([part]))",
    "show_assembly([part, part])",
    "show_compound((part, part))",
    "show_assembly(ShapeList([part, part]))",
    "show_assembly(p for p in [part, part])",
    "show_object(part)\nshow_assembly([part])",
    "show_assembly([part])\nshow_object(part)",
    "show_assembly([part])\nshow_assembly([part])",
    "show_assembly([part])\nshow_object(part.parent)",
]


@pytest.mark.parametrize("output", DUPLICATE_OUTPUTS)
def test_duplicate_identity_is_a_targeted_error(output):
    result = runner.execute("part = Box(1, 2, 3)\n" + output)
    assert result.status == "execution_error"
    assert result.error_kind == "duplicate_capture"
    assert "same object" in result.exception
    assert "deepcopy" in result.exception
    assert "TreeError" not in result.exception
    assert result.traceback is None
    assert result.topo_shape is None


def test_duplicate_raw_shape_is_rejected_too():
    result = runner.execute(
        "part = Box(1, 2, 3).wrapped\nshow_object(part)\nshow_object(part)"
    )
    assert result.error_kind == "duplicate_capture"


def test_duplicate_part_aliases_through_explicit_api():
    result = runner.execute(
        "from agentcad.api import show_object\n"
        "with BuildPart() as model:\n"
        "    Box(1, 2, 3)\n"
        "part = model.part\n"
        "result = part\n"
        "show_object(part)\n"
        "show_object(result)\n"
    )
    assert result.error_kind == "duplicate_capture"
    assert result.traceback is None


@pytest.mark.parametrize("output", [
    "show_object(first, name='first')\nshow_object(second, name='second')",
    "show_assembly([first, second], name='pair')",
    "show_compound([first, second], name='pair')",
])
def test_explicit_instances_survive_step_export(output, tmp_path):
    from build123d import Compound
    from agentcad.step_io import load_cad_shape

    result = runner.execute(
        "from copy import deepcopy\n"
        "first = Box(1, 2, 3)\n"
        "second = deepcopy(first).translate((10, 0, 0))\n" + output
    )
    assert result.success, result.exception
    assert compute_metrics(result.topo_shape)["volume"] == pytest.approx(12)
    expected_names = ["first", "second"] if len(result.parts) == 2 else ["pair"]
    assert [part["name"] for part in result.parts] == expected_names
    path = tmp_path / "instances.step"
    runner.export_step(result.native_shape, str(path))
    exported = load_cad_shape(path)
    metrics = compute_metrics(exported)
    assert len(Compound(exported).solids()) == 2
    assert metrics["volume"] == pytest.approx(12)


def test_equal_geometry_with_distinct_identity_is_not_duplicate_capture():
    result = runner.execute("show_object(Box(1, 2, 3))\nshow_object(Box(1, 2, 3))")
    assert result.success, result.exception
    assert len(result.parts) == 2


def test_caught_duplicate_does_not_poison_subsequent_capture():
    result = runner.execute(
        "part = Box(1, 2, 3)\n"
        "try:\n"
        "    show_assembly([part, part])\n"
        "except ValueError:\n"
        "    pass\n"
        "show_object(part)\n"
    )
    assert result.success, result.exception
    assert compute_metrics(result.topo_shape)["volume"] == pytest.approx(6)


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("output", [DUPLICATE_OUTPUTS[0], DUPLICATE_OUTPUTS[4]])
def test_cli_duplicate_capture_is_json_with_recovery(
    runner, b3d_project, output, dry_run
):
    (b3d_project / "duplicate.py").write_text("part = Box(1, 2, 3)\n" + output)
    args = ["run", "duplicate.py", "--output", "duplicate", "--no-daemon",
            "--no-preview", "--no-view"]
    if dry_run:
        args.append("--dry-run")
    result = runner.invoke(cli, args)
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == ("error" if dry_run else "failed")
    assert payload["error_kind"] == "duplicate_capture"
    assert "deepcopy" in payload.get("error", payload.get("message", ""))
    assert "TreeError" not in result.output
    assert "Traceback" not in result.output
    assert payload["artifact_created"] is False
    assert not list(b3d_project.rglob("*.step"))
    manifest = json.loads((b3d_project / "agentcad.json").read_text())
    assert manifest.get("current") is None
    if dry_run:
        assert manifest["versions"] == []
        assert not list(b3d_project.glob("v*"))
    else:
        meta = json.loads((b3d_project / "v1_duplicate_failed/meta.json").read_text())
        assert meta["error_kind"] == "duplicate_capture"
