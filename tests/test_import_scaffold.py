"""`agentcad import` writes an `edit.py` scaffold (Phase 2)."""
import json
from pathlib import Path

import cadquery as cq
from cadquery import exporters

from agentcad.cli import cli


def _bracket_step(directory: Path, name: str = "bracket.step") -> Path:
    plate = cq.Workplane("XY").box(40, 30, 5)
    hole = cq.Workplane("XY").circle(5).extrude(10).translate((10, 0, 0))
    part = plate.cut(hole).edges(">Z").fillet(1)
    path = directory / name
    exporters.export(part, str(path))
    return path


class TestImportScaffold:
    def test_import_writes_edit_py_scaffold(self, runner, isolated_dir):
        """First-time import in a fresh project writes edit.py templated
        for the just-imported version."""
        runner.invoke(cli, ["init"])
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["import", str(step)])

        scaffold = isolated_dir / "edit.py"
        assert scaffold.exists(), "edit.py scaffold not written"
        content = scaffold.read_text()
        # Templated to the imported version's path
        assert "v1_bracket/output.step" in content
        # Uses load_step (the Phase 2 helper), not raw cadquery.importers
        assert "load_step(" in content
        # build123d-only path per design conventions
        assert "from build123d" in content
        # Has show_object so the script produces a v2 immediately
        assert "show_object" in content

    def test_import_response_includes_scaffold_path(self, runner, isolated_dir):
        runner.invoke(cli, ["init"])
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["import", str(step)])
        parsed = json.loads(result.stdout)
        assert parsed.get("scaffold") == "edit.py"

    def test_import_does_not_overwrite_existing_edit_py(self, runner, isolated_dir):
        """Don't clobber the agent's work-in-progress."""
        runner.invoke(cli, ["init"])
        existing = isolated_dir / "edit.py"
        existing.write_text("# my work in progress, do not overwrite\n")
        step = _bracket_step(isolated_dir)
        runner.invoke(cli, ["import", str(step)])
        assert existing.read_text() == "# my work in progress, do not overwrite\n"

    def test_import_response_omits_scaffold_when_already_existed(
        self, runner, isolated_dir
    ):
        """If the scaffold isn't written, the JSON shouldn't claim it was."""
        runner.invoke(cli, ["init"])
        (isolated_dir / "edit.py").write_text("# existing\n")
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["import", str(step)])
        parsed = json.loads(result.stdout)
        assert "scaffold" not in parsed

    def test_scaffold_runs_successfully_unedited(self, runner, isolated_dir):
        """A fresh agent who runs `agentcad run edit.py --output v2` without
        editing anything should get a successful v2 — useful as a sanity
        baseline before they start modifying."""
        runner.invoke(cli, ["init"])
        step = _bracket_step(isolated_dir)
        runner.invoke(cli, ["import", str(step)])
        # Run the scaffold as-is.
        result = runner.invoke(
            cli, ["run", "edit.py", "--output", "scaffold_baseline"]
        )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"


class TestImportScaffoldRuntimeAware:
    """Scaffold language must match the project's pinned runtime.

    Pre-fix: the scaffold was always build123d, even when the manifest
    pinned cadquery. The b3d edit helpers (`load_step`, `fillet_edges`)
    fail validation under cadquery, so a cq-pinned project's
    auto-generated scaffold contradicted its own runtime. 5/5 cq edit
    trials in user testing hit this; some worked around it by letting
    `from build123d import *` re-dispatch to b3d, while others manually
    rewrote to the documented cq fallback.
    """

    def test_cq_project_gets_cadquery_scaffold(self, runner, isolated_dir):
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "cadquery"])
        step = _bracket_step(isolated_dir)
        runner.invoke(cli, ["import", str(step)])
        scaffold = (isolated_dir / "edit.py").read_text()

        # Must NOT use the b3d-only helpers — they fail under cq.
        assert "from build123d" not in scaffold, (
            "cq-pinned project must not get a build123d scaffold"
        )
        assert "load_step(" not in scaffold
        assert "fillet_edges(" not in scaffold

        # Must use the documented cq fallback path from `docs editing`.
        assert "import cadquery" in scaffold
        assert "importers.importStep" in scaffold
        # Templated to the imported version's path.
        assert "v1_bracket/output.step" in scaffold
        # Has show_object so the scaffold actually runs.
        assert "show_object" in scaffold

    def test_b3d_project_gets_build123d_scaffold(self, runner, isolated_dir):
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
        step = _bracket_step(isolated_dir)
        runner.invoke(cli, ["import", str(step)])
        scaffold = (isolated_dir / "edit.py").read_text()
        assert "from build123d" in scaffold
        assert "load_step(" in scaffold

    def test_cq_scaffold_runs_successfully_unedited(self, runner, isolated_dir):
        """The cq scaffold must produce a valid v2 without any agent edits,
        the same contract as test_scaffold_runs_successfully_unedited."""
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "cadquery"])
        step = _bracket_step(isolated_dir)
        runner.invoke(cli, ["import", str(step)])
        result = runner.invoke(
            cli, ["run", "edit.py", "--output", "scaffold_baseline"]
        )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success", parsed
        assert parsed["runtime"] == "cadquery"
