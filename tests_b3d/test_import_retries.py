"""Retry identity survives response loss, competing callers and process death."""

import asyncio
from copy import deepcopy
import hashlib
import importlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import click
import pytest
from mcp.types import CallToolResult

from agentcad.cli import cli
from agentcad.geometry_manifest import load_geometry
from agentcad.mcp import server

REPO = Path(__file__).parents[1]
REAL_SOURCE = REPO / "tests/fixtures/real_world/cadgenbench_101_agentcad.step"


@pytest.fixture(autouse=True)
def no_viewer_service(monkeypatch):
    monkeypatch.setattr("agentcad.project_viewer.handoff", lambda *a, **kw: None)


def _args(source=REAL_SOURCE, key="supplier:1", *options):
    return ["import", str(source), "--init", "--no-view", "--no-diff",
            "--no-daemon", "--no-inspection-image", "--idempotency-key", key, *options]


def _invoke(runner, args):
    result = runner.invoke(cli, args)
    assert result.stdout, (result.exit_code, result.exception)
    return result, json.loads(result.stdout)


def _requests(root):
    return list((root / ".agentcad/import_requests").glob("*/request.json"))


def _attempts(root):
    return list((root / ".agentcad/changes/attempts").glob("*/request.json"))


def _history(root):
    return json.loads((root / "agentcad.json").read_text())["versions"]


def test_lost_response_replays_without_new_version_or_side_effects(runner, isolated_dir, monkeypatch):
    original_echo = click.echo

    def lose_response(message=None, **kwargs):
        if message and not kwargs.get("err") and json.loads(message).get("command") == "import":
            raise KeyboardInterrupt("caller lost response")
        original_echo(message, **kwargs)

    monkeypatch.setattr(click, "echo", lose_response)
    first = runner.invoke(cli, _args())
    assert first.exit_code != 0 and first.stdout == ""
    assert len(_history(isolated_dir)) == len(_attempts(isolated_dir)) == 1
    request = _requests(isolated_dir)[0]
    recorded = json.loads(request.with_name("response.json").read_text())["payload"]
    manifest_before = (isolated_dir / "agentcad.json").read_bytes()
    working = isolated_dir / ".agentcad/changes/working.json"
    working_before = working.read_bytes()
    monkeypatch.setattr(click, "echo", original_echo)

    def unexpected(*a, **kw):
        pytest.fail("Replay attempted new CAD/viewer work")

    monkeypatch.setattr("agentcad.commands.import_cmd._import_impl", unexpected)
    monkeypatch.setattr("agentcad.project_viewer.record_failure", unexpected)
    result, replay = _invoke(runner, _args())
    assert result.exit_code == 0
    expected = deepcopy(recorded)
    expected["idempotency"]["replayed"] = True
    expected["viewer_opened"] = False
    for field, value in recorded.items():
        assert replay[field] == expected[field]
    assert replay["version"] == 1
    assert (isolated_dir / "agentcad.json").read_bytes() == manifest_before
    assert working.read_bytes() == working_before
    assert len(_attempts(isolated_dir)) == 1
    geometry = load_geometry(isolated_dir / replay["outputs"]["step"])
    assert geometry.manifest_id == replay["geometry_manifest"]["manifest_id"]


@pytest.mark.parametrize("change", ["bytes", "filename", "label", "profile", "runtime", "images", "text", "diff", "view"])
def test_conflicting_key_never_changes_history(runner, isolated_dir, change):
    source = isolated_dir / "supplier.step"
    source.write_bytes(REAL_SOURCE.read_bytes())
    args = _args(source)
    assert _invoke(runner, args)[0].exit_code == 0
    before = (isolated_dir / "agentcad.json").read_bytes()
    if change == "bytes":
        source.write_bytes(source.read_bytes() + b"\n")
    elif change == "filename":
        source = isolated_dir / "renamed.step"
        source.write_bytes(REAL_SOURCE.read_bytes())
        args[1] = str(source)
    else:
        args.extend({"label": ["--label", "another"], "profile": ["--validation-profile", "kernel"],
                     "runtime": ["--runtime", "cadquery"], "images": ["--max-images", "1"],
                     "text": ["--text-budget", "1600"], "diff": ["--diff"], "view": ["--view"]}[change])
    result, payload = _invoke(runner, args)
    assert result.exit_code == 1
    assert payload["reason"] == "idempotency_conflict"
    assert (isolated_dir / "agentcad.json").read_bytes() == before
    assert len(_attempts(isolated_dir)) == 1


