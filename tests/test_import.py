"""End-to-end tests for `agentcad import` — the Phase 1b front door.

Imports a STEP/BREP file as a real version in the manifest, with provenance
fields, source-file copy, preview/viewer/auto-diff, and `next_actions` per
the design conventions.
"""
import hashlib
import json
import shutil
from pathlib import Path

import cadquery as cq
import pytest
from cadquery import exporters

from agentcad import __version__
from agentcad.cli import cli


# --- fixtures ---------------------------------------------------------------

def _bracket_step(directory: Path, name: str = "bracket.step") -> Path:
    """A non-trivial part: filleted bracket with a through-hole. Realistic
    enough to mirror what an agent would actually be handed (per CLAUDE.md
    'verify with realistic inputs')."""
    plate = cq.Workplane("XY").box(40, 30, 5)
    hole = cq.Workplane("XY").circle(5).extrude(10).translate((10, 0, 0))
    part = plate.cut(hole).edges(">Z").fillet(1)
    path = directory / name
    exporters.export(part, str(path))
    return path


def _box_step(directory: Path, name: str = "box.step") -> Path:
    box = cq.Workplane("XY").box(10, 10, 10)
    path = directory / name
    exporters.export(box, str(path))
    return path


def _invalid_brep(directory: Path, name: str = "invalid.brep") -> Path:
    """Write a solid containing an open one-face shell: parseable but invalid."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS, TopoDS_Shell, TopoDS_Solid

    box = BRepPrimAPI_MakeBox(10, 10, 10).Shape()
    face = TopoDS.Face_s(TopExp_Explorer(box, TopAbs_FACE).Current())
    builder = BRep_Builder()
    shell = TopoDS_Shell()
    builder.MakeShell(shell)
    builder.Add(shell, face)
    solid = TopoDS_Solid()
    builder.MakeSolid(solid)
    builder.Add(solid, shell)

    path = directory / name
    assert BRepTools.Write_s(solid, str(path))
    return path


def _init_project(runner, isolated_dir):
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0, result.output


# --- core import flow -------------------------------------------------------

class TestImportCore:
    def test_import_step_produces_v1(self, runner, isolated_dir):
        _init_project(runner, isolated_dir)
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["import", str(step)])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["command"] == "import"
        assert parsed["status"] == "success"
        assert parsed["version"] == 1
        assert parsed["label"] == "bracket"

    def test_import_creates_version_directory_with_artifacts(
        self, runner, isolated_dir
    ):
        _init_project(runner, isolated_dir)
        step = _bracket_step(isolated_dir)
        runner.invoke(cli, ["import", str(step)])

        v1_dir = isolated_dir / "v1_bracket"
        assert v1_dir.is_dir()
        assert (v1_dir / "output.step").exists(), \
            "normalized STEP missing — every other command reads this"
        assert (v1_dir / "source.step").exists(), \
            "verbatim source copy missing — needed for provenance"
        assert (v1_dir / "meta.json").exists()
        assert (v1_dir / "preview.png").exists(), "preview not generated"
        assert (v1_dir / "output.glb").exists(), "GLB for viewer not generated"
        assert (v1_dir / "viewer.html").exists(), "unified viewer missing"

    def test_source_file_is_byte_identical_to_input(self, runner, isolated_dir):
        """source.step must be a verbatim copy. The normalized output.step is
        re-exported by OCCT and may differ slightly; provenance demands the
        original be preserved."""
        _init_project(runner, isolated_dir)
        step = _bracket_step(isolated_dir)
        original_bytes = step.read_bytes()
        runner.invoke(cli, ["import", str(step)])

        source_copy = isolated_dir / "v1_bracket" / "source.step"
        assert source_copy.read_bytes() == original_bytes

    def test_meta_json_includes_provenance_fields(self, runner, isolated_dir):
        _init_project(runner, isolated_dir)
        step = _bracket_step(isolated_dir)
        original_sha = hashlib.sha256(step.read_bytes()).hexdigest()
        runner.invoke(cli, ["import", str(step)])

        meta = json.loads(
            (isolated_dir / "v1_bracket" / "meta.json").read_text()
        )
        assert meta["source"] == "import"
        assert meta["original_filename"] == "bracket.step"
        assert meta["sha256"] == original_sha
        assert meta["tool_version"] == __version__

    def test_manifest_entry_marks_source_as_import(self, runner, isolated_dir):
        _init_project(runner, isolated_dir)
        step = _bracket_step(isolated_dir)
        runner.invoke(cli, ["import", str(step)])

        manifest = json.loads((isolated_dir / "agentcad.json").read_text())
        assert len(manifest["versions"]) == 1
        entry = manifest["versions"][0]
        assert entry["version"] == 1
        assert entry["label"] == "bracket"
        assert entry["status"] == "success"
        assert entry["source"] == "import", \
            "manifest must distinguish imports from scripted runs"
        assert manifest["current"] == "bracket"

    def test_response_includes_metrics(self, runner, isolated_dir):
        _init_project(runner, isolated_dir)
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["import", str(step)])
        parsed = json.loads(result.stdout)
        assert "metrics" in parsed
        # Bracket has a real volume; sanity check.
        assert parsed["metrics"]["volume"] > 0

    def test_invalid_geometry_is_recorded_but_not_made_current(
        self, runner, isolated_dir
    ):
        _init_project(runner, isolated_dir)
        manifest_path = isolated_dir / "agentcad.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["versions"] = [{
            "version": 1,
            "label": "baseline",
            "status": "success",
            "source": "import",
            "path": "v1_baseline/",
        }]
        manifest["current"] = "baseline"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        source = _invalid_brep(isolated_dir)

        result = runner.invoke(
            cli, ["import", str(source), "--no-view"]
        )

        assert result.exit_code == 1
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "invalid_geometry"
        assert parsed["metrics"]["is_valid"] is False
        assert parsed["version_recorded"] is True
        assert parsed["current_advanced"] is False
        invalid_dir = isolated_dir / "v2_invalid_invalid"
        assert (invalid_dir / "source.brep").exists()
        assert not (invalid_dir / "output.step").exists()
        assert not (invalid_dir / "output.glb").exists()
        assert not (invalid_dir / "preview.png").exists()
        assert not (invalid_dir / "viewer.html").exists()
        meta = json.loads((invalid_dir / "meta.json").read_text())
        manifest = json.loads(manifest_path.read_text())
        assert meta["status"] == "invalid_geometry"
        assert manifest["current"] == "baseline"
        assert manifest["versions"][-1]["status"] == "invalid_geometry"

    def test_preview_failure_preserves_registered_core(
        self, runner, isolated_dir, monkeypatch
    ):
        _init_project(runner, isolated_dir)
        source = _box_step(isolated_dir)
        observed = {}

        def fail_preview(*_args, **_kwargs):
            meta_path = isolated_dir / "v1_box" / "meta.json"
            observed["meta"] = json.loads(meta_path.read_text())
            observed["manifest"] = json.loads(
                (isolated_dir / "agentcad.json").read_text()
            )
            raise RuntimeError("injected import preview failure")

        monkeypatch.setattr(
            "agentcad.render.render_composite_4view",
            fail_preview,
        )

        result = runner.invoke(
            cli, ["import", str(source), "--no-view", "--no-daemon"]
        )

        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        assert parsed["core"]["status"] == "success"
        assert parsed["artifacts"]["preview"]["status"] == "failed"
        assert observed["meta"]["artifacts"]["preview"]["status"] == "pending"
        assert observed["manifest"]["current"] == "box"
        assert observed["manifest"]["versions"][0]["status"] == "success"
        assert (isolated_dir / "v1_box" / "output.step").exists()
        assert (isolated_dir / "v1_box" / "meta.json").exists()

    @pytest.mark.parametrize(
        ("artifact", "target"),
        [
            ("preview", "agentcad.render.render_composite_4view"),
            ("viewer_glb", "agentcad.export.export_glb"),
            ("viewer", "agentcad.commands.view._render_unified"),
        ],
    )
    def test_optional_phase_failure_never_reverses_core_success(
        self, runner, isolated_dir, monkeypatch, artifact, target
    ):
        _init_project(runner, isolated_dir)
        source = _box_step(isolated_dir)

        def injected_failure(*_args, **_kwargs):
            raise RuntimeError(f"injected import {artifact} failure")

        monkeypatch.setattr(target, injected_failure)
        result = runner.invoke(
            cli, ["import", str(source), "--no-view", "--no-daemon"]
        )

        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        assert parsed["core"]["status"] == "success"
        assert parsed["artifacts"][artifact]["status"] == "failed"
        assert (isolated_dir / "v1_box" / "output.step").exists()
        manifest = json.loads((isolated_dir / "agentcad.json").read_text())
        assert manifest["current"] == "box"
        assert manifest["versions"][0]["status"] == "success"

    def test_response_includes_next_actions_per_convention(
        self, runner, isolated_dir
    ):
        """Per design_conventions.md, success outputs include 1-2 next_actions
        and a more_at pointer."""
        _init_project(runner, isolated_dir)
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["import", str(step)])
        parsed = json.loads(result.stdout)
        assert "next_actions" in parsed
        assert 1 <= len(parsed["next_actions"]) <= 2
        assert all(" — " in a for a in parsed["next_actions"])
        assert parsed["more_at"] == "agentcad docs editing"
        assert "agentcad docs editing" in parsed["next_actions"][0]

    def test_context_surfaces_source_import(self, runner, isolated_dir):
        """Per PRD: `agentcad context` distinguishes imported versions from
        scripted ones via the `source` field — required for the audit trail."""
        _init_project(runner, isolated_dir)
        step = _bracket_step(isolated_dir)
        runner.invoke(cli, ["import", str(step)])
        result = runner.invoke(cli, ["context"])
        parsed = json.loads(result.stdout)
        assert parsed["versions"][0]["source"] == "import"

    def test_label_override(self, runner, isolated_dir):
        _init_project(runner, isolated_dir)
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["import", str(step), "--label", "vendor_part"])
        parsed = json.loads(result.stdout)
        assert parsed["label"] == "vendor_part"
        assert (isolated_dir / "v1_vendor_part").is_dir()


# --- second-import auto-diff -----------------------------------------------

class TestImportAutoDiff:
    def test_second_import_auto_diffs_against_first(self, runner, isolated_dir):
        """A revision flow: import rev_a, then import rev_b — second import
        should auto-diff against the first."""
        _init_project(runner, isolated_dir)
        first = _box_step(isolated_dir, "rev_a.step")
        runner.invoke(cli, ["import", str(first)])
        second = _bracket_step(isolated_dir, "rev_b.step")
        result = runner.invoke(cli, ["import", str(second)])
        parsed = json.loads(result.stdout)
        assert "diff" in parsed
        assert parsed["diff"]["against"] == "rev_a"
        projection = parsed["diff"]["projection_comparison"]
        assert projection["method"] == "four_view_image_mask"
        assert "score" in projection
        assert (
            parsed["diff"]["comparison_3d"]["method"]
            == "source_frame_boolean_volume"
        )
        assert parsed["diff"]["comparison_3d"]["status"] == "success"
        side = isolated_dir / "v2_rev_b" / "diff_side.png"
        assert side.exists()
        assert (isolated_dir / "v2_rev_b" / "diff_volume.png").exists()
        assert (isolated_dir / "v2_rev_b" / "diff_volume.glb").exists()

    def test_auto_diff_reports_observable_comparison_phases(
        self, runner, isolated_dir
    ):
        _init_project(runner, isolated_dir)
        first = _box_step(isolated_dir, "rev_a.step")
        runner.invoke(cli, [
            "import", str(first), "--no-view", "--no-daemon",
        ])
        second = _bracket_step(isolated_dir, "rev_b.step")

        result = runner.invoke(cli, [
            "import", str(second), "--no-view", "--no-daemon",
        ])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        expected = [
            "source_loading",
            "comparison_rendering",
            "projection_comparison",
            "exact_3d_comparison",
            "difference_artifact_export",
            "viewer_generation",
        ]
        assert list(parsed["comparison_phases"]) == expected
        for phase in expected:
            entry = parsed["comparison_phases"][phase]
            assert entry["status"] == "success"
            assert isinstance(entry["duration_ms"], int)
            assert entry["duration_ms"] >= 0
        meta = json.loads((isolated_dir / "v2_rev_b" / "meta.json").read_text())
        assert meta["comparison_phases"] == parsed["comparison_phases"]

    def test_exact_failure_is_attributed_and_projection_survives(
        self, runner, isolated_dir, monkeypatch
    ):
        _init_project(runner, isolated_dir)
        first = _box_step(isolated_dir, "rev_a.step")
        runner.invoke(cli, [
            "import", str(first), "--no-view", "--no-daemon",
        ])
        second = _bracket_step(isolated_dir, "rev_b.step")

        def fail_exact(*_args, **_kwargs):
            raise RuntimeError("injected import exact failure")

        monkeypatch.setattr(
            "agentcad.solid_compare.bounded_compare_solid_volumes", fail_exact
        )
        result = runner.invoke(cli, [
            "import", str(second), "--no-view", "--no-daemon",
        ])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        phases = parsed["comparison_phases"]
        assert phases["projection_comparison"]["status"] == "success"
        assert phases["exact_3d_comparison"]["status"] == "failed"
        assert "injected import exact failure" in (
            phases["exact_3d_comparison"]["message"]
        )
        assert phases["difference_artifact_export"]["status"] == "skipped"
        assert parsed["artifacts"]["diff"]["status"] == "success"
        assert (
            parsed["diff"]["projection_comparison"]["method"]
            == "four_view_image_mask"
        )
        assert "comparison_3d" not in parsed["diff"]

    def test_exact_timeout_is_attributed_and_projection_survives(
        self, runner, isolated_dir, monkeypatch
    ):
        _init_project(runner, isolated_dir)
        first = _box_step(isolated_dir, "rev_a.step")
        runner.invoke(cli, [
            "import", str(first), "--no-view", "--no-daemon",
        ])
        second = _bracket_step(isolated_dir, "rev_b.step")

        def exact_timeout(*_args, **_kwargs):
            from agentcad.solid_compare import SolidComparison

            return SolidComparison({
                "method": "source_frame_boolean_volume",
                "status": "timeout",
                "timeout_s": 0.05,
                "reason": {
                    "code": "exact_comparison_timeout",
                    "message": "Exact comparison timed out.",
                },
            })

        monkeypatch.setattr(
            "agentcad.solid_compare.bounded_compare_solid_volumes",
            exact_timeout,
        )
        result = runner.invoke(cli, [
            "import", str(second), "--no-view", "--no-daemon",
        ])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        phases = parsed["comparison_phases"]
        assert parsed["status"] == "success"
        assert phases["projection_comparison"]["status"] == "success"
        assert phases["exact_3d_comparison"]["status"] == "timeout"
        assert parsed["diff"]["comparison_3d"]["status"] == "timeout"
        assert parsed["artifacts"]["diff"]["status"] == "success"

    def test_no_diff_skips_every_automatic_comparison_call(
        self, runner, isolated_dir, monkeypatch
    ):
        _init_project(runner, isolated_dir)
        first = _box_step(isolated_dir, "rev_a.step")
        baseline = runner.invoke(cli, [
            "import", str(first), "--no-view", "--no-daemon",
        ])
        assert baseline.exit_code == 0, baseline.output
        second = _bracket_step(isolated_dir, "rev_b.step")

        def forbidden(*_args, **_kwargs):
            raise AssertionError("comparison work must not run with --no-diff")

        for target in (
            "agentcad.render.render_diff_side_by_side",
            "agentcad.render.render_diff_overlay",
            "agentcad.solid_compare.bounded_compare_solid_volumes",
            "agentcad.solid_compare.write_solid_comparison_artifacts",
        ):
            monkeypatch.setattr(target, forbidden)

        result = runner.invoke(cli, [
            "import", str(second), "--no-diff", "--no-view", "--no-daemon",
        ])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        version_dir = isolated_dir / "v2_rev_b"
        assert parsed["artifacts"]["diff"] == {
            "status": "skipped",
            "message": "Automatic comparison disabled with --no-diff.",
        }
        assert "diff" not in parsed
        assert not list(version_dir.glob("diff_*"))
        meta = json.loads((version_dir / "meta.json").read_text())
        assert meta["artifacts"]["diff"]["status"] == "skipped"
        viewer_html = (version_dir / "viewer.html").read_text()
        assert 'DEFAULT_MODE = "single-a"' in viewer_html


# --- non-Tier-0 inputs ------------------------------------------------------

class TestImportRejectsNonTier0:
    def test_stl_returns_polite_limited(self, runner, isolated_dir):
        """STL is Tier 1 (inspect-only). Import must refuse with the same
        honest 'not editable' messaging from inspect, NOT crash."""
        _init_project(runner, isolated_dir)
        stl = isolated_dir / "part.stl"
        stl.write_text(
            "solid x\nfacet normal 0 0 1\nouter loop\n"
            "vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
            "endloop\nendfacet\nendsolid x\n"
        )
        result = runner.invoke(cli, ["import", str(stl)])
        # Non-zero exit because the file isn't importable for editing.
        assert result.exit_code != 0
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "limited"
        assert parsed["format_detected"] == "stl"
        assert "suggestion" in parsed
        # No version directory should be created on rejection.
        assert not (isolated_dir / "v1_part").exists()
        # No manifest version entry either.
        manifest = json.loads((isolated_dir / "agentcad.json").read_text())
        assert manifest["versions"] == []

    def test_html_renamed_step_returns_malformed(self, runner, isolated_dir):
        _init_project(runner, isolated_dir)
        bogus = isolated_dir / "widget.step"
        bogus.write_text("<!DOCTYPE html>\n<html>404</html>\n")
        result = runner.invoke(cli, ["import", str(bogus)])
        assert result.exit_code != 0
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "malformed"
        assert not (isolated_dir / "v1_widget").exists()

    def test_missing_file_returns_clean_error(self, runner, isolated_dir):
        _init_project(runner, isolated_dir)
        result = runner.invoke(cli, ["import", "nope.step"])
        assert result.exit_code != 0
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "error"
        assert "Traceback" not in result.output


# --- --init shortcut --------------------------------------------------------

class TestImportInitFlag:
    def test_import_with_init_creates_manifest(self, runner, isolated_dir):
        """Human drops a STEP in a fresh folder and hands it to an agent.
        --init lets the agent get past the 'no manifest' wall in one command."""
        # NO `agentcad init` — start from empty dir.
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["import", "--init", str(step)])
        assert result.exit_code == 0, result.output
        assert (isolated_dir / "agentcad.json").exists()
        manifest = json.loads((isolated_dir / "agentcad.json").read_text())
        assert manifest["runtime"] == "build123d"
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"

    def test_import_without_init_in_empty_dir_returns_error(
        self, runner, isolated_dir
    ):
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["import", str(step)])
        assert result.exit_code != 0
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "error"
        # Error message must point the agent at the recovery path.
        text = (parsed.get("message", "") + parsed.get("suggestion", "")).lower()
        assert "init" in text


# --- BREP path (new in 1b — separate OCCT reader) --------------------------

class TestImportBrep:
    def test_brep_import_works(self, runner, isolated_dir):
        """BREP uses OCCT's BRepTools.Read directly — different code path than
        cadquery's STEP importer. Make sure it round-trips."""
        from OCP.BRepTools import BRepTools
        # Make a real shape, write it as BREP, then import it.
        plate = cq.Workplane("XY").box(20, 20, 5)
        topo_shape = plate.val().wrapped
        brep_path = isolated_dir / "part.brep"
        BRepTools.Write_s(topo_shape, str(brep_path))

        _init_project(runner, isolated_dir)
        result = runner.invoke(cli, ["import", str(brep_path)])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        assert (isolated_dir / "v1_part" / "output.step").exists()
        # source.brep keeps the verbatim BREP for provenance
        assert (isolated_dir / "v1_part" / "source.brep").exists()


