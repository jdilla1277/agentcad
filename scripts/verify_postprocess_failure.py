#!/usr/bin/env python3
"""Development harness for the commit-before-post-processing contract.

Runs in a temporary project and injects a preview exception with a mock. It
does not add a production failure flag or modify the caller's project.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from agentcad.cli import cli
from agentcad.step_io import load_cad_shape
from agentcad.metrics import compute_metrics


SCRIPT = """\
from build123d import Box
show_object(Box(10, 10, 10))
"""


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentcad-postprocess-") as temp:
        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=temp):
            # CliRunner creates a child directory; use it as the disposable
            # project so all command-relative paths match normal CLI use.
            scratch = Path.cwd()
            (scratch / "verify.py").write_text(SCRIPT)
            init = runner.invoke(cli, [
                "init", "--name", "postprocess-verification",
                "--runtime", "build123d",
            ])
            if init.exit_code != 0:
                raise RuntimeError(init.output)

            with patch(
                "agentcad.render.render_composite_4view",
                side_effect=RuntimeError("injected preview failure"),
            ):
                result = runner.invoke(cli, [
                    "run", "verify.py", "--output", "postprocess_test",
                    "--no-view", "--no-daemon",
                ])

            payload = json.loads(result.stdout)
            version_dir = scratch / "v1_postprocess_test"
            meta = json.loads((version_dir / "meta.json").read_text())
            manifest = json.loads((scratch / "agentcad.json").read_text())
            step_path = version_dir / "output.step"
            metrics = compute_metrics(load_cad_shape(step_path))

            checks = {
                "exit_code_zero": result.exit_code == 0,
                "overall_success": payload.get("status") == "success",
                "core_success": payload.get("core", {}).get("status") == "success",
                "preview_failed": (
                    payload.get("artifacts", {}).get("preview", {}).get("status")
                    == "failed"
                ),
                "step_exists": step_path.exists(),
                "meta_exists": (version_dir / "meta.json").exists(),
                "manifest_registered_once": len(manifest.get("versions", [])) == 1,
                "current_advanced": (
                    manifest.get("current") == "postprocess_test"
                ),
                "step_valid": metrics.get("is_valid") is True,
                "no_failed_copy": not (
                    scratch / "v1_postprocess_test_failed"
                ).exists(),
                "meta_core_success": meta.get("core", {}).get("status") == "success",
            }
            summary = {
                "status": "success" if all(checks.values()) else "failed",
                "checks": checks,
                "run": payload,
            }
            print(json.dumps(summary, indent=2))
            return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
