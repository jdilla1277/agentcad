"""Contract for the optional CadQuery extra.

Plan: docs/product-plans/cadquery-optional.md. The default
``pip install agentcad`` ships build123d and the OpenCascade binding only;
CadQuery lives behind ``agentcad[cadquery]``.

These tests also run in the full-profile environment where CadQuery *is*
installed, so the ``no_cadquery`` fixture simulates its absence in-process:
it makes the dispatcher's availability probe say "missing" and blocks
``import cadquery`` through ``sys.modules`` so any hidden import on a shared
command path fails loudly instead of silently working because the package
happened to be present.

The repo-level conftest skips CadQuery-flavored modules when the extra is
absent; this file must run on *both* profiles, so it carries the opt-out
marker below.
"""
# collect-on-default-profile

from __future__ import annotations

import json
import math
import sys
import tomllib
from pathlib import Path

import pytest

import agentcad
from agentcad import daemon as daemon_mod
from agentcad.cli import cli
from agentcad.runners import dispatch

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"
CQ = "cadquery"
RUNTIME_FLAG = "--runtime"

B3D_SCRIPT = "box = Box(10, 20, 5)\nshow_object(box)\n"


@pytest.fixture
def no_cadquery(monkeypatch):
    """Make CadQuery look uninstalled for one test."""
    monkeypatch.setattr(
        dispatch, "runtime_available", lambda name: name != CQ
    )
    # ``None`` in sys.modules makes importing the package raise
    # ImportError, which is what a shared path with a hidden import would
    # hit on the default profile.
    monkeypatch.setitem(sys.modules, CQ, None)


def _b3d_step(tmp_path: Path) -> Path:
    from build123d import Box, export_step

    path = tmp_path / "box.step"
    export_step(Box(10, 20, 5), str(path))
    return path


def _solid_count(shape) -> int:
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    n = 0
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        n += 1
        explorer.Next()
    return n


# ---------- packaging ----------


class TestPackaging:
    def test_default_dependencies_exclude_cadquery_and_casadi(self):
        project = tomllib.loads(PYPROJECT.read_text())["project"]
        names = [d.split(";")[0].split(">")[0].split("<")[0].split("=")[0].strip()
                 for d in project["dependencies"]]
        assert CQ not in names
        assert "casadi" not in names
        # The OpenCascade binding is still a direct dependency: agentcad
        # imports OCP itself, and build123d needs it too.
        assert "cadquery-ocp" in names

    def test_cadquery_extra_carries_the_compatibility_runtime(self):
        project = tomllib.loads(PYPROJECT.read_text())["project"]
        extra = project["optional-dependencies"][CQ]
        assert any(d.startswith("cadquery>=") for d in extra)


# ---------- dispatch + missing-extra error ----------