# --- label collision (per PRD: rely on version number) ---------------------

class TestImportLabelCollision:
    def test_repeated_import_same_filename_creates_distinct_versions(
        self, runner, isolated_dir
    ):
        """PRD Open Question #1 resolution: two imports with the same stem
        get distinct version directories via the version number prefix."""
        _init_project(runner, isolated_dir)
        first = _bracket_step(isolated_dir, "rev.step")
        runner.invoke(cli, ["import", str(first)])
        # Overwrite with a different shape but same filename
        second = _box_step(isolated_dir, "rev.step")
        result = runner.invoke(cli, ["import", str(second)])
        assert result.exit_code == 0, result.output
        assert (isolated_dir / "v1_rev").is_dir()
        assert (isolated_dir / "v2_rev").is_dir()


# --- subprocess contract (catches fd-level leaks like inspect did) ---------

class TestImportSubprocessContract:
    @staticmethod
    def _agentcad_bin() -> Path:
        import sys as _sys
        return Path(_sys.executable).parent / "agentcad"

    def _run(self, isolated_dir, *args):
        import subprocess
        return subprocess.run(
            [str(self._agentcad_bin()), *args],
            capture_output=True, text=True, cwd=str(isolated_dir),
        )

    def test_import_real_step_stdout_is_pure_json(self, runner, isolated_dir):
        """Import via subprocess: stdout must be parseable JSON, no
        OCCT-style native diagnostic leaks."""
        _init_project(runner, isolated_dir)
        step = _bracket_step(isolated_dir)
        result = self._run(isolated_dir, "import", str(step))
        parsed = json.loads(result.stdout)
        assert parsed["command"] == "import"
        assert parsed["status"] == "success"
        assert "Traceback" not in result.stderr

    def test_import_truncated_step_stdout_is_pure_json(self, runner, isolated_dir):
        """Same OCCT-leak risk applies to import's parser path. Verify it
        doesn't regress."""
        _init_project(runner, isolated_dir)
        full = _bracket_step(isolated_dir, "full.step")
        truncated = isolated_dir / "truncated.step"
        truncated.write_text(full.read_text()[: len(full.read_text()) // 2])
        result = self._run(isolated_dir, "import", str(truncated))
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "malformed"
        assert "Traceback" not in result.stderr


# --- init --force -----------------------------------------------------------

class TestInitForce:
    def test_init_force_overwrites_existing_manifest(self, runner, isolated_dir):
        runner.invoke(cli, ["init", "--name", "first"])
        result = runner.invoke(cli, ["init", "--name", "second", "--force"])
        assert result.exit_code == 0, result.output
        manifest = json.loads((isolated_dir / "agentcad.json").read_text())
        assert manifest["name"] == "second"

    def test_init_without_force_still_errors_on_existing(
        self, runner, isolated_dir
    ):
        """Existing behavior preserved — bare `init` on a manifested folder
        still errors. Only --force changes that."""
        runner.invoke(cli, ["init"])
        result = runner.invoke(cli, ["init"])
        assert result.exit_code != 0
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "error"


# --- Daemon routing (#177) ---

def test_import_routes_through_daemon_when_available(runner, isolated_dir, monkeypatch):
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    daemon_output = json.dumps({
        "command": "import", "status": "success",
        "version": 1, "label": "foo",
    })
    monkeypatch.setattr(
        "agentcad.daemon.send_request",
        lambda *a, **kw: {"type": "result", "exit_code": 0, "output": daemon_output},
    )
    result = runner.invoke(cli, ["import", "any.step"])
    assert result.exit_code == 0, result.stdout
    parsed = json.loads(result.stdout)
    assert parsed["via"] == "daemon"
    assert parsed["command"] == "import"


def test_import_routes_no_diff_flag_through_daemon(
    runner, isolated_dir, monkeypatch
):
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    requests = []

    def fake_send_request(message, **_kwargs):
        requests.append(message)
        return {
            "type": "result",
            "exit_code": 0,
            "output": json.dumps({"command": "import", "status": "success"}),
        }

    monkeypatch.setattr("agentcad.daemon.send_request", fake_send_request)
    result = runner.invoke(cli, [
        "import", "any.step", "--label", "fast", "--no-diff", "--no-view",
    ])

    assert result.exit_code == 0, result.output
    assert requests[0]["argv"] == [
        "import", "any.step", "--label", "fast", "--no-view", "--no-diff",
    ]


def test_import_no_daemon_flag_skips_routing_and_spawn(
    runner, isolated_dir, monkeypatch
):
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    socket_path = isolated_dir / "daemon.sock"
    pid_path = isolated_dir / "daemon.pid"
    monkeypatch.setenv("AGENTCAD_SOCKET_PATH", str(socket_path))
    monkeypatch.setenv("AGENTCAD_PID_PATH", str(pid_path))
    route_calls: list[tuple] = []
    spawn_calls: list[tuple] = []

    def _track_route(*a, **kw):
        route_calls.append((a, kw))
        return None

    def _track_spawn(*a, **kw):
        spawn_calls.append((a, kw))
        return {"spawned": True}

    monkeypatch.setattr("agentcad.daemon.send_request", _track_route)
    monkeypatch.setattr("agentcad.daemon.spawn_daemon_via_fork", _track_spawn)

    _init_project(runner, isolated_dir)
    step = _box_step(isolated_dir)
    result = runner.invoke(cli, ["import", str(step), "--no-daemon"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "success"
    assert route_calls == []
    assert spawn_calls == []
    assert not socket_path.exists()
    assert not pid_path.exists()


def test_successful_import_without_flag_still_routes_then_spawns(
    runner, isolated_dir, monkeypatch
):
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    route_calls: list[tuple] = []
    spawn_calls: list[tuple] = []

    def _track_route(*a, **kw):
        route_calls.append((a, kw))
        return None

    def _track_spawn(*a, **kw):
        spawn_calls.append((a, kw))
        return {"spawned": True}

    monkeypatch.setattr("agentcad.daemon.send_request", _track_route)
    monkeypatch.setattr("agentcad.daemon.spawn_daemon_via_fork", _track_spawn)

    _init_project(runner, isolated_dir)
    step = _box_step(isolated_dir)
    result = runner.invoke(cli, ["import", str(step)])

    assert result.exit_code == 0, result.output
    assert len(route_calls) == 1
    assert len(spawn_calls) == 1


# --- --init --runtime (issue #129) ------------------------------------------

class TestImportInitRuntime:
    """`import --init --runtime <rt>` pins the bootstrapped manifest.

    Before this, --init always produced a build123d manifest, so a CadQuery
    agent taking the natural one-command path was silently pinned to the
    wrong engine and only found out when the scaffolded edit script hit a
    runtime mismatch. The docs worked around it by prescribing the two-step
    `init --runtime cadquery` then `import` dance.
    """

    def test_init_runtime_cadquery_pins_the_manifest(self, runner, isolated_dir):
        step = _bracket_step(isolated_dir)
        result = runner.invoke(
            cli, ["import", "--init", "--runtime", "cadquery", str(step)]
        )
        assert result.exit_code == 0, result.output
        manifest = json.loads((isolated_dir / "agentcad.json").read_text())
        assert manifest["runtime"] == "cadquery"

    def test_init_runtime_cadquery_scaffolds_a_cadquery_edit_script(
        self, runner, isolated_dir
    ):
        """The scaffold follows the manifest, so pinning cq must reach edit.py."""
        step = _bracket_step(isolated_dir)
        result = runner.invoke(
            cli, ["import", "--init", "--runtime", "cadquery", str(step)]
        )
        assert result.exit_code == 0, result.output
        scaffold = (isolated_dir / "edit.py").read_text()
        assert "import cadquery" in scaffold
        assert "importers.importStep" in scaffold
        assert "from build123d" not in scaffold

    def test_scaffolded_cq_edit_runs_without_a_runtime_flag(
        self, runner, isolated_dir
    ):
        """The acceptance criterion: no `--runtime` needed on the follow-up run."""
        step = _bracket_step(isolated_dir)
        runner.invoke(cli, ["import", "--init", "--runtime", "cadquery", str(step)])
        result = runner.invoke(cli, ["run", "edit.py", "--output", "scaffold_baseline"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success", parsed
        assert parsed["runtime"] == "cadquery"

    def test_init_runtime_build123d_is_explicit_but_unchanged(
        self, runner, isolated_dir
    ):
        step = _bracket_step(isolated_dir)
        result = runner.invoke(
            cli, ["import", "--init", "--runtime", "build123d", str(step)]
        )
        assert result.exit_code == 0, result.output
        manifest = json.loads((isolated_dir / "agentcad.json").read_text())
        assert manifest["runtime"] == "build123d"

    def test_init_without_runtime_still_defaults_to_build123d(
        self, runner, isolated_dir
    ):
        """Unchanged behaviour, pinned so the default cannot drift silently."""
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["import", "--init", str(step)])
        assert result.exit_code == 0, result.output
        manifest = json.loads((isolated_dir / "agentcad.json").read_text())
        assert manifest["runtime"] == "build123d"

    def test_runtime_without_init_is_rejected_not_ignored(
        self, runner, isolated_dir
    ):
        """An existing manifest already records its runtime.

        Silently ignoring the flag would recreate the footgun this flag exists
        to remove, so it is an error that names the alternatives.
        """
        _init_project(runner, isolated_dir)
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["import", "--runtime", "cadquery", str(step)])
        assert result.exit_code != 0
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "error"
        assert "the manifest already exists" in parsed["message"]
        # The project must be left untouched.
        manifest = json.loads((isolated_dir / "agentcad.json").read_text())
        assert manifest["runtime"] == "build123d"
        assert manifest["versions"] == []

    def test_runtime_with_init_is_rejected_when_manifest_exists(
        self, runner, isolated_dir
    ):
        """--init is a no-op for an existing manifest, so runtime must error."""
        _init_project(runner, isolated_dir)
        step = _bracket_step(isolated_dir)
        result = runner.invoke(
            cli, ["import", "--init", "--runtime", "cadquery", str(step)]
        )
        assert result.exit_code != 0
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "error"
        assert "the manifest already exists" in parsed["message"]
        manifest = json.loads((isolated_dir / "agentcad.json").read_text())
        assert manifest["runtime"] == "build123d"
        assert manifest["versions"] == []
        assert not (isolated_dir / "edit.py").exists()

    def test_runtime_without_init_in_fresh_directory_reports_requires_init(
        self, runner, isolated_dir
    ):
        result = runner.invoke(
            cli,
            ["import", "--runtime", "cadquery", str(isolated_dir / "part.step")],
        )
        assert result.exit_code != 0
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "error"
        assert "--runtime requires --init" in parsed["message"]
        assert "--init --runtime cadquery" in parsed["suggestion"]
        assert not (isolated_dir / "agentcad.json").exists()

    def test_runtime_rejects_an_unknown_engine(self, runner, isolated_dir):
        step = _bracket_step(isolated_dir)
        result = runner.invoke(
            cli, ["import", "--init", "--runtime", "openscad", str(step)]
        )
        assert result.exit_code != 0
        assert "openscad" in result.output
