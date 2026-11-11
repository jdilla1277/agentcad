"""Check the installed package's ordinary and experimental Inspect paths."""

import json
from pathlib import Path
import tempfile

from click.testing import CliRunner

from agentcad.cli import cli
import agentcad


def check():
    source = Path(__file__).resolve().parent.parent / "src"
    assert not Path(agentcad.__file__).resolve().is_relative_to(source), "Run this guard against the installed wheel, not the source checkout"
    runner = CliRunner()
    env = {"AGENTCAD_DAEMON": "1", "AGENTCAD_NO_LOG": "1"}

    def invoke(args, code=0):
        result = runner.invoke(cli, args, env=env)
        assert result.exit_code == code, (args, result.stdout, result.exception)
        return json.loads(result.stdout)

    with tempfile.TemporaryDirectory(prefix="inspect-release-") as directory:
        with runner.isolated_filesystem(temp_dir=directory):
            root = Path.cwd()
            refused = invoke(["inspect", "missing.step", "--report", "--no-daemon"], 1)
            assert refused["reason"] == "experimental_inspect_required"
            assert not list(root.iterdir()), "Unacknowledged Inspect wrote project state"
            invoke(["init", "--name", "release-gate", "--no-agent-setup"])
            Path("part.py").write_text("show_object(Box(10,20,5))\n", encoding="utf-8")
            quiet = ["--no-preview", "--no-view", "--no-diff", "--no-daemon"]
            ordinary = invoke(["run", "part.py", "--label", "ordinary", *quiet])
            assert "geometry_manifest" not in ordinary and "experimental_inspect" not in ordinary
            baseline = Path(ordinary["outputs"]["step"])
            assert not baseline.with_name("geometry_manifest.json").exists()
            assert not baseline.with_name("inspection").exists()
            assert not (root / ".agentcad/inspection_delivery").exists()
            opted = invoke(["run", "part.py", "--label", "opted", "--experimental-inspect", *quiet])
            assert opted["experimental_inspect"]["status"] == "work_in_progress"
            baseline = Path(opted["outputs"]["step"])
            assert baseline.with_name("geometry_manifest.json").is_file()
            denied = invoke(["inspect", str(baseline), "--id", "face_v2_1", "--no-daemon"], 1)
            assert denied["reason"] == "experimental_inspect_required"
            basic = invoke(["inspect", str(baseline), "--ids", "--no-daemon"])
            assert "experimental_inspect" not in basic
            assert "geometry_manifest" not in basic
    print("Installed Inspect gate passed: standard CAD works, rich Inspect requires per-call acknowledgment.")


if __name__ == "__main__":
    check()