def test_new_key_deliberately_imports_same_bytes_again(runner, isolated_dir):
    first, one = _invoke(runner, _args(key="one"))
    second, two = _invoke(runner, _args(key="two"))
    assert first.exit_code == second.exit_code == 0
    assert (one["version"], two["version"]) == (1, 2)
    assert len(_history(isolated_dir)) == len(_attempts(isolated_dir)) == 2


def test_replaying_old_request_preserves_newer_work(runner, isolated_dir):
    assert _invoke(runner, _args(key="one"))[0].exit_code == 0
    assert _invoke(runner, _args(REAL_SOURCE, "two", "--label", "newer"))[0].exit_code == 0
    scaffold = isolated_dir / "edit.py"
    scaffold.write_text("# agent's ongoing edit\n", encoding="utf-8")
    manifest = (isolated_dir / "agentcad.json").read_bytes()
    selection = isolated_dir / ".agentcad/changes/working.json"
    working = selection.read_bytes()
    result, replay = _invoke(runner, _args(key="one"))
    assert result.exit_code == 0 and replay["version"] == 1
    assert (isolated_dir / "agentcad.json").read_bytes() == manifest
    assert selection.read_bytes() == working
    assert scaffold.read_text() == "# agent's ongoing edit\n"


def test_runtime_bootstrap_replay_and_build_root_scope(runner, isolated_dir):
    args = _args(REAL_SOURCE, "bootstrap", "--runtime", "build123d", "--build-dir", "build one")
    first, one = _invoke(runner, args)
    second, replay = _invoke(runner, args)
    assert first.exit_code == second.exit_code == 0
    assert replay["idempotency"]["replayed"] is True
    assert replay["outputs"] == one["outputs"]
    args[-1] = "build two"
    third, two = _invoke(runner, args)
    assert third.exit_code == 0 and two["version"] == 1
    assert two["idempotency"]["replayed"] is False
    assert two["outputs"]["step"] != one["outputs"]["step"]


@pytest.mark.parametrize("failure", ["malformed", "invalid", "runtime"])
def test_terminal_errors_replay_with_original_exit_code(runner, isolated_dir, failure):
    source = REAL_SOURCE
    if failure == "malformed":
        source = isolated_dir / "bad.step"
        source.write_text("not CAD", encoding="utf-8")
    elif failure == "invalid":
        source = REPO / "tests/fixtures/validation/open_shell.step"
    args = _args(source)
    if failure == "runtime":
        args.remove("--init")
        args.extend(["--runtime", "build123d"])
    first, one = _invoke(runner, args)
    second, replay = _invoke(runner, args)
    assert first.exit_code == second.exit_code == 1
    assert one["status"] == replay["status"]
    assert replay["idempotency"]["replayed"]
    assert replay.get("change") == one.get("change")
    assert len(_attempts(isolated_dir)) == (0 if failure == "runtime" else 1)
    if failure == "invalid":
        assert len(_history(isolated_dir)) == 1
        assert replay["version"] == one["version"]


@pytest.mark.parametrize("key", ["", "../escape", "has space", "a" * 129])
def test_invalid_keys_fail_before_cad(runner, isolated_dir, key):
    result, payload = _invoke(runner, _args(key=key))
    assert result.exit_code == 1 and payload["reason"] == "invalid_idempotency_key"
    assert not (isolated_dir / "agentcad.json").exists()
    assert not _requests(isolated_dir)


def test_source_is_frozen_before_parser_reads_it(runner, isolated_dir, monkeypatch):
    from agentcad.import_requests import ImportRequest

    source = isolated_dir / "supplier.step"
    original = REAL_SOURCE.read_bytes()
    source.write_bytes(original)
    run = ImportRequest.run

    def mutate(self, operation):
        source.write_text("replaced while importing", encoding="utf-8")
        return run(self, operation)

    monkeypatch.setattr(ImportRequest, "run", mutate)
    result, payload = _invoke(runner, _args(source))
    assert result.exit_code == 0
    assert payload["original_filename"] == "supplier.step"
    assert payload["sha256"] == hashlib.sha256(original).hexdigest()
    assert (isolated_dir / payload["outputs"]["source"]).read_bytes() == original


