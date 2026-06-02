import json

import cadquery as cq
from cadquery import exporters

from agentcad.cli import cli


def _make_step(directory, name="output.step"):
    box = cq.Workplane("XY").box(10, 10, 10)
    path = directory / name
    exporters.export(box, str(path))
    return path


class TestInspectCommand:
    def test_inspect_returns_json(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed["command"] == "inspect"
        assert parsed["status"] == "success"

    def test_inspect_reports_solid_count(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert parsed["solid_count"] == 1

    def test_inspect_reports_shell_count(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert parsed["shell_count"] == 1

    def test_inspect_reports_face_count(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert parsed["face_count"] == 6

    def test_inspect_reports_edge_count(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert parsed["edge_count"] == 12

    def test_inspect_shell_details(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        shells = parsed["shells"]
        assert len(shells) == 1
        assert shells[0]["closed"] is True
        assert shells[0]["face_count"] == 6

    def test_inspect_face_orientations(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        orient = parsed["face_orientations"]
        assert orient["forward"] + orient["reversed"] == 6

    def test_inspect_missing_file_error(self, runner, isolated_dir):
        result = runner.invoke(cli, ["inspect", "nope.step"])
        assert result.exit_code == 1
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "error"

    def test_inspect_compound(self, runner, isolated_dir):
        """Compound with 2 solids should report correctly."""
        box1 = cq.Workplane("XY").box(10, 10, 10)
        box2 = cq.Workplane("XY").workplane(offset=20).box(5, 5, 5)
        compound = cq.Compound.makeCompound([box1.val(), box2.val()])
        wp = cq.Workplane("XY").newObject([compound])
        step = isolated_dir / "compound.step"
        exporters.export(wp, str(step))

        result = runner.invoke(cli, ["inspect", str(step)])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed["solid_count"] == 2
        assert parsed["shell_count"] == 2

    def test_inspect_is_valid(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert parsed["is_valid"] is True

    def test_inspect_free_edge_count(self, runner, isolated_dir):
        """A closed box has zero free edges."""
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert parsed["free_edge_count"] == 0


# --- Daemon routing (#177) ---

class TestInspectDaemonRouting:
    """`agentcad inspect` routes through the daemon when one is running,
    same contract as `agentcad run`. Without this, a workflow that runs
    `run` → `inspect` pays the OCP startup cost twice."""

    def test_inspect_routes_through_daemon_when_available(self, runner, isolated_dir, monkeypatch):
        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
        daemon_output = json.dumps({
            "command": "inspect",
            "status": "success",
            "solid_count": 1, "shell_count": 1,
            "face_count": 6, "edge_count": 12, "is_valid": True,
        })
        monkeypatch.setattr(
            "agentcad.daemon.send_request",
            lambda *a, **kw: {"type": "result", "exit_code": 0, "output": daemon_output},
        )
        result = runner.invoke(cli, ["inspect", "any.step"])
        assert result.exit_code == 0, result.stdout
        parsed = json.loads(result.stdout)
        assert parsed["via"] == "daemon"
        assert parsed["command"] == "inspect"

    def test_inspect_direct_no_via_field(self, runner, isolated_dir):
        step = _make_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert "via" not in parsed

    def test_inspect_no_daemon_flag_skips_routing(self, runner, isolated_dir, monkeypatch):
        step = _make_step(isolated_dir)
        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
        calls: list[tuple] = []
        def _track(*a, **kw):
            calls.append((a, kw))
            return None
        monkeypatch.setattr("agentcad.daemon.send_request", _track)
        result = runner.invoke(cli, ["inspect", str(step), "--no-daemon"])
        assert result.exit_code == 0, result.stdout
        parsed = json.loads(result.stdout)
        assert "via" not in parsed
        assert calls == [], f"send_request was called despite --no-daemon: {calls}"
