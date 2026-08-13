#!/usr/bin/env python3
"""Development harness proving comparison source views render only once."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

import agentcad.render as render_module
from agentcad.cli import cli


SCRIPT = """\
from build123d import Box
show_object(Box({size}, 10, 10))
"""


def _invoke_json(runner: CliRunner, args: list[str]) -> tuple[object, dict]:
    result = runner.invoke(cli, args)
    if not result.stdout:
        raise RuntimeError(result.exception or "command returned no JSON")
    return result, json.loads(result.stdout)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentcad-render-reuse-") as temp:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=temp):
            project = Path.cwd()
            script = project / "verify.py"
            script.write_text(SCRIPT.format(size=10))
            init, _ = _invoke_json(runner, [
                "init", "--name", "render-reuse-verification",
                "--runtime", "build123d",
            ])
            first, _ = _invoke_json(runner, [
                "run", "verify.py", "--output", "first",
                "--no-preview", "--no-view", "--no-daemon",
            ])

            # Keep geometry identical so any projection difference can only
            # come from the previous STEP's neutral material versus the
            # current named part's palette color.
            script.write_text(SCRIPT.format(size=10))
            source_batch_calls = []
            source_sessions = 0
            tracking_sources = False
            original_batch = render_module.render_shape_batch
            original_sources = render_module.render_comparison_source_views

            def tracked_batch(*args, **kwargs):
                if tracking_sources:
                    source_batch_calls.append(len(args[1]))
                return original_batch(*args, **kwargs)

            def tracked_sources(*args, **kwargs):
                nonlocal source_sessions, tracking_sources
                source_sessions += 1
                tracking_sources = True
                try:
                    return original_sources(*args, **kwargs)
                finally:
                    tracking_sources = False

            with patch.object(
                render_module, "render_shape_batch", tracked_batch
            ), patch.object(
                render_module,
                "render_comparison_source_views",
                tracked_sources,
            ):
                second, response = _invoke_json(runner, [
                    "run", "verify.py", "--output", "second",
                    "--no-preview", "--no-view", "--no-daemon",
                ])

            version_dir = project / "v2_second"
            phases = response.get("comparison_phases", {})
            projection = response.get("diff", {}).get(
                "projection_comparison", {}
            )
            checks = {
                "runs_succeed": first.exit_code == 0 and second.exit_code == 0,
                "one_shared_source_session": source_sessions == 1,
                "one_batch_per_source": source_batch_calls == [4, 4],
                "eight_source_view_captures": sum(source_batch_calls) == 8,
                "side_by_side_exists": (version_dir / "diff_side.png").exists(),
                "overlay_exists": (version_dir / "diff_overlay.png").exists(),
                "rendering_phase_succeeds": (
                    phases.get("comparison_rendering", {}).get("status")
                    == "success"
                ),
                "projection_phase_succeeds": (
                    phases.get("projection_comparison", {}).get("status")
                    == "success"
                ),
                "display_color_does_not_create_false_difference": (
                    projection.get("score", {}).get("value") == 1.0
                    and all(
                        view.get("coincident_fraction_of_union") == 1.0
                        and view.get("reference_only_fraction_of_union") == 0.0
                        and view.get("candidate_only_fraction_of_union") == 0.0
                        for view in projection.get("views", [])
                    )
                ),
            }
            summary = {
                "status": "success" if all(checks.values()) else "failed",
                "checks": checks,
                "source_batch_calls": source_batch_calls,
                "source_view_capture_count": sum(source_batch_calls),
                "comparison_phases": phases,
            }
            print(json.dumps(summary, indent=2))
            return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