def test_reply_cache_write_failure_recovers_terminal_attempt(runner, isolated_dir, monkeypatch):
    from agentcad import import_requests

    original = import_requests._save

    def broken(path, body):
        if path.name == "response.json":
            raise OSError("temporary reply cache failure")
        return original(path, body)

    monkeypatch.setattr(import_requests, "_save", broken)
    first, one = _invoke(runner, _args())
    assert first.exit_code == 0 and one["idempotency"]["state"] == "recording_failed"
    monkeypatch.setattr(import_requests, "_save", original)
    second, replay = _invoke(runner, _args())
    assert second.exit_code == 0 and replay["idempotency"]["state"] == "recovered"
    assert replay["version"] == one["version"] == 1
    assert len(_attempts(isolated_dir)) == len(_history(isolated_dir)) == 1


def test_outer_error_handler_cannot_finalize_after_unlock(runner, isolated_dir, monkeypatch):
    from agentcad import import_requests
    from agentcad.project import ProjectError

    complete = import_requests.ImportRequest.complete
    calls = []
    monkeypatch.setattr(import_requests, "_LOCK_WAIT_SECONDS", 0)

    def broken(self, payload, exit_code):
        with import_requests._locked(self.directory / "execution.lock") as acquired:
            assert not acquired, "Request finalization ran outside execution ownership"
        calls.append(True)
        raise ProjectError("retry_record_error", "Injected record failure", "Retry the original request.")

    monkeypatch.setattr(import_requests.ImportRequest, "complete", broken)
    first, failed = _invoke(runner, _args())
    assert first.exit_code == 1 and failed["reason"] == "retry_record_error"
    assert len(calls) == 2  # Original reply + handled error, both under lock.
    monkeypatch.setattr(import_requests.ImportRequest, "complete", complete)
    second, recovered = _invoke(runner, _args())
    assert second.exit_code == 0 and recovered["version"] == 1
    assert recovered["idempotency"]["state"] == "recovered"
    assert len(_attempts(isolated_dir)) == len(_history(isolated_dir)) == 1


def test_key_is_forwarded_before_daemon_execution(runner, isolated_dir, monkeypatch):
    requests = []
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    # This test simulates supported transport on Windows too; its socket
    # location must not fall through to the POSIX-only getuid default.
    monkeypatch.setenv("AGENTCAD_SOCKET_PATH", str(isolated_dir / "daemon.sock"))
    monkeypatch.setattr("agentcad.daemon.daemon_supported", lambda: True)

    def sent(request, **kwargs):
        requests.append(request)
        return {"exit_code": 0, "output": json.dumps({"command": "import", "status": "success"})}

    monkeypatch.setattr("agentcad.daemon.send_request", sent)
    args = _args()
    args.remove("--no-daemon")
    result, payload = _invoke(runner, args)
    assert result.exit_code == 0 and payload["via"] == "daemon"
    assert requests[0]["argv"][-2:] == ["--idempotency-key", "supplier:1"]
    assert not _requests(isolated_dir)


def test_daemon_spawn_happens_after_request_unlock(runner, isolated_dir, monkeypatch):
    from agentcad import import_requests

    command = importlib.import_module("agentcad.commands.import_cmd")
    spawned = []
    monkeypatch.setattr(import_requests, "_LOCK_WAIT_SECONDS", 0)

    def spawn(**kwargs):
        request = click.get_current_context().meta["import_request"]
        with import_requests._locked(request.directory / "execution.lock") as acquired:
            assert acquired, "Daemon spawn inherited a held request lock"
        spawned.append(True)

    monkeypatch.setattr(command, "maybe_spawn_daemon_for_next_run", spawn)
    assert _invoke(runner, _args())[0].exit_code == 0
    assert spawned == [True]
    assert _invoke(runner, _args())[0].exit_code == 0
    assert spawned == [True]  # A replay must not warm/fork another daemon.


@pytest.mark.parametrize("record", ["request.json", "response.json", "source.step"])
def test_damaged_retry_evidence_never_starts_a_new_import(runner, isolated_dir, record):
    assert _invoke(runner, _args())[0].exit_code == 0
    request = _requests(isolated_dir)[0]
    request.with_name(record).write_text("damaged", encoding="utf-8")
    result, payload = _invoke(runner, _args())
    assert result.exit_code == 1 and payload["reason"] == "import_request_unavailable"
    assert len(_history(isolated_dir)) == len(_attempts(isolated_dir)) == 1


