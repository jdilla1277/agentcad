import json

import cadquery as cq
from cadquery import exporters

from agentcad.cli import cli


def _box_step(directory, name="box.step"):
    box = cq.Workplane("XY").box(10, 20, 5)
    path = directory / name
    exporters.export(box, str(path))
    return path


def _cylinder_step(directory, name="cylinder.step"):
    cyl = cq.Workplane("XY").circle(5).extrude(20)
    path = directory / name
    exporters.export(cyl, str(path))
    return path


class TestMeasureCommand:
    def test_measure_returns_json_metrics_and_feature_summary(self, runner, isolated_dir):
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["measure", str(step)])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["command"] == "measure"
        assert parsed["status"] == "success"
        assert parsed["metrics"]["dimensions"]["x"] == 10.0
        assert parsed["metrics"]["dimensions"]["y"] == 20.0
        assert parsed["metrics"]["dimensions"]["z"] == 5.0
        assert parsed["feature_summary"]["face_count"] == 6
        assert parsed["feature_summary"]["edge_count"] == 12
        assert "features" not in parsed

    def test_measure_summary_reports_circle_and_cylinder_diameter(
        self, runner, isolated_dir
    ):
        step = _cylinder_step(isolated_dir)
        result = runner.invoke(cli, ["measure", str(step)])
        assert result.exit_code == 0, result.output
        summary = json.loads(result.stdout)["feature_summary"]
        cyl_faces = [
            c for c in summary["face_clusters"] if c["kind"] == "cylindrical"
        ]
        circle_edges = [
            c for c in summary["edge_clusters"] if c["kind"] == "circle"
        ]
        assert any(c["radius"] == 5.0 and c["diameter"] == 10.0 for c in cyl_faces)
        assert any(c["radius"] == 5.0 and c["diameter"] == 10.0 for c in circle_edges)

    def test_measure_features_flag_adds_full_feature_lists(self, runner, isolated_dir):
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["measure", str(step), "--features"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        features = parsed["features"]
        assert len(features["solids"]) == 1
        assert len(features["faces"]) == 6
        assert len(features["edges"]) == 12
        assert "area" in features["faces"][0]
        assert "length" in features["edges"][0]

    def test_measure_missing_file_error(self, runner, isolated_dir):
        result = runner.invoke(cli, ["measure", "missing.step"])
        assert result.exit_code == 1
        parsed = json.loads(result.stdout)
        assert parsed["command"] == "measure"
        assert parsed["status"] == "error"

    def test_measure_returns_no_artifacts(self, runner, isolated_dir):
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["measure", str(step)])
        parsed = json.loads(result.stdout)
        assert "outputs" not in parsed
        assert "renders" not in parsed

    def test_measure_next_actions_do_not_redirect_measurements_to_inspect(
        self, runner, isolated_dir
    ):
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["measure", str(step)])
        parsed = json.loads(result.stdout)
        joined = " ".join(parsed["next_actions"])
        assert "feature_summary" in joined
        assert "inspect" in joined
        assert "--summary" not in joined


class TestMeasureDaemonRouting:
    def test_measure_routes_through_daemon_when_available(
        self, runner, isolated_dir, monkeypatch
    ):
        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
        daemon_output = json.dumps({
            "command": "measure",
            "status": "success",
            "metrics": {"dimensions": {"x": 10, "y": 20, "z": 5}},
            "feature_summary": {"face_count": 6, "edge_count": 12},
        })
        monkeypatch.setattr(
            "agentcad.daemon.send_request",
            lambda *a, **kw: {"type": "result", "exit_code": 0, "output": daemon_output},
        )
        result = runner.invoke(cli, ["measure", "any.step"])
        assert result.exit_code == 0, result.stdout
        parsed = json.loads(result.stdout)
        assert parsed["via"] == "daemon"
        assert parsed["command"] == "measure"

    def test_measure_no_daemon_flag_skips_routing(
        self, runner, isolated_dir, monkeypatch
    ):
        step = _box_step(isolated_dir)
        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
        calls = []

        def _track(*a, **kw):
            calls.append((a, kw))
            return None

        monkeypatch.setattr("agentcad.daemon.send_request", _track)
        result = runner.invoke(cli, ["measure", str(step), "--no-daemon"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert "via" not in parsed
        assert calls == []
