"""Check the installed package's default Milestone 2 Inspect contract."""

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
            invoke(["init", "--name", "release-contract", "--no-agent-setup"])
            Path("part.py").write_text("show_object(Box(10,20,5))\n", encoding="utf-8")
            quiet = ["--no-preview", "--no-view", "--no-diff", "--no-daemon"]
            built = invoke(["run", "part.py", "--label", "source", *quiet])
            assert built["geometry_manifest"] and "experimental_inspect" not in built
            baseline = Path(built["outputs"]["step"])
            assert baseline.with_name("geometry_manifest.json").is_file()
            imported = invoke(["import", str(baseline), "--label", "baseline", "--max-images", "1", "--no-view", "--no-diff", "--no-daemon"])
            assert imported["core"]["status"] == "success"
            assert imported["inspection"]["media"] and imported["response_snapshot"]
            assert "--experimental-inspect" not in imported["response_snapshot"]["retrieve"]
            baseline = Path(imported["outputs"]["step"])
            entity = invoke(["inspect", str(baseline), "--id", "face_v2_1", "--no-daemon"])
            assert entity["entity"]["id"] == "face_v2_1" and entity["selector"]
            image = invoke(["inspect", str(baseline), "--id", "face_v2_1", "--image", "--no-daemon"])
            assert (root / image["image"]["png"]).is_file()
            compatible = invoke(["inspect", str(baseline), "--id", "face_v2_1", "--experimental-inspect", "--no-daemon"])
            assert compatible == entity
            query = json.dumps({"kind": "face", "where": {"id": "face_v2_1"}})
            gated = invoke(["inspect", str(baseline), "--query", query, "--no-daemon"], 1)
            assert gated["reason"] == "experimental_geometry_search_required"
            search = invoke(["inspect", str(baseline), "--query", query, "--search-images", "0",
                             "--experimental-geometry-search", "--no-daemon"])
            assert search["match_count"] == 1 and search["results"][0]["id"] == "face_v2_1"
            assert search["ranking_mode"] == "not_applicable" and search["comparison_counts"] is None
            retained = invoke(["inspect", str(baseline), "--search-snapshot", search["search_snapshot"]["id"],
                               "--experimental-geometry-search", "--no-daemon"])
            assert retained["results"] == search["results"]
            artifact = invoke(["inspect", str(baseline), "--artifact", search["candidate_manifest"]["id"],
                               "--experimental-geometry-search", "--no-daemon"])
            assert artifact["artifact"]["sha256"] == search["candidate_manifest"]["sha256"]
            lean = invoke(["import", str(baseline), "--label", "lean", "--no-report", "--no-inspection-image", "--no-view", "--no-diff", "--no-daemon"])
            assert "inspection" not in lean and lean["geometry_manifest"]
            refused = invoke(["run", "part.py", "--label", "staged", "--stage", *quiet], 1)
            assert refused["reason"] == "experimental_changes_required"
    print("Installed Inspect contract passed: Milestone 2 is default-on; geometry search and changes remain gated.")


if __name__ == "__main__":
    check()
