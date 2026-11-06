"""Real worker death, cancellation, deadlines and checkpoint reuse for inspection."""

import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import pytest
from mcp.types import CallToolResult

from agentcad.cli import cli
from agentcad.geometry_manifest import read_manifest
from agentcad.inspection_jobs import InspectionJob, JobError, _read, _write, request_report
from agentcad.inspection_report import _estimated_tokens
from agentcad.mcp import server
from agentcad.project import ProjectLayout
from tests_b3d.test_inspection_report import _baseline

REPO = Path(__file__).parents[1]
REAL = REPO / "tests/fixtures/real_world/cadgenbench_101_agentcad.step"


def _job(root, *, real=False, images=True, max_images=2):
    step, _ = _baseline(root, REAL if real else None)
    layout = ProjectLayout(root.resolve(), root.resolve())
    return InspectionJob.prepare(step, root, layout, images=images, max_images=max_images), step, layout


def _invoke(runner, args):
    result = runner.invoke(cli, [*args, "--no-daemon"])
    assert result.stdout, (result.exit_code, result.exception)
    return result, json.loads(result.stdout)


def _until(predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("Worker did not reach the expected boundary")
        time.sleep(0.03)


WORKER = r'''
import os, sys, time
from pathlib import Path
from agentcad.inspection_jobs import InspectionJob
job = InspectionJob(Path(sys.argv[1]), sys.argv[2])
mode = sys.argv[3]
original = job._checkpoint

def checkpoint(name, status, paths):
    original(name, status, paths)
    if mode == 'hold' and name == 'image:overall' and status == 'running':
        (job.directory / 'entered').touch()
        deadline = time.monotonic() + 40
        while not (job.directory / 'release').exists():
            if time.monotonic() > deadline:
                raise RuntimeError('test did not release worker')
            time.sleep(.03)
    if status == 'complete' and name == mode:
        os._exit(91)
job._checkpoint = checkpoint
job.start()
'''


def _worker(job, mode):
    return subprocess.Popen([sys.executable, "-c", WORKER, str(job.directory), job.job_id, mode],
        env={**os.environ, "PYTHONPATH": str(REPO / "src"), "AGENTCAD_DAEMON": "1"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)


@pytest.mark.parametrize("real", [False, True])
def test_default_job_and_warm_delivery_preserve_baseline_and_budget(isolated_dir, monkeypatch, real):
    job, step, layout = _job(isolated_dir, real=real)
    protected = {p: p.read_bytes() for p in (step, step.with_name("geometry_manifest.json"))}
    job.start()
    assert job.status()["status"] == "complete"
    result, prepared = job.result(text_budget=2000)
    hashes = {p: p.read_bytes() for p in job.directory.parent.parent.iterdir() if p.is_file()}

    def unexpected(*a, **kw):
        pytest.fail("Completed job repeated native work")

    monkeypatch.setattr("agentcad.inspection_report.BaselineGeometry", unexpected)
    monkeypatch.setattr("agentcad.geometry_images.BaselineGeometry", unexpected)
    same = InspectionJob.prepare(step, isolated_dir, layout, max_images=2)
    same.start()
    bounded, _ = same.result(text_budget=1000)
    assert same.job_id == job.job_id
    assert bounded["manifest_id"] == result["manifest_id"]
    assert _estimated_tokens(bounded) == bounded["budget"]["estimated_text_tokens"] <= 990
    assert {p: p.read_bytes() for p in hashes} == hashes
    assert {p: p.read_bytes() for p in protected} == protected
    assert prepared["report"].is_file() and prepared["bundle"].is_file()


@pytest.mark.parametrize("stage", ["analysis", "image:overall", "catalogue"])
def test_process_death_resumes_same_job_and_verified_completed_stages(isolated_dir, monkeypatch, stage):
    job, step, _ = _job(isolated_dir, real=True)
    worker = _worker(job, stage)
    out, err = worker.communicate(timeout=40)
    assert worker.returncode == 91, (out, err)
    stopped = job.status()
    assert stopped["status"] == "stopped" and not stopped["worker_running"]
    before = {Path(a["path"]): Path(a["path"]).read_bytes()
              for s in _read(job.directory / "state.json", job.job_id)["stages"].values()
              if s["status"] == "complete" for a in s["artifacts"]}
    registry = step.with_name("geometry_manifest.json").read_bytes()
    monkeypatch.setattr("agentcad.inspection_report.BaselineGeometry", lambda *a: pytest.fail("Repeated completed analysis"))
    if stage == "catalogue":
        monkeypatch.setattr("agentcad.geometry_images.BaselineGeometry", lambda *a: pytest.fail("Repeated finished image"))
    job.start(resume=True)
    assert job.status()["status"] == "complete"
    assert job.result()[0]["manifest_id"] == read_manifest(step)["manifest_id"]
    assert {p: p.read_bytes() for p in before} == before
    assert step.with_name("geometry_manifest.json").read_bytes() == registry


def test_wait_deadline_shared_execution_and_cancel_resume(isolated_dir, monkeypatch):
    job, step, layout = _job(isolated_dir)
    worker = _worker(job, "hold")
    try:
        _until(lambda: (job.directory / "entered").exists())
        running = job.status()
        assert running["status"] == "running" and running["worker_running"]
        state_before = _read(job.directory / "state.json", job.job_id)
        shared, result, expired = request_report(step, isolated_dir, layout, max_images=2, wait_timeout=0)
        assert shared["id"] == job.job_id and shared["worker_running"]
        assert result is None and expired
        assert _read(job.directory / "state.json", job.job_id)["run_token"] == state_before["run_token"]
        job.cancel()
        requested = job.status()
        assert requested["status"] == "running" and requested["cancel_requested"]
        assert requested["worker_running"]
        (job.directory / "release").touch()
        out, err = worker.communicate(timeout=30)
        assert worker.returncode == 0, (out, err)
        canceled = job.status()
        assert canceled["status"] == "canceled" and not canceled["worker_running"]
        completed = {Path(a["path"]): Path(a["path"]).read_bytes()
                     for s in _read(job.directory / "state.json", job.job_id)["stages"].values()
                     if s["status"] == "complete" for a in s["artifacts"]}
        assert any(p.suffix == ".png" for p in completed)
        monkeypatch.setattr("agentcad.inspection_report.BaselineGeometry", lambda *a: pytest.fail("Repeated completed analysis"))
        job.start(resume=True)
        assert job.status()["status"] == "complete"
        assert len(job.result()[0]["media"]) == 2
        assert {p: p.read_bytes() for p in completed} == completed
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.communicate(timeout=10)


@pytest.mark.parametrize("damage", ["request", "state", "result", "checkpoint"])
def test_bad_evidence_fails_explicitly_without_touching_geometry(isolated_dir, damage):
    job, step, layout = _job(isolated_dir, images=False)
    job.start()
    protected = {p: p.read_bytes() for p in (step, step.with_name("geometry_manifest.json"))}
    if damage == "checkpoint":
        report = job.result()[1]["report"]
        report.write_text("{}", encoding="utf-8")
    else:
        path = (Path(_read(job.directory / "state.json", job.job_id)["result"]["path"])
                if damage == "result" else job.directory / (damage + ".json"))
        path.write_text("{}", encoding="utf-8")
    with pytest.raises(JobError, match="unavailable|checksum|identity"):
        other = InspectionJob.open(step, isolated_dir, layout, job.job_id)
        other.status()
        other.result()
    assert {p: p.read_bytes() for p in protected} == protected


def test_queued_startup_expiry_cancel_and_resume(isolated_dir, monkeypatch):
    job, _, _ = _job(isolated_dir, images=False)
    with monkeypatch.context() as guard:
        guard.setattr("agentcad.inspection_jobs.subprocess.Popen", lambda *a, **kw: None)
        job.start(background=True)
    assert job.status()["status"] == "queued"
    with job._state() as state:
        state["launch_deadline"] = 0
    assert job.status()["status"] == "stopped"
    job.cancel()
    assert job.status()["status"] == "canceled"
    job.start(resume=True)
    assert job.status()["status"] == "complete"


def test_configuration_and_baseline_binding(isolated_dir, monkeypatch):
    job, step, layout = _job(isolated_dir, images=False)
    variant = InspectionJob.prepare(step, isolated_dir, layout, max_images=1)
    assert variant.job_id != job.job_id
    with pytest.raises(JobError, match="another baseline|project"):
        InspectionJob.open(step, isolated_dir, ProjectLayout(isolated_dir.parent, isolated_dir), job.job_id)
    monkeypatch.setattr("agentcad.inspection_jobs.IMAGE_REVISION", "next-renderer")
    with pytest.raises(JobError, match="software changed"):
        job.start(resume=True)
    upgraded = InspectionJob.prepare(step, isolated_dir, layout, images=False)
    assert upgraded.job_id != job.job_id
    step.write_bytes(step.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Baseline does not match"):
        upgraded.start()


@pytest.mark.parametrize("source_marker", [True, False])
def test_import_deadline_retry_cli_mcp_and_executable_actions(runner, isolated_dir, monkeypatch, source_marker):
    monkeypatch.setattr("agentcad.project_viewer.handoff", lambda *a, **kw: None)
    if source_marker:
        (isolated_dir / "agentcad.toml").write_text('build_dir = "CAD artifacts"\n', encoding="utf-8")
    args = ["import", str(REAL), "--init", "--no-view", "--no-diff", "--no-inspection-image",
            "--idempotency-key", "job-retry", "--inspection-wait-timeout", "0"]
    if not source_marker:
        args.extend(["--build-dir", str(isolated_dir / "CAD artifacts")])
    first, imported = _invoke(runner, args)
    assert first.exit_code == 0 and imported["core"]["status"] == "success"
    job_id = imported["inspection_job"]["id"]
    root = isolated_dir / "CAD artifacts"
    protected = {p: p.read_bytes() for p in [root / "agentcad.json", root / ".agentcad/changes/working.json",
                 Path(imported["outputs"]["step"]), Path(imported["outputs"]["geometry_manifest"])]}
    args[args.index("--inspection-wait-timeout") + 1] = "30"  # Caller wait is not part of request identity.
    retry, replay = _invoke(runner, args)
    assert retry.exit_code == 0 and replay["idempotency"]["replayed"]
    assert replay["inspection_job"]["id"] == job_id
    subdir = isolated_dir / "caller"
    subdir.mkdir()
    monkeypatch.chdir(subdir)
    wait_action = shlex.split(imported["inspection_job"]["actions"]["wait"])
    waited, complete = _invoke(runner, wait_action[1:])
    assert waited.exit_code == 0 and complete["inspection_job"]["status"] == "complete"
    assert complete["inspection"]["manifest_id"] == imported["geometry_manifest"]["manifest_id"]
    mcp = asyncio.run(server.mcp.call_tool("inspect", {
        "file": imported["outputs"]["step"], "cwd": str(subdir), "report": True,
        "job_id": job_id, "job_action": "wait", "build_dir": str(root),
    }))
    blocks = mcp.content if isinstance(mcp, CallToolResult) else mcp
    mcp_payload = mcp.structuredContent if isinstance(mcp, CallToolResult) else json.loads(blocks[0].text)
    mcp_payload.pop("_exit_code", None)
    assert mcp_payload == complete
    assert {p: p.read_bytes() for p in protected} == protected
    assert len(list((root / ".agentcad/changes/attempts").glob("*/request.json"))) == 1
    polled, status = _invoke(runner, shlex.split(imported["inspection_job"]["actions"]["status"])[1:])
    assert polled.exit_code == 0 and status["inspection_job"]["status"] == "complete"
    assert "inspection" not in status  # Polling doesn't attach the gallery.


def test_background_report_delivers_pngs_on_wait(runner, isolated_dir):
    job, step, _ = _job(isolated_dir, max_images=1)
    first, response = _invoke(runner, ["inspect", str(step), "--report", "--background", "--max-images", "1"])
    assert first.exit_code == 0
    assert response["inspection_job"]["id"] == job.job_id
    output = server.inspect(str(step), str(isolated_dir), report=True,
                            job_id=job.job_id, job_action="wait", wait_timeout=30)
    assert isinstance(output, CallToolResult), output
    assert output.structuredContent["inspection_job"]["status"] == "complete"
    assert [block.type for block in output.content] == ["text", "image"]


def test_lost_import_response_recovers_same_stopped_job(runner, isolated_dir):
    from tests_b3d.test_import_retries import _args, _worker as import_worker

    worker = import_worker(isolated_dir, "report")
    out, err = worker.communicate(timeout=40)
    assert worker.returncode == 91, (out, err)
    meta_path, = isolated_dir.glob("v1_*/meta.json")
    original_job = json.loads(meta_path.read_text())["inspection_job"]
    replayed, recovered = _invoke(runner, _args(key="process:1"))
    assert replayed.exit_code == 0 and recovered["idempotency"]["replayed"]
    assert recovered["inspection_job"]["id"] == original_job["id"]
    status, stopped = _invoke(runner, shlex.split(original_job["actions"]["status"])[1:])
    assert status.exit_code == 0 and stopped["inspection_job"]["status"] == "stopped"
    resumed, complete = _invoke(runner, shlex.split(original_job["actions"]["resume"])[1:])
    assert resumed.exit_code == 0 and complete["inspection_job"]["status"] == "complete"
    assert complete["inspection_job"]["id"] == original_job["id"]
    manifest = json.loads((isolated_dir / "agentcad.json").read_text())
    assert len(manifest["versions"]) == 1


def test_partial_image_failure_resumes_only_missing_image(isolated_dir, monkeypatch):
    from agentcad import geometry_images, render

    job, _, _ = _job(isolated_dir)
    original = geometry_images.geometry_image
    failed = []

    def fail_sample(*a, **kw):
        if kw.get("object_id") is not None and not failed:
            failed.append(kw["object_id"])
            raise RuntimeError("sample renderer stopped")
        return original(*a, **kw)

    with monkeypatch.context() as fail:
        fail.setattr(geometry_images, "geometry_image", fail_sample)
        job.start()
    assert job.status()["status"] == "failed"
    partial, _ = job.result()
    assert partial["media_summary"]["failed"] == 1
    old_catalogue = Path(partial["inventory"]["bundle"])
    old_bytes = old_catalogue.read_bytes()
    original_render = render.render_geometry_sheet
    calls = []

    def counted(*a, **kw):
        calls.append(True)
        return original_render(*a, **kw)

    monkeypatch.setattr(render, "render_geometry_sheet", counted)
    monkeypatch.setattr("agentcad.inspection_report.BaselineGeometry", lambda *a: pytest.fail("Repeated analysis"))
    job.start(resume=True)
    assert job.status()["status"] == "complete"
    assert len(calls) == 1
    assert len(job.result()[0]["media"]) == 2
    assert old_catalogue.read_bytes() == old_bytes


def test_script_generated_baseline_uses_same_jobs(runner, isolated_dir):
    (isolated_dir / "part.py").write_text("from build123d import Box\nshow_object(Box(10, 20, 30))\n", encoding="utf-8")
    initialized = runner.invoke(cli, ["init", "--runtime", "build123d"])
    assert initialized.exit_code == 0
    built, output = _invoke(runner, ["run", "part.py", "--label", "box", "--no-preview", "--no-view", "--no-diff"])
    assert built.exit_code == 0, output
    step = isolated_dir / output["outputs"]["step"]
    manifest = step.with_name("geometry_manifest.json").read_bytes()
    started, response = _invoke(runner, ["inspect", str(step), "--report", "--no-images", "--background"])
    assert started.exit_code == 0
    waited, result = _invoke(runner, shlex.split(response["inspection_job"]["actions"]["wait"])[1:])
    assert waited.exit_code == 0
    assert result["inspection"]["quality"]["status"] == "available"
    assert result["inspection"]["provenance"]["kind"] == "script"
    assert result["inspection"]["manifest_id"] == output["geometry_manifest"]["manifest_id"]
    assert step.with_name("geometry_manifest.json").read_bytes() == manifest


@pytest.mark.parametrize("controls", [
    ["--background"], ["--job-action", "resume"],
    ["--report", "--background", "--wait-timeout", "1"],
    ["--report", "--job", "inspection_bad", "--no-images"],
    ["--report", "--job", "inspection_bad", "--background"],
])
def test_invalid_job_controls_do_not_start_work(runner, isolated_dir, controls):
    result = runner.invoke(cli, ["inspect", "missing.step", *controls, "--no-daemon"])
    assert result.exit_code != 0
    assert not list(isolated_dir.rglob("request.json"))


def test_mcp_job_controls_are_thin_cli_arguments(monkeypatch):
    calls = []

    def invoked(args, cwd=None):
        calls.append((args, cwd))
        return {"status": "running"}

    monkeypatch.setattr(server, "_invoke", invoked)
    server.inspect("part.step", "/project", report=True, job_id="inspection_ID",
                   job_action="resume", background=True, build_dir="CAD files")
    assert calls == [(["inspect", "part.step", "--report", "--background", "--job", "inspection_ID",
                       "--job-action", "resume", "--max-images", "9", "--text-budget", "2000",
                       "--build-dir", "CAD files"], "/project")]


@pytest.mark.parametrize("command,controls", [
    ("import", ["--inspection-background", "--idempotency-key", "forward"]),
    ("import", ["--inspection-wait-timeout", "0.25"]),
    ("inspect", ["--report", "--background"]),
    ("inspect", ["--report", "--job", "inspection_ID", "--job-action", "resume", "--wait-timeout", "0.25"]),
])
def test_daemon_routes_job_controls_before_starting_work(runner, isolated_dir, monkeypatch, command, controls):
    import importlib

    forwarded = []

    def routed(argv, **kw):
        forwarded.extend(argv)
        raise SystemExit(0)

    module = importlib.import_module("agentcad.commands." + ("import_cmd" if command == "import" else "inspect_cmd"))
    monkeypatch.setattr(module, "maybe_route_through_daemon", routed)
    invoked = runner.invoke(cli, [command, "missing.step", *controls])
    assert invoked.exit_code == 0
    for value in controls:
        assert value in forwarded
    assert not list(isolated_dir.rglob("request.json"))


@pytest.mark.parametrize("record", ["request", "state", "result"])
def test_valid_record_from_another_job_cannot_rebind_identity(isolated_dir, record):
    job, step, layout = _job(isolated_dir, images=False)
    job.start()
    other = InspectionJob.prepare(step, isolated_dir, layout, images=False, max_images=1)
    other.start()
    if record == "result":
        with job._state() as state:
            state["result"] = _read(other.directory / "state.json", other.job_id)["result"]
    else:
        (job.directory / (record + ".json")).write_bytes((other.directory / (record + ".json")).read_bytes())
    with pytest.raises(JobError, match="identity|checksum"):
        recovered = InspectionJob.open(step, isolated_dir, layout, job.job_id)
        recovered.status()
        recovered.result()


def test_missing_request_is_not_silently_reissued(isolated_dir):
    job, step, layout = _job(isolated_dir, images=False)
    job.start()
    (job.directory / "request.json").unlink()
    with pytest.raises(JobError, match="request is missing"):
        InspectionJob.prepare(step, isolated_dir, layout, images=False, max_images=2)


@pytest.mark.parametrize("real", [False, True])
@pytest.mark.parametrize("configured", [False, True])
def test_external_baseline_returned_actions_keep_original_job_root(runner, isolated_dir, monkeypatch, real, configured):
    caller, source = isolated_dir / "caller A", isolated_dir / "geometry B"
    caller.mkdir()
    source.mkdir()
    step, saved = _baseline(source, REAL if real else None)
    monkeypatch.chdir(caller)
    args = ["inspect", str(step), "--report", "--no-images", "--wait-timeout", "30"]
    if configured:
        args.extend(["--build-dir", str(caller)])
    invoked, first = _invoke(runner, args)
    assert invoked.exit_code == 0, first
    job = first["inspection_job"]
    request_path = Path(job["request"])
    expected_root = request_path.parents[3]
    assert expected_root != source if configured else expected_root == source
    protected = {p: p.read_bytes() for p in [step, step.with_name("geometry_manifest.json"), request_path]}
    protected.update({p: p.read_bytes() for p in expected_root.joinpath("inspection").glob("*.json")})

    def unexpected(*a, **kw):
        pytest.fail("A completed external job repeated analysis")

    monkeypatch.setattr("agentcad.inspection_report._analysis", unexpected)
    for action in ("status", "wait", "cancel", "resume"):
        command = shlex.split(job["actions"][action])
        assert command[command.index("--build-dir") + 1] == str(caller)
        result, payload = _invoke(runner, command[1:])
        assert result.exit_code == 0, payload
        assert payload["inspection_job"]["id"] == job["id"]
        assert payload["inspection_job"]["request"] == str(request_path)
        assert payload["inspection_job"]["status"] == "complete"
        if action in {"wait", "resume"}:
            assert Path(payload["inspection"]["inventory"]["image_root"]) == expected_root / "inspection"
            assert payload["inspection"]["manifest_id"] == saved["manifest_id"]
        else:
            assert "inspection" not in payload
    # A registered MCP call uses that same explicit root/location too.
    response = asyncio.run(server.mcp.call_tool("inspect", {
        "file": str(step), "cwd": str(caller), "report": True,
        "job_id": job["id"], "job_action": "wait", "build_dir": str(caller),
    }))
    assert not isinstance(response, CallToolResult)  # Image-free job.
    mcp_payload = json.loads(response[0].text)
    assert mcp_payload["_exit_code"] == 0
    assert mcp_payload["inspection_job"]["request"] == str(request_path)
    assert mcp_payload["inspection_job"]["id"] == job["id"]
    assert {p: p.read_bytes() for p in protected} == protected
    assert len(list(isolated_dir.rglob("request.json"))) == 1


def test_stopped_external_job_actions_resume_and_preserve_bindings(runner, isolated_dir, monkeypatch):
    caller, source = isolated_dir / "caller", isolated_dir / "external"
    caller.mkdir()
    source.mkdir()
    step, _ = _baseline(source, REAL)
    monkeypatch.chdir(caller)
    layout = ProjectLayout(caller, caller)
    job = InspectionJob.prepare(step, source, layout, images=False)
    actions = job.status()["actions"]
    protected = {p: p.read_bytes() for p in [step, step.with_name("geometry_manifest.json"), job.directory / "request.json"]}
    for action, state, exit_code in [("status", "stopped", 0), ("wait", "stopped", 1),
                                     ("cancel", "canceled", 0), ("resume", "complete", 0)]:
        result, payload = _invoke(runner, shlex.split(actions[action])[1:])
        assert result.exit_code == exit_code, payload
        assert payload["inspection_job"]["status"] == state
        assert payload["inspection_job"]["id"] == job.job_id
    assert {p: p.read_bytes() for p in protected} == protected
    assert len(list(isolated_dir.rglob("request.json"))) == 1
    absent_derived = caller / "derived" / "inspect" / "missing"
    # The standalone fallback must still reject another source project/build
    # root or a different baseline, even if it can find this valid request.
    with pytest.raises(JobError, match="another baseline or project/build root"):
        InspectionJob.open(step, absent_derived, ProjectLayout(source, source), job.job_id)
    with pytest.raises(JobError, match="another baseline or project/build root"):
        InspectionJob.open(step.with_name("different.step"), absent_derived, layout, job.job_id)
    # A valid copy in an incorrect output location cannot override the original.
    misplaced = absent_derived / "inspection" / "jobs" / job.job_id
    misplaced.mkdir(parents=True)
    (misplaced / "request.json").write_bytes((job.directory / "request.json").read_bytes())
    with pytest.raises(JobError, match="another baseline or project/build root"):
        InspectionJob.open(step, absent_derived, layout, job.job_id)


@pytest.mark.parametrize('terminal', ['complete', 'resource_limit'])
def test_full_wait_finishes_execution_handoff_but_deadline_returns_early(isolated_dir, terminal):
    from concurrent.futures import ThreadPoolExecutor

    job, _, _ = _job(isolated_dir, images=False)
    with job._state() as state:
        state['status'] = terminal
    ready, release = isolated_dir / 'owner-ready', isolated_dir / 'owner-release'
    holder = r'''
import sys, time
from pathlib import Path
from agentcad.import_requests import _locked
with _locked(Path(sys.argv[1]) / 'execution.lock') as owned:
    assert owned
    Path(sys.argv[2]).write_text('owned')
    while not Path(sys.argv[3]).exists():
        time.sleep(0.01)
'''
    child = subprocess.Popen([sys.executable, '-c', holder, str(job.directory), str(ready), str(release)],
                             env={**os.environ, 'PYTHONPATH': str(REPO / 'src')},
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        _until(ready.exists)
        early, expired = job.wait(0)
        assert expired and early['worker_running'] and early['status'] == terminal
        with ThreadPoolExecutor(max_workers=1) as pool:
            full = pool.submit(job.wait, 5)
            try:
                with pytest.raises(TimeoutError):
                    full.result(timeout=0.2)
            finally:
                release.write_text('finish')
            finished, expired = full.result(timeout=10)
        assert not expired and finished['status'] == terminal and not finished['worker_running']
    finally:
        release.write_text('finish')
        child.wait(timeout=10)
        if child.stderr:
            child.stderr.close()
    assert child.returncode == 0