@pytest.mark.parametrize("record", ["request.json", "response.json", "attempt.json"])
def test_valid_record_from_another_key_cannot_retarget_a_request(runner, isolated_dir, record):
    first, one = _invoke(runner, _args(key="one"))
    second, two = _invoke(runner, _args(key="two"))
    assert first.exit_code == second.exit_code == 0
    target = Path(one["idempotency"]["request"]).with_name(record)
    other = Path(two["idempotency"]["request"]).with_name(record)
    target.write_bytes(other.read_bytes())  # Valid checksum, same source/options.
    if record == "attempt.json":
        target.with_name("response.json").unlink()  # Exercise retained recovery.
    before = (isolated_dir / "agentcad.json").read_bytes()
    result, failed = _invoke(runner, _args(key="one"))
    assert result.exit_code == 1 and failed["reason"] == "import_request_unavailable"
    assert (isolated_dir / "agentcad.json").read_bytes() == before
    assert len(_history(isolated_dir)) == len(_attempts(isolated_dir)) == 2


def test_cli_and_registered_mcp_share_retry_and_checked_images(runner, isolated_dir, monkeypatch):
    project = isolated_dir / "source project"
    project.mkdir()
    monkeypatch.chdir(project)
    args = _args(REAL_SOURCE, "shared", "--inspection-image", "--max-images", "1", "--build-dir", "build files")
    result, first = _invoke(runner, args)
    assert result.exit_code == 0
    payload = asyncio.run(server.mcp.call_tool("import_cad", {
        "file": str(REAL_SOURCE), "cwd": str(project), "initialize": True,
        "view": False, "diff": False, "max_images": 1,
        "build_dir": "build files", "idempotency_key": "shared",
    }))
    assert isinstance(payload, CallToolResult) and len(payload.content) == 2
    replay = payload.structuredContent
    assert replay["idempotency"]["replayed"] and replay["_exit_code"] == 0
    assert replay["inspection"] == first["inspection"]
    assert replay["change"] == first["change"]
    assert len(_history(project / "build files")) == 1


def test_default_root_replay_from_subdirectory_resolves_media(runner, isolated_dir, monkeypatch):
    first = server.import_cad(str(REAL_SOURCE), str(isolated_dir), initialize=True,
                              view=False, diff=False, max_images=1, idempotency_key="subdir")
    assert isinstance(first, CallToolResult)
    subdir = isolated_dir / "caller"
    subdir.mkdir()
    replay = server.import_cad(str(REAL_SOURCE), str(subdir), initialize=True,
                               view=False, diff=False, max_images=1, idempotency_key="subdir")
    assert isinstance(replay, CallToolResult) and len(replay.content) == 2
    assert replay.content[1].data == first.content[1].data
    assert replay.structuredContent["idempotency"]["replayed"]
    assert Path(replay.structuredContent["inspection"]["inventory"]["image_root"]).is_absolute()
    assert len(_history(isolated_dir)) == 1


# Real processes prove OS lock lifetime and persisted recovery; no CLI thread
# tests (Click and cwd are process globals). The commit hook kills at the exact
# boundary after registration, before any request-side completion is recorded.
WORKER = r'''
import importlib, json, os, sys, time
from pathlib import Path
from agentcad.cli import cli
import agentcad.import_requests as requests
import agentcad.project_viewer as viewer
import agentcad.versioning as versioning

viewer.handoff = lambda *a, **kw: None
mode, source = sys.argv[1:3]
if mode == "busy":
    requests._LOCK_WAIT_SECONDS = 0.1
elif mode == "precommit" or mode == "postcommit":
    original = versioning.commit_version
    def commit(*a, **kw):
        if mode == "postcommit":
            original(*a, **kw)
        os._exit(91)
    versioning.commit_version = commit
elif mode == "report":
    import agentcad.inspection_report as report
    report.inspection_report = lambda *a, **kw: os._exit(91)
elif mode == "response":
    original = requests._save
    def save(path, body):
        if path.name == "response.json":
            os._exit(91)
        return original(path, body)
    requests._save = save
elif mode == "hold":
    command = importlib.import_module("agentcad.commands.import_cmd")
    original = command._export_step
    def export(*a, **kw):
        Path("entered").touch()
        deadline = time.monotonic() + 40
        while not Path("release").exists():
            if time.monotonic() > deadline:
                raise RuntimeError("test release missing")
            time.sleep(0.03)
        return original(*a, **kw)
    command._export_step = export

cli.main(args=["import", source, "--init", "--no-view", "--no-diff",
               "--no-daemon", "--no-inspection-image", "--idempotency-key", "process:1"])
'''