class TestMissingExtra:
    def test_get_runner_raises_install_hint(self, no_cadquery):
        with pytest.raises(ValueError) as exc_info:
            dispatch.get_runner(CQ)
        message = str(exc_info.value)
        assert message == dispatch.MISSING_CADQUERY_MESSAGE
        assert 'pip install "agentcad[cadquery]"' in message
        assert "daemon" in message

    def test_get_runner_does_not_gate_build123d(self, no_cadquery):
        runner = dispatch.get_runner("build123d")
        assert hasattr(runner, "execute")

    def test_run_with_runtime_flag_returns_structured_error(
        self, no_cadquery, runner, isolated_dir
    ):
        runner.invoke(cli, ["init", "--name", "proj"])
        (isolated_dir / "script.py").write_text(B3D_SCRIPT)

        result = runner.invoke(
            cli, ["run", "script.py", "--label", "first", RUNTIME_FLAG, CQ]
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["command"] == "run"
        assert data["status"] == "error"
        assert data["message"] == dispatch.MISSING_CADQUERY_MESSAGE
        assert data["suggestion"] == dispatch.PORT_TO_BUILD123D_HINT
        assert "Traceback" not in result.output
        # No version consumed, no directory created.
        assert not list(isolated_dir.glob("v1_*"))

    def test_run_in_cadquery_pinned_project_returns_structured_error(
        self, no_cadquery, runner, isolated_dir
    ):
        # ``init`` refuses to pin an absent runtime, so write the manifest
        # an older install would have produced.
        (isolated_dir / "agentcad.json").write_text(json.dumps({
            "name": "legacy", "version": "0.5.2", "created": "2026-01-01",
            "runtime": CQ, "versions": [],
        }))
        (isolated_dir / "legacy.py").write_text(
            "result = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n"
        )

        result = runner.invoke(cli, ["run", "legacy.py", "--label", "first"])

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert data["message"] == dispatch.MISSING_CADQUERY_MESSAGE

    def test_mismatch_in_build123d_project_names_the_real_blocker(
        self, no_cadquery, runner, isolated_dir
    ):
        """A CadQuery script in a build123d project must not be told to pass
        --runtime cadquery when that would only fail with "not installed"."""
        runner.invoke(cli, ["init", "--name", "proj"])
        (isolated_dir / "legacy.py").write_text(
            "result = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n"
        )

        result = runner.invoke(cli, ["run", "legacy.py", "--label", "legacy"])

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "runtime mismatch" in data["message"]
        assert dispatch.MISSING_CADQUERY_MESSAGE in data["message"]
        assert data["suggestion"] == dispatch.PORT_TO_BUILD123D_HINT

    def test_run_runtime_flag_reports_missing_extra_before_manifest(
        self, no_cadquery, runner, isolated_dir
    ):
        (isolated_dir / "legacy.py").write_text(B3D_SCRIPT)

        result = runner.invoke(
            cli, ["run", "legacy.py", "--label", "x", RUNTIME_FLAG, CQ]
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["message"] == dispatch.MISSING_CADQUERY_MESSAGE
        assert data["suggestion"] == dispatch.PORT_TO_BUILD123D_HINT
        assert not (isolated_dir / "agentcad.json").exists()

    def test_init_refuses_to_pin_absent_runtime(
        self, no_cadquery, runner, isolated_dir
    ):
        result = runner.invoke(
            cli, ["init", "--name", "legacy", RUNTIME_FLAG, CQ]
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["command"] == "init"
        assert data["status"] == "error"
        assert data["message"] == dispatch.MISSING_CADQUERY_MESSAGE
        assert not (isolated_dir / "agentcad.json").exists()

    def test_import_init_refuses_to_pin_absent_runtime(
        self, no_cadquery, runner, isolated_dir
    ):
        step = _b3d_step(isolated_dir)
        result = runner.invoke(
            cli, ["import", "--init", RUNTIME_FLAG, CQ, str(step)]
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["command"] == "import"
        assert data["status"] == "error"
        assert data["message"] == dispatch.MISSING_CADQUERY_MESSAGE
        assert not (isolated_dir / "agentcad.json").exists()

    def test_assemble_helper_gives_install_hint(self, no_cadquery):
        from build123d import Box

        from agentcad.helpers import assemble

        with pytest.raises(RuntimeError, match=r"agentcad\[cadquery\]"):
            assemble(Box(1, 1, 1).wrapped)


# ---------- shared paths work with CadQuery physically unavailable ----------


class TestBuild123dOnlyProfile:
    def test_run_default_runtime(self, no_cadquery, runner, isolated_dir):
        runner.invoke(cli, ["init", "--name", "proj"])
        (isolated_dir / "script.py").write_text(B3D_SCRIPT)

        result = runner.invoke(
            cli, ["run", "script.py", "--label", "first", "--no-preview", "--no-view"]
        )

        assert result.exit_code == 0, result.output
        # Progress heartbeats go to stderr; the JSON envelope is stdout.
        data = json.loads(result.stdout)
        assert data["status"] == "success"
        assert data["runtime"] == "build123d"
        assert abs(data["metrics"]["volume"] - 1000.0) < 1e-6

    def test_step_loader(self, no_cadquery, tmp_path):
        from agentcad.step_io import load_cad_shape

        shape = load_cad_shape(_b3d_step(tmp_path))
        assert not shape.IsNull()
        assert _solid_count(shape) == 1

    def test_step_loader_keeps_every_root_shape(self, no_cadquery, tmp_path):
        """A STEP with two top-level entities loads as one compound holding
        both. The CadQuery loader this replaced kept only the first root."""
        from build123d import Box
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

        from agentcad.step_io import load_cad_shape

        path = tmp_path / "two_roots.step"
        writer = STEPControl_Writer()
        assert writer.Transfer(Box(10, 10, 10).wrapped, STEPControl_AsIs) == IFSelect_RetDone
        assert writer.Transfer(
            Box(5, 5, 5).translate((30, 0, 0)).wrapped, STEPControl_AsIs
        ) == IFSelect_RetDone
        assert writer.Write(str(path)) == IFSelect_RetDone

        shape = load_cad_shape(path)
        assert _solid_count(shape) == 2

    def test_step_loader_failure_contracts(self, no_cadquery, tmp_path):
        from agentcad.step_io import load_cad_shape

        full = _b3d_step(tmp_path)
        truncated = tmp_path / "truncated.step"
        truncated.write_text(full.read_text()[: len(full.read_text()) // 2])
        with pytest.raises(ValueError, match="truncated.step"):
            load_cad_shape(truncated)

        header_only = tmp_path / "empty.step"
        header_only.write_text(
            "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('empty'),'2;1');\n"
            "FILE_NAME('empty','2026-01-01T00:00:00',(''),(''),'',' ',' ');\n"
            "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\nENDSEC;\nDATA;\nENDSEC;\n"
            "END-ISO-10303-21;\n"
        )
        with pytest.raises(ValueError) as exc_info:
            load_cad_shape(header_only)
        assert "no geometric shapes" in str(exc_info.value)

    def test_step_writer_round_trips(self, no_cadquery, tmp_path):
        from build123d import Box

        from agentcad.step_io import load_cad_shape, write_step_shape

        out = tmp_path / "written.step"
        write_step_shape(Box(10, 20, 5).wrapped, out)
        assert _solid_count(load_cad_shape(out)) == 1

    def test_render_inspect_measure_export(self, no_cadquery, runner, isolated_dir):
        step = _b3d_step(isolated_dir)

        render = runner.invoke(
            cli, ["render", str(step), "--view", "iso", "--no-daemon"]
        )
        assert render.exit_code == 0, render.output
        assert json.loads(render.output)["status"] == "success"

        inspect = runner.invoke(cli, ["inspect", str(step), "--no-daemon"])
        assert inspect.exit_code == 0, inspect.output
        assert json.loads(inspect.output)["status"] == "success"

        measure = runner.invoke(cli, ["measure", str(step), "--no-daemon"])
        assert measure.exit_code == 0, measure.output
        assert json.loads(measure.output)["status"] == "success"

        export = runner.invoke(
            cli, ["export", str(step), "--format", "stl", "--no-daemon"]
        )
        assert export.exit_code == 0, export.output
        assert json.loads(export.output)["status"] == "success"

    def test_import_command(self, no_cadquery, runner, isolated_dir):
        step = _b3d_step(isolated_dir)
        result = runner.invoke(
            cli, ["import", "--init", "--no-view", "--no-diff", str(step)]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "success"
        assert (isolated_dir / "v1_import" / "output.step").exists() or any(
            isolated_dir.glob("v1_*/output.step")
        )

    def test_annular_boss_helper(self, no_cadquery):
        from agentcad.metrics import compute_metrics
        from agentcad.helpers import annular_boss

        boss = annular_boss(center=(0, 0), inner_radius=5, outer_radius=10, height=4)
        volume = compute_metrics(boss)["volume"]
        expected = math.pi * (10**2 - 5**2) * 4
        assert abs(volume - expected) / expected < 1e-4


# ---------- daemon reports availability ----------


class TestDaemonReporting:
    def test_ping_reports_cadquery_available(self, no_cadquery, tmp_path):
        server = daemon_mod.DaemonServer(
            socket_path=str(tmp_path / "s.sock"), pid_path=str(tmp_path / "s.pid")
        )
        resp = server.handle_request({"type": "ping"})
        assert resp["type"] == "pong"
        assert resp["cadquery_available"] is False

    def test_ping_is_true_when_installed(self, tmp_path):
        if not dispatch.runtime_available(CQ):
            pytest.skip("cadquery extra not installed in this environment")
        server = daemon_mod.DaemonServer(
            socket_path=str(tmp_path / "s.sock"), pid_path=str(tmp_path / "s.pid")
        )
        assert server.handle_request({"type": "ping"})["cadquery_available"] is True

    def test_status_propagates_cadquery_available(self, monkeypatch, tmp_path):
        socket_path = tmp_path / "d.sock"
        socket_path.write_text("")
        pid_path = tmp_path / "d.pid"

        monkeypatch.setattr(
            daemon_mod, "_read_pid_metadata",
            lambda _p: {"pid": 4242, "instance_id": "abc"},
        )
        monkeypatch.setattr(daemon_mod, "_pid_alive", lambda _pid: True)
        monkeypatch.setattr(
            daemon_mod, "_send_request_with_peer",
            lambda *a, **k: (
                {
                    "type": "pong",
                    "version": agentcad.__version__,
                    "instance_id": "abc",
                    "cadquery_available": False,
                },
                4242,
                True,
            ),
        )

        status = daemon_mod.daemon_status(
            socket_path=str(socket_path), pid_path=str(pid_path)
        )
        assert status["running"] is True
        assert status["cadquery_available"] is False


# ---------- docs / help never imply CadQuery ships by default ----------


class TestDocsSurface:
    def test_install_docs_name_the_extra(self, runner, isolated_dir):
        result = runner.invoke(cli, ["docs", "install"])
        content = json.loads(result.output)["content"]
        assert 'pip install "agentcad[cadquery]"' in content
        assert "plus CadQuery" not in content
        assert "NOT included by default" in content

    def test_runtimes_and_pointer_docs_name_the_extra(self, runner, isolated_dir):
        for section in ("runtimes", CQ):
            result = runner.invoke(cli, ["docs", section])
            content = json.loads(result.output)["content"]
            assert 'pip install "agentcad[cadquery]"' in content, section

    def test_help_names_the_extra(self, runner, isolated_dir):
        result = runner.invoke(cli, ["--help"])
        assert 'agentcad[cadquery]' in result.output
