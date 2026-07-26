"""Runtime dispatcher — import detection + runner resolution.

Lives in tests/ rather than tests_b3d/ because dispatching
is CLI infrastructure, not build123d-specific. Every regression run
checks it.
"""

from __future__ import annotations

import pytest

from agentcad.runners import dispatch


class TestDetect:
    def test_empty_script_defaults_to_dispatch_default(self):
        """No imports → ``dispatch.DEFAULT_RUNTIME`` (tracks the constant so
        Phase-6-style flips don't require a coordinated test edit)."""
        assert dispatch.detect("") == dispatch.DEFAULT_RUNTIME

    def test_comment_only_script_defaults_to_dispatch_default(self):
        assert dispatch.detect("# just a comment\n") == dispatch.DEFAULT_RUNTIME

    def test_unrelated_imports_default_to_dispatch_default(self):
        assert dispatch.detect("import math\nimport os\n") == dispatch.DEFAULT_RUNTIME

    def test_plain_import_cadquery(self):
        assert dispatch.detect("import cadquery\n") == "cadquery"

    def test_aliased_import_cadquery(self):
        assert dispatch.detect("import cadquery as cq\n") == "cadquery"

    def test_from_import_cadquery(self):
        assert dispatch.detect("from cadquery import Workplane\n") == "cadquery"

    def test_nested_from_import_cadquery(self):
        assert dispatch.detect("from cadquery.occ_impl.shapes import Solid\n") == "cadquery"

    def test_legacy_zero_import_cq_workplane(self):
        assert dispatch.detect("show_object(cq.Workplane('XY').box(1, 2, 3))\n") == "cadquery"

    def test_unrelated_cq_string_does_not_trigger(self):
        assert dispatch.detect('message = "cq.Workplane"\n') == dispatch.DEFAULT_RUNTIME

    def test_plain_import_build123d(self):
        assert dispatch.detect("import build123d\n") == "build123d"

    def test_aliased_import_build123d(self):
        assert dispatch.detect("import build123d as b\n") == "build123d"

    def test_from_star_import_build123d(self):
        assert dispatch.detect("from build123d import *\n") == "build123d"

    def test_from_specific_import_build123d(self):
        assert dispatch.detect("from build123d import Box, Cylinder\n") == "build123d"

    def test_nested_from_import_build123d(self):
        assert dispatch.detect("from build123d.topology import Shape\n") == "build123d"

    def test_ambiguous_both_raises(self):
        with pytest.raises(ValueError, match="ambiguous"):
            dispatch.detect("import cadquery\nfrom build123d import Box\n")

    def test_ambiguous_error_mentions_both_libs(self):
        with pytest.raises(ValueError) as exc:
            dispatch.detect("import cadquery\nimport build123d\n")
        msg = str(exc.value)
        assert "cadquery" in msg and "build123d" in msg

    def test_syntax_error_falls_back_to_default(self):
        """Unparseable sources fall back to ``dispatch.DEFAULT_RUNTIME``;
        the runner's validate() is responsible for surfacing the syntax
        error."""
        assert dispatch.detect("import cadquery as\n") == dispatch.DEFAULT_RUNTIME

    def test_nested_cadquery_string_does_not_trigger(self):
        """Strings containing 'cadquery' don't count as imports — these
        fall through to ``dispatch.DEFAULT_RUNTIME``."""
        assert dispatch.detect('x = "cadquery"\n') == dispatch.DEFAULT_RUNTIME
        assert dispatch.detect('x = "build123d"\n') == dispatch.DEFAULT_RUNTIME


class TestGetRunner:
    def test_returns_cadquery_module(self):
        runner = dispatch.get_runner("cadquery")
        assert hasattr(runner, "execute")
        assert hasattr(runner, "validate")
        assert hasattr(runner, "PREAMBLE")

    def test_returns_build123d_module(self):
        runner = dispatch.get_runner("build123d")
        assert hasattr(runner, "execute")
        assert hasattr(runner, "validate")
        assert hasattr(runner, "PREAMBLE")

    def test_unknown_name_raises(self):
        with pytest.raises(ValueError, match="unknown runtime"):
            dispatch.get_runner("openscad")  # type: ignore[arg-type]


class TestResolve:
    def test_auto_detects_cadquery(self):
        name, runner = dispatch.resolve("import cadquery\n")
        assert name == "cadquery"
        assert runner.execute.__module__.endswith("cadquery")

    def test_auto_detects_build123d(self):
        name, runner = dispatch.resolve("from build123d import Box\n")
        assert name == "build123d"
        assert runner.execute.__module__.endswith("build123d")

    def test_override_wins_over_detection(self):
        """--runtime=build123d should force b3d even for cadquery-importing source."""
        name, runner = dispatch.resolve("import cadquery\n", override="build123d")
        assert name == "build123d"

    def test_override_rejected_if_unknown(self):
        with pytest.raises(ValueError, match="unknown --runtime"):
            dispatch.resolve("", override="openscad")

    def test_override_empty_string_is_ignored(self):
        """Empty override string (default from click) falls through to detection."""
        name, _ = dispatch.resolve("import cadquery\n", override="")
        assert name == "cadquery"

    def test_override_none_is_ignored(self):
        name, _ = dispatch.resolve("from build123d import Box\n", override=None)
        assert name == "build123d"

    def test_project_runtime_rejects_detected_mismatch(self):
        with pytest.raises(ValueError, match="--runtime cadquery"):
            dispatch.resolve(
                "show_object(cq.Workplane('XY').box(1, 2, 3))\n",
                project_default="build123d",
            )