def _worker(root, mode):
    return subprocess.Popen([sys.executable, "-c", WORKER, mode, str(REAL_SOURCE)], cwd=root,
                            env={**os.environ, "PYTHONPATH": str(REPO / "src"), "AGENTCAD_DAEMON": "1"},
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _communicate(process, code):
    out, err = process.communicate(timeout=60)
    assert process.returncode in (code if isinstance(code, tuple) else (code,)), (out, err)
    return json.loads(out) if out else None


@pytest.mark.parametrize("stage", ["precommit", "postcommit", "report", "response"])
def test_process_death_recovers_binding_without_reimport(runner, isolated_dir, stage):
    worker = _worker(isolated_dir, stage)
    _communicate(worker, 91)
    before_dirs = {p.name for p in isolated_dir.glob("v*") if p.is_dir()}
    before_attempts = _attempts(isolated_dir)
    result, replay = _invoke(runner, _args(REAL_SOURCE, "process:1"))
    assert replay["idempotency"]["replayed"]
    assert {p.name for p in isolated_dir.glob("v*") if p.is_dir()} == before_dirs
    assert _attempts(isolated_dir) == before_attempts
    if stage == "precommit":
        assert result.exit_code == 1
        assert replay["reason"] == "import_interrupted"
        assert replay["idempotency"]["state"] == "interrupted"
        assert _history(isolated_dir) == []
    else:
        assert result.exit_code == 0 and replay["version"] == 1
        assert replay["idempotency"]["state"] == "recovered"
        assert len(_history(isolated_dir)) == 1
        assert (isolated_dir / replay["outputs"]["step"]).is_file()
        if stage != "response":
            assert any(a["status"] == "interrupted" for a in replay["artifacts"].values())
        if stage in {"report", "response"}:
            assert (isolated_dir / "edit.py").is_file()
            assert load_geometry(isolated_dir / replay["outputs"]["step"])
        if stage in {"postcommit", "report"}:
            action, inspected = _invoke(runner, shlex.split(replay["next_actions"][0])[1:])
            assert action.exit_code == 0 and inspected["status"] == "success"
            meta = json.loads((isolated_dir / replay["path"] / "meta.json").read_text())
            assert not any(a["status"] == "pending" for a in meta["artifacts"].values())
    assert replay["change"]["status"] == "completed"
    # Recovery itself is cached and never replays CAD on the third call.
    third, repeated = _invoke(runner, _args(REAL_SOURCE, "process:1"))
    assert third.exit_code == result.exit_code
    assert repeated["idempotency"]["state"] == replay["idempotency"]["state"]


@pytest.mark.parametrize("busy", [False, True])
def test_concurrent_retries_share_one_import(isolated_dir, busy):
    first = _worker(isolated_dir, "hold")
    second = None
    try:
        deadline = time.monotonic() + 30
        while not (isolated_dir / "entered").exists():
            assert first.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.03)
        second = _worker(isolated_dir, "busy" if busy else "normal")
        if busy:
            waiting = _communicate(second, 1)
            assert waiting["status"] == waiting["idempotency"]["state"] == "in_progress"
            assert waiting["idempotency"]["replayed"]
            assert first.poll() is None
        else:
            time.sleep(0.4)
            assert second.poll() is None
        assert len(_attempts(isolated_dir)) == 1
        (isolated_dir / "release").touch()
        one = _communicate(first, 0)
        if busy:
            two = _communicate(_worker(isolated_dir, "normal"), 0)
        else:
            two = _communicate(second, (0, 1))
            if second.returncode == 1:
                # Rendering/import can legitimately outlast the bounded wait
                # on loaded machines. The documented continuation still must
                # recover this same version, not execute another import.
                assert two["status"] == "in_progress"
                two = _communicate(_worker(isolated_dir, "normal"), 0)
        assert one["version"] == two["version"] == 1
        assert one["change"] == two["change"]
        assert not one["idempotency"]["replayed"] and two["idempotency"]["replayed"]
        assert len(_history(isolated_dir)) == len(_attempts(isolated_dir)) == 1
    finally:
        for process in (first, second):
            if process and process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
