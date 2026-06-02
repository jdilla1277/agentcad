"""Phase 2.5 — project-level runtime mode.

End-to-end checks that:
  - `agentcad init --runtime <engine>` stamps the manifest.
  - `dispatch.project_runtime()` reads it correctly.
  - `resolve()` honors project_default at the right precedence.
  - `agentcad docs` serves runtime-specific content when in a b3d project.
  - `agentcad run` on a script with no imports follows the project mode.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcad.cli import cli
from agentcad.runners import dispatch


# ---------- init --runtime ----------

class TestInitRuntime:
    def test_init_without_runtime_omits_field(self, runner, isolated_dir):
        result = runner.invoke(cli, ["init", "--name", "p"])
        assert result.exit_code == 0
        manifest = json.loads((isolated_dir / "agentcad.json").read_text())
        assert "runtime" not in manifest

    def test_init_with_cadquery_stamps_field(self, runner, isolated_dir):
        result = runner.invoke(cli, ["init", "--name", "p", "--runtime", "cadquery"])
        assert result.exit_code == 0
        manifest = json.loads((isolated_dir / "agentcad.json").read_text())
        assert manifest["runtime"] == "cadquery"
        assert json.loads(result.stdout)["runtime"] == "cadquery"

    def test_init_with_build123d_stamps_field(self, runner, isolated_dir):
        result = runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
        assert result.exit_code == 0
        manifest = json.loads((isolated_dir / "agentcad.json").read_text())
        assert manifest["runtime"] == "build123d"

    def test_init_rejects_unknown_runtime(self, runner, isolated_dir):
        result = runner.invoke(cli, ["init", "--runtime", "openscad"])
        assert result.exit_code != 0  # click.Choice rejects


# ---------- dispatch.project_runtime ----------

class TestProjectRuntime:
    def test_no_manifest_returns_none(self, isolated_dir):
        assert dispatch.project_runtime() is None

    def test_manifest_without_runtime_returns_none(self, isolated_dir):
        (isolated_dir / "agentcad.json").write_text(json.dumps({"name": "p", "versions": []}))
        assert dispatch.project_runtime() is None

    def test_manifest_with_build123d_returns_build123d(self, isolated_dir):
        (isolated_dir / "agentcad.json").write_text(
            json.dumps({"name": "p", "runtime": "build123d", "versions": []})
        )
        assert dispatch.project_runtime() == "build123d"

    def test_manifest_with_cadquery_returns_cadquery(self, isolated_dir):
        (isolated_dir / "agentcad.json").write_text(
            json.dumps({"name": "p", "runtime": "cadquery", "versions": []})
        )
        assert dispatch.project_runtime() == "cadquery"

    def test_unknown_runtime_in_manifest_returns_none(self, isolated_dir):
        (isolated_dir / "agentcad.json").write_text(
            json.dumps({"name": "p", "runtime": "openscad", "versions": []})
        )
        assert dispatch.project_runtime() is None

    def test_corrupt_manifest_returns_none(self, isolated_dir):
        (isolated_dir / "agentcad.json").write_text("not json{")
        assert dispatch.project_runtime() is None

    def test_search_parents_finds_manifest_in_parent_dir(self, isolated_dir, monkeypatch):
        """`docs` invoked from a subdir should still find the project pin.

        Agents driving via shell-with-non-persistent-cwd (e.g. Bash tool)
        often end up running `agentcad docs preamble` from outside the
        project root. Without parent-walking, the manifest is invisible
        and docs falls back to the global default — which became b3d
        post-#163, systematically wrong for cq-pinned projects.
        """
        (isolated_dir / "agentcad.json").write_text(
            json.dumps({"name": "p", "runtime": "cadquery", "versions": []})
        )
        subdir = isolated_dir / "deep" / "nested"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)
        # Default (cwd-only) still returns None — preserves existing contract for `run`/`inspect`.
        assert dispatch.project_runtime() is None
        # With search_parents, the upstream manifest is found.
        assert dispatch.project_runtime(search_parents=True) == "cadquery"

    def test_search_parents_stops_at_root_when_no_manifest(self, isolated_dir, monkeypatch):
        subdir = isolated_dir / "no" / "manifest" / "anywhere"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)
        assert dispatch.project_runtime(search_parents=True) is None


# ---------- resolve precedence ----------

class TestResolvePrecedence:
    def test_default_falls_back_to_dispatch_default(self):
        """No project pin, no imports, no override → ``DEFAULT_RUNTIME``.
        Tracks the constant rather than hardcoding so Phase 6's flip
        carries this test along automatically."""
        name, _ = dispatch.resolve("show_object(None)\n")
        assert name == dispatch.DEFAULT_RUNTIME

    def test_project_default_used_when_no_imports(self):
        name, _ = dispatch.resolve("show_object(None)\n", project_default="build123d")
        assert name == "build123d"

    def test_explicit_imports_beat_project_default(self):
        """An import-cadquery script in a b3d project still routes cadquery."""
        name, _ = dispatch.resolve("import cadquery\n", project_default="build123d")
        assert name == "cadquery"

    def test_override_beats_project_default(self):
        name, _ = dispatch.resolve("show_object(None)\n",
                                   override="cadquery", project_default="build123d")
        assert name == "cadquery"

    def test_override_beats_imports_too(self):
        name, _ = dispatch.resolve("from build123d import Box\n",
                                   override="cadquery", project_default="build123d")
        assert name == "cadquery"


# ---------- docs runtime-aware ----------

class TestDocsRuntimeAware:
    def test_docs_in_cadquery_project_shows_cq_preamble(self, runner, isolated_dir):
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "cadquery"])
        result = runner.invoke(cli, ["docs", "preamble"])
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        assert parsed["runtime"] == "cadquery"
        assert "cq             cadquery module" in parsed["content"]

    def test_docs_in_build123d_project_shows_b3d_preamble(self, runner, isolated_dir):
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
        result = runner.invoke(cli, ["docs", "preamble"])
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        assert parsed["runtime"] == "build123d"
        assert "build123d (the entire public API" in parsed["content"]
        # Sanity: explicitly mentions the wrap pattern from prior user testing.
        assert "Face(Wire(" in parsed["content"]

    def test_docs_from_subdir_of_cq_project_still_shows_cq(self, runner, isolated_dir, monkeypatch):
        """`docs` invoked from a subdir of a cq-pinned project should
        return cq content, not the global-default b3d. This is the
        symptom multiple agent trials independently reported. Pre-#163
        this bug was invisible because the global default and the cq pin agreed.
        """
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "cadquery"])
        subdir = isolated_dir / "scripts" / "experiments"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)
        result = runner.invoke(cli, ["docs", "preamble"])
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        assert parsed["runtime"] == "cadquery", (
            "docs invoked from inside a project tree must honor the manifest"
        )
        assert "cq             cadquery module" in parsed["content"]

    def test_docs_b3d_preamble_explains_extrude_direction(self, runner, isolated_dir):
        """extrude(sketch, amount=N) pushes along the sketch plane's +normal,
        and fresh agents have to guess that without a doc. Issue #168 — UC4
        phone-stand friction trial inferred it from build123d experience but
        flagged the absence."""
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
        result = runner.invoke(cli, ["docs", "preamble"])
        content = json.loads(result.stdout)["content"]
        # Each plane → axis mapping must be present so the agent doesn't guess.
        assert "Plane.XY" in content and "+Z" in content
        assert "Plane.XZ" in content and "+Y" in content
        assert "Plane.YZ" in content and "+X" in content
        # And the "amount=-N flips" escape hatch.
        assert "amount=-N" in content or "flip" in content.lower()

    def test_docs_runtime_override_wins_over_project(self, runner, isolated_dir):
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "cadquery"])
        result = runner.invoke(cli, ["docs", "preamble", "--runtime", "build123d"])
        parsed = json.loads(result.stdout)
        assert parsed["runtime"] == "build123d"

    def test_docs_no_arg_in_b3d_project_lists_b3d_sections(self, runner, isolated_dir):
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
        result = runner.invoke(cli, ["docs"])
        parsed = json.loads(result.stdout)
        assert parsed["runtime"] == "build123d"
        # `examples` is a build123d-only section we added
        assert "examples" in parsed["sections"]

    def test_docs_b3d_examples_section_exists(self, runner, isolated_dir):
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
        result = runner.invoke(cli, ["docs", "examples"])
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        # Three examples: plate, bracket, gear
        assert "Example 1" in parsed["content"]
        assert "Example 2" in parsed["content"]
        assert "Example 3" in parsed["content"]

    def test_docs_b3d_example3_is_buildable(self, runner, isolated_dir):
        """The headline gear recipe must produce working geometry — it did not
        previously, which is exactly the Phase 2.5 friction-log finding."""
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
        result = runner.invoke(cli, ["docs", "examples"])
        parsed = json.loads(result.stdout)
        # Subtractive cylinder-blank recipe, not the broken Face(Wire(involute)) path.
        assert "Cylinder(radius=tip_radius" in parsed["content"]
        assert "Face(Wire(profile))" not in parsed["content"]
        # And the limitation is documented so future agents know why.
        assert "issue #68" in parsed["content"]

    def test_docs_b3d_parts_overlay_documents_per_part(self, runner, isolated_dir):
        """After PR #79 closed issue #69, build123d supports per-part output.
        The overlay should now teach the working API, not the old limitation."""
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
        result = runner.invoke(cli, ["docs", "parts"])
        parsed = json.loads(result.stdout)
        content = parsed["content"]
        # Teaches the per-part API
        assert "parts" in content.lower()
        assert "name=" in content
        assert "color" in content.lower()
        # Stale limitation language should be gone
        assert "CADQUERY-ONLY" not in content
        assert "issues/69" not in content

    def test_docs_b3d_patterns_includes_edges_by_position(self, runner, isolated_dir):
        """filter_by(Axis.Z) footgun: docs should teach position-based fallback."""
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
        result = runner.invoke(cli, ["docs", "patterns"])
        parsed = json.loads(result.stdout)
        assert "parallel to the Z axis" in parsed["content"].lower() or "PARALLEL to the Z axis" in parsed["content"]
        assert "e.center().Z" in parsed["content"]

    def test_docs_outside_project_surfaces_runtime_hint(self, isolated_dir):
        """Fresh user running docs with no project pinned gets a hint that the
        other runtime is available. Prevents silently landing on wrong-engine
        docs. Tracks ``dispatch.DEFAULT_RUNTIME`` rather than hardcoding so the
        Phase 6 flip carries this test along automatically."""
        from click.testing import CliRunner
        from agentcad.runners import dispatch
        r = CliRunner()
        # isolated_dir has no agentcad.json — and no --runtime flag.
        result = r.invoke(cli, ["docs", "preamble"])
        parsed = json.loads(result.stdout)
        expected_default = dispatch.DEFAULT_RUNTIME
        expected_other = "build123d" if expected_default == "cadquery" else "cadquery"
        assert parsed["runtime"] == expected_default
        assert "runtime_hint" in parsed
        assert expected_other in parsed["runtime_hint"]


    def test_docs_default_runtime_follows_dispatch_default(self, monkeypatch, isolated_dir):
        """Lock the contract that docs uses ``dispatch.DEFAULT_RUNTIME`` —
        not a hardcoded string. Monkeypatch the constant and confirm docs
        follows. This is the test that would have caught the pre-Phase-6
        ``or "cadquery"`` fallback."""
        from click.testing import CliRunner
        from agentcad.runners import dispatch
        monkeypatch.setattr(dispatch, "DEFAULT_RUNTIME", "build123d")
        r = CliRunner()
        result = r.invoke(cli, ["docs", "preamble"])
        parsed = json.loads(result.stdout)
        assert parsed["runtime"] == "build123d"
        # Hint should now point at cadquery as the alternative.
        assert "cadquery" in parsed["runtime_hint"]

    def test_docs_in_pinned_project_has_no_hint(self, runner, isolated_dir):
        """Hint only appears when there's genuine ambiguity — suppress it once
        the project has committed to a runtime."""
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "cadquery"])
        result = runner.invoke(cli, ["docs", "preamble"])
        parsed = json.loads(result.stdout)
        assert "runtime_hint" not in parsed


# ---------- run command honors project mode ----------

class TestRunFollowsProjectMode:
    def test_no_import_script_uses_b3d_in_b3d_project(self, runner, isolated_dir):
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
        # Bare script — no `import build123d` — but Box is pre-injected by the b3d preamble.
        (isolated_dir / "bare.py").write_text("show_object(Box(3, 4, 5))\n")

        result = runner.invoke(cli, ["run", "bare.py", "--output", "v1", "--dry-run"])
        parsed = json.loads(result.stdout)
        assert result.exit_code == 0, result.output
        assert parsed["runtime"] == "build123d"
        assert parsed["metrics"]["volume"] == 60.0

    def test_no_import_script_uses_cq_in_cq_project(self, runner, isolated_dir):
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "cadquery"])
        # Bare script — relies on cq pre-injection.
        (isolated_dir / "bare.py").write_text("show_object(cq.Workplane('XY').box(3, 4, 5))\n")

        result = runner.invoke(cli, ["run", "bare.py", "--output", "v1", "--dry-run"])
        parsed = json.loads(result.stdout)
        assert result.exit_code == 0, result.output
        assert parsed["runtime"] == "cadquery"

    def test_no_import_script_uses_default_when_no_project_runtime(self, runner, isolated_dir):
        """No project pin, no imports → dispatch.DEFAULT_RUNTIME. The script
        uses syntax appropriate to whichever runtime is currently the
        default so --dry-run actually executes; the assertion tracks the
        constant so Phase 6's flip carries the test along."""
        runner.invoke(cli, ["init", "--name", "p"])

        if dispatch.DEFAULT_RUNTIME == "cadquery":
            script = "show_object(cq.Workplane('XY').box(1, 2, 3))\n"
        else:
            script = "show_object(Box(1, 2, 3))\n"
        (isolated_dir / "bare.py").write_text(script)

        result = runner.invoke(cli, ["run", "bare.py", "--output", "v1", "--dry-run"])
        parsed = json.loads(result.stdout)
        assert parsed["runtime"] == dispatch.DEFAULT_RUNTIME

    def test_explicit_cadquery_import_wins_in_b3d_project(self, runner, isolated_dir):
        """Escape hatch: explicit imports always beat project mode."""
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
        (isolated_dir / "cq.py").write_text(
            "import cadquery as cq\nshow_object(cq.Workplane('XY').box(1, 2, 3))\n"
        )

        result = runner.invoke(cli, ["run", "cq.py", "--output", "v1", "--dry-run"])
        parsed = json.loads(result.stdout)
        assert parsed["runtime"] == "cadquery"

    def test_meta_json_records_runtime_in_b3d_project(self, runner, isolated_dir):
        runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
        (isolated_dir / "bare.py").write_text("show_object(Box(5, 5, 5))\n")

        result = runner.invoke(cli, ["run", "bare.py", "--output", "logged", "--no-preview"])
        assert result.exit_code == 0, result.output

        meta = json.loads((isolated_dir / "v1_logged" / "meta.json").read_text())
        assert meta["runtime"] == "build123d"
