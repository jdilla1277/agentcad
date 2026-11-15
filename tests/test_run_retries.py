"""Authored CAD request identities survive lost responses and process death."""

# collect-on-default-profile

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import click
import pytest

from agentcad.cli import cli

QUIET = ["--no-preview", "--no-diff", "--no-view", "--no-daemon"]
SOURCES = [
    (
        "build123d",
        """length = 60
plate = Box(length, 30, 6) - Pos(-20, 0, 0) * Cylinder(3, 12) - Pos(20, 0, 0) * Cylinder(3, 12)
show_object(fillet(plate.edges().filter_by(Axis.Z), radius=1), options={"id": "bracket", "part_of": "mount"})
show_object(Pos(0, 50, 0) * Cylinder(2, 12), options={"id": "pin", "part_of": "mount"})
""",
    ),
    pytest.param(
        "cadquery",
        """import cadquery as cq
length = 60
plate = cq.Workplane('XY').box(length,30,6).faces('>Z').workplane().pushPoints([(-20,0),(20,0)]).hole(6)
show_object(plate.edges('|Z').fillet(1), options={"id": "bracket", "part_of": "mount"})
show_object(cq.Workplane('XY').center(0,50).circle(2).extrude(12), options={"id": "pin", "part_of": "mount"})
""",
        marks=pytest.mark.skipif(
            importlib.util.find_spec("cadquery") is None,
            reason="optional CadQuery extra",
        ),
    ),
]


def invoke(runner, args, code=0):
    result = runner.invoke(cli, args)
    assert result.exit_code == code, result.output
    return json.loads(result.stdout)


def setup(runner, root, runtime="build123d", source=SOURCES[0][1]):
    invoke(runner, ["init", "--name", "run-retry", "--runtime", runtime])
    (root / "part.py").write_text(source)


def args(key="edit:1", *extra):
    return [
        "run", "--experimental-changes",
        "part.py",
        "--label",
        "bracket",
        "--idempotency-key",
        key,
        *QUIET,
        *extra,
    ]


def status(runner, key="edit:1", code=0):
    return invoke(runner, ["run", "--experimental-changes", "--idempotency-key", key, "--status"], code)


@pytest.mark.parametrize("runtime,source", SOURCES)
def test_lost_run_response_replays_saved_parts_without_execution(
    runner, isolated_dir, monkeypatch, runtime, source
):
    setup(runner, isolated_dir, runtime, source)
    echo = click.echo

    def lose_response(message=None, **kwargs):
        if (
            message
            and not kwargs.get("err")
            and json.loads(message).get("command") == "run"
        ):
            raise KeyboardInterrupt("lost response")
        return echo(message, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(click, "echo", lose_response)
        lost = runner.invoke(cli, args())
    assert lost.exit_code != 0 and not lost.stdout
    protected = [
        isolated_dir / "agentcad.json",
        isolated_dir / ".agentcad/changes/working.json",
    ]
    protected.extend(isolated_dir.glob("v*/*"))
    before = {p: p.read_bytes() for p in protected if p.is_file()}
    with monkeypatch.context() as patch:
        patch.setattr(
            "agentcad.commands.run._run_impl", lambda *a, **kw: pytest.fail("reran CAD")
        )
        replay = invoke(runner, args())
    assert replay["version"] == 1
    assert replay["idempotency"]["replayed"]
    assert replay["current_advanced"] is False
    assert [p["id"] for p in replay["parts"]] == ["bracket", "pin"]
    assert len(list((isolated_dir / ".agentcad/changes/attempts").iterdir())) == 1
    assert {p: p.read_bytes() for p in before} == before


def test_status_reads_original_result_after_newer_work_and_source_removal(
    runner, isolated_dir
):
    setup(runner, isolated_dir)
    first = invoke(runner, args())
    newer = invoke(runner, ["run", "--experimental-changes", "part.py", "--label", "newer", *QUIET])
    (isolated_dir / "part.py").unlink()
    paths = [
        isolated_dir / "agentcad.json",
        isolated_dir / ".agentcad/changes/working.json",
    ]
    paths.extend(Path(first["idempotency"]["request"]).parent.glob("*.json"))
    before = {p: p.read_bytes() for p in paths}
    original = status(runner)
    assert original["version"] == 1
    assert original["promotion"] == first["promotion"]
    assert original["current_advanced"] is False
    assert original["idempotency"]["status_only"] is True
    assert original["outputs"]["step"] != newer["outputs"]["step"]
    assert {p: p.read_bytes() for p in paths} == before


@pytest.mark.parametrize(
    "option,value", [("--label", "other"), ("--params", "length=80"), ("--stage", None)]
)
def test_same_run_key_conflicts_on_changed_options(runner, isolated_dir, option, value):
    setup(runner, isolated_dir)
    first = invoke(runner, args())
    retry = args()
    if option == "--label":
        retry[retry.index("--label") + 1] = value
    else:
        retry.extend([option, *([value] if value else [])])
    conflict = invoke(runner, retry, 1)
    assert conflict["reason"] == "idempotency_conflict"
    assert status(runner)["promotion"] == first["promotion"]


def test_source_changes_conflict_but_status_needs_no_source(runner, isolated_dir):
    setup(runner, isolated_dir)
    first = invoke(runner, args())
    (isolated_dir / "part.py").write_text("raise RuntimeError('changed')\n")
    assert invoke(runner, args(), 1)["reason"] == "idempotency_conflict"
    assert status(runner)["change"] == first["change"]


@pytest.mark.parametrize("ending", ["bad syntax (\n", "raise RuntimeError('failed')\n"])
def test_failed_run_is_cached_and_repair_uses_a_new_key(runner, isolated_dir, ending):
    setup(runner, isolated_dir, source=SOURCES[0][1] + ending)
    first = invoke(runner, args(), 1)
    replay = invoke(runner, args(), 1)
    assert replay["change"] == first["change"]
    assert replay["idempotency"]["replayed"]
    (isolated_dir / "part.py").write_text(SOURCES[0][1])
    assert invoke(runner, args(), 1)["reason"] == "idempotency_conflict"
    repaired = invoke(runner, args("repair:1"))
    assert repaired["change"]["parent_revision_id"] == first["change"]["revision_id"]


def test_unknown_status_and_keyed_dry_run_do_not_start_work(runner, isolated_dir):
    setup(runner, isolated_dir)
    assert status(runner, "unknown", 1)["reason"] == "run_request_not_found"
    assert not (isolated_dir / ".agentcad/run_requests").exists()
    assert (
        invoke(runner, [*args(), "--dry-run"], 1)["reason"] == "run_key_needs_retention"
    )
    assert not (isolated_dir / ".agentcad/run_requests").exists()


def test_registered_mcp_run_and_status_support_retry_keys(runner, isolated_dir):
    from agentcad.mcp.server import mcp

    setup(runner, isolated_dir)

    def call(name, arguments):
        result = asyncio.run(mcp.call_tool(name, {**arguments, "experimental_changes": True}))
        return result[1] if isinstance(result, tuple) else json.loads(result[0].text)

    arguments = {
        "script": "part.py",
        "cwd": str(isolated_dir),
        "output": "mcp",
        "idempotency_key": "mcp:1",
        "preview": False,
        "diff": False,
        "view": False,
    }
    first = call("run", arguments)
    assert call("run", arguments)["idempotency"]["replayed"]
    (isolated_dir / "part.py").unlink()
    lookup = call("run_status", {"cwd": str(isolated_dir), "idempotency_key": "mcp:1"})
    assert lookup["change"] == first["change"]
    assert lookup["current_advanced"] is False


def test_requirements_content_is_bound_and_staged_replay_stays_staged(
    runner, isolated_dir
):
    setup(runner, isolated_dir)
    checks = {
        "schema_version": 1,
        "checks": [
            {"check_id": "length", "kind": "dimension", "axis": "x", "expected_mm": 60}
        ],
    }
    path = isolated_dir / "checks.json"
    path.write_text(json.dumps(checks))
    first = invoke(runner, args("stage:1", "--stage", "--checks", "checks.json"))
    assert first["status"] == "staged"
    assert (
        invoke(runner, args("stage:1", "--stage", "--checks", "checks.json"))["change"]
        == first["change"]
    )
    checks["checks"][0]["expected_mm"] = 80
    path.write_text(json.dumps(checks))
    assert (
        invoke(runner, args("stage:1", "--stage", "--checks", "checks.json"), 1)[
            "reason"
        ]
        == "idempotency_conflict"
    )
    assert status(runner, "stage:1")["status"] == "staged"
    assert (
        json.loads((isolated_dir / "agentcad.json").read_text()).get("current") is None
    )


@pytest.mark.parametrize("runtime,source", SOURCES)
@pytest.mark.parametrize("lose_cache", [False, True])
def test_deferred_run_recovery_keeps_original_checks_after_completion(
    runner, isolated_dir, monkeypatch, runtime, source, lose_cache
):
    import agentcad.run_requests as requests

    setup(runner, isolated_dir, runtime, source)
    checks = {
        "schema_version": 1,
        "checks": [
            {"check_id": "length", "kind": "dimension", "axis": "x", "expected_mm": 60}
        ],
    }
    (isolated_dir / "checks.json").write_text(json.dumps(checks))
    invocation = args("deferred:1", "--checks", "checks.json", "--defer-checks")
    original_save = requests._save
    original_response = {}

    def stop_before_cache(path, body):
        if path.name == "response.json":
            original_response.update(body["payload"])
            raise KeyboardInterrupt("lost before response cache")
        return original_save(path, body)

    if lose_cache:
        with monkeypatch.context() as patch:
            patch.setattr(requests, "_save", stop_before_cache)
            stopped = runner.invoke(cli, invocation)
        assert stopped.exit_code != 0 and not stopped.stdout
        assert (
            not Path(original_response["idempotency"]["request"])
            .with_name("response.json")
            .exists()
        )
    else:
        original_response = invoke(runner, invocation, 1)
    revision = original_response["change"]["revision_id"]
    original_checks = original_response["change"]["validation_checks"]
    assert original_checks["validation_state"] == "pending"
    before_completion = status(runner, "deferred:1", 1)
    assert before_completion["change"]["validation_checks"] == original_checks
    completed = invoke(runner, ["check-spec", "--experimental-changes", revision, "--complete"])
    assert completed["validation_state"] == "valid"
    protected = [
        isolated_dir / "agentcad.json",
        isolated_dir / ".agentcad/changes/working.json",
    ]
    protected.extend(Path(original_response["change"]["request"]).parent.glob("*.json"))
    before = {p: p.read_bytes() for p in protected}
    with monkeypatch.context() as patch:
        patch.setattr(
            "agentcad.commands.run._run_impl", lambda *a, **kw: pytest.fail("reran CAD")
        )
        for recovered in (
            status(runner, "deferred:1", 1),
            invoke(runner, invocation, 1),
            status(runner, "deferred:1", 1),
        ):
            assert recovered["status"] == "checks_pending"
            assert recovered["change"]["validation_checks"] == original_checks
            assert recovered["change"] == original_response["change"]
            assert recovered["current_advanced"] is False
    assert {p: p.read_bytes() for p in protected} == before
    assert invoke(runner, ["check-spec", "--experimental-changes", revision])["validation_state"] == "valid"


def test_execution_uses_the_source_saved_before_work_begins(
    runner, isolated_dir, monkeypatch
):
    import importlib

    command = importlib.import_module("agentcad.commands.run")
    setup(runner, isolated_dir)
    original = command._run_impl

    def change_working_source(*a, **kw):
        (isolated_dir / "part.py").write_text(
            "raise RuntimeError('changed after saving the request')\n"
        )
        return original(*a, **kw)

    monkeypatch.setattr(command, "_run_impl", change_working_source)
    first = invoke(runner, args())
    assert first["metrics"]["dimensions"]["x"] == pytest.approx(60)
    assert Path(first["outputs"]["script"]).read_text() == SOURCES[0][1]


def test_configured_roots_scope_keys_and_status_works_from_subdirectory(
    runner, isolated_dir
):
    (isolated_dir / "part.py").write_text(SOURCES[0][1])
    for build_dir in ("artifacts with spaces", "other build"):
        invoke(runner, ["init", "--name", "configured", "--build-dir", build_dir])
        first = invoke(runner, [*args(), "--build-dir", build_dir])
        assert first["version"] == 1
        assert Path(first["outputs"]["step"]).is_relative_to(isolated_dir / build_dir)
    (isolated_dir / "caller").mkdir()
    os.chdir(isolated_dir / "caller")
    for build_dir in ("artifacts with spaces", "other build"):
        saved = invoke(
            runner,
            [
                "run", "--experimental-changes",
                "--idempotency-key",
                "edit:1",
                "--status",
                "--build-dir",
                str(isolated_dir / build_dir),
            ],
        )
        assert saved["status"] == "success"
        assert saved["current_advanced"] is False
        assert Path(saved["outputs"]["step"]).is_file()


@pytest.mark.parametrize("record", ["request.json", "response.json"])
def test_corrupted_records_fail_closed_without_execution(runner, isolated_dir, record):
    setup(runner, isolated_dir)
    first = invoke(runner, args())
    directory = Path(first["idempotency"]["request"]).parent
    (directory / record).write_text('{"checksum":"corrupt"}')
    before = (isolated_dir / "agentcad.json").read_bytes()
    assert status(runner, code=1)["reason"] == "run_request_unavailable"
    assert invoke(runner, args(), 1)["reason"] == "run_request_unavailable"
    assert (isolated_dir / "agentcad.json").read_bytes() == before
    assert len(list((isolated_dir / ".agentcad/changes/attempts").iterdir())) == 1


def test_status_rejects_execution_options_and_registered_mcp_unknown_options(
    runner, isolated_dir
):
    from agentcad.mcp.server import mcp

    setup(runner, isolated_dir)
    assert invoke(runner, ["run", "--experimental-changes", "--status"], 1)["reason"] == "run_key_required"
    for extra in (
        ["--label", "bad"],
        ["--no-preview"],
        ["--validation-profile", "kernel"],
    ):
        error = invoke(
            runner, ["run", "--experimental-changes", "--idempotency-key", "edit:1", "--status", *extra], 1
        )
        assert error["reason"] == "run_status_options"
    assert not (isolated_dir / ".agentcad/run_requests").exists()
    with pytest.raises(ValueError, match="Invalid run_status arguments"):
        asyncio.run(
            mcp.call_tool(
                "run_status",
                {"cwd": str(isolated_dir), "idempotency_key": "edit:1", "stage": True},
            )
        )


REPO = Path(__file__).parents[1]
WORKER = r"""
import importlib, os, sys, time
from pathlib import Path
from agentcad.cli import cli
import agentcad.run_requests as requests
import agentcad.versioning as versioning

mode = sys.argv[1]
if mode in ("request", "response"):
    original = requests._save
    def save(path, body):
        if path.name == mode + ".json":
            if mode == "request":
                original(path, body)
            os._exit(91)
        return original(path, body)
    requests._save = save
elif mode in ("precommit", "postcommit"):
    original = versioning.commit_version
    def commit(*a, **kw):
        if mode == "postcommit":
            original(*a, **kw)
        os._exit(91)
    versioning.commit_version = commit
elif mode == "optional":
    from agentcad.core_build import ArtifactLifecycle
    original = ArtifactLifecycle.persist
    def persist(self):
        original(self)
        os._exit(91)
    ArtifactLifecycle.persist = persist
elif mode == "hold":
    import agentcad.runners.build123d as runner
    original = runner.execute
    def execute(*a, **kw):
        Path('entered').touch()
        deadline = time.monotonic() + 40
        while not Path('release').exists():
            if time.monotonic() > deadline:
                raise RuntimeError('test release missing')
            time.sleep(.03)
        return original(*a, **kw)
    runner.execute = execute
elif mode == "busy":
    requests._LOCK_WAIT_SECONDS = .1
cli.main(args=['run','--experimental-changes','part.py','--label','bracket','--idempotency-key','process:1',
               '--no-preview','--no-diff','--no-view','--no-daemon'])
"""


def worker(root, mode):
    return subprocess.Popen(
        [sys.executable, "-c", WORKER, mode],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(REPO / "src"), "AGENTCAD_DAEMON": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def receive(process, code):
    try:
        out, err = process.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=10)
        raise
    expected_codes = (code,) if isinstance(code, int) else code
    assert process.returncode in expected_codes, (out, err)
    return json.loads(out) if out else None


def counting_source(root):
    (root / "part.py").write_text(
        "from pathlib import Path\n"
        "counter = Path('executions')\n"
        "counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else '1')\n"
        + SOURCES[0][1]
    )


@pytest.mark.parametrize(
    "mode", ["request", "precommit", "postcommit", "optional", "response"]
)
def test_process_death_never_reexecutes_or_reselects_older_work(
    runner, isolated_dir, mode
):
    setup(runner, isolated_dir)
    counting_source(isolated_dir)
    receive(worker(isolated_dir, mode), 91)
    count = "0" if mode == "request" else "1"
    assert (
        (isolated_dir / "executions").read_text() == "1"
        if mode != "request"
        else not (isolated_dir / "executions").exists()
    )
    (isolated_dir / "newer.py").write_text(SOURCES[0][1])
    newer = invoke(runner, ["run", "--experimental-changes", "newer.py", "--label", "newer", *QUIET])
    protected = [
        isolated_dir / "agentcad.json",
        isolated_dir / ".agentcad/changes/working.json",
    ]
    protected.extend(p for p in isolated_dir.glob("v*/*") if p.is_file())
    before = {p: p.read_bytes() for p in protected}
    code = 1 if mode in ("request", "precommit") else 0
    lookup = status(runner, "process:1", code)
    replay = invoke(runner, args("process:1"), code)
    assert replay["current_advanced"] is False
    assert replay["idempotency"]["replayed"]
    assert lookup["status"] == replay["status"]
    if code:
        assert replay["reason"] == "run_interrupted"
        assert (
            lookup["idempotency"]["state"]
            == replay["idempotency"]["state"]
            == "interrupted"
        )
        assert replay["outputs"]["step"] is None
    else:
        assert replay["version"] == 1
        assert replay["promotion"]["outcome"] == "promoted"
        assert replay["accepted_version_before"] is None
        assert replay["accepted_version_after"]["version"] == 1
        assert replay["outputs"]["step"] != newer["outputs"]["step"]
        assert Path(replay["outputs"]["step"]).exists()
    assert {p: p.read_bytes() for p in protected} == before
    assert (
        (isolated_dir / "executions").read_text() == "1"
        if count == "1"
        else not (isolated_dir / "executions").exists()
    )


@pytest.mark.parametrize("busy", [False, True])
def test_concurrent_same_key_shares_execution(runner, isolated_dir, busy):
    setup(runner, isolated_dir)
    counting_source(isolated_dir)
    first = worker(isolated_dir, "hold")
    second = None
    try:
        deadline = time.monotonic() + 60
        while not (isolated_dir / "entered").exists():
            assert first.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.03)
        second = worker(isolated_dir, "busy" if busy else "normal")
        lookup = status(runner, "process:1", 1)
        assert lookup["status"] == "in_progress"
        if busy:
            waiting = receive(second, 1)
            assert waiting["status"] == "in_progress"
        (isolated_dir / "release").touch()
        one = receive(first, 0)
        if busy:
            two = receive(worker(isolated_dir, "normal"), 0)
        else:
            two = receive(second, (0, 1))
            if two["status"] == "in_progress":
                # A cold CAD import can outlast the normal bounded lock wait.
                # Once the first run finishes, retry must replay it, not execute.
                assert second.returncode == 1
                assert two["outputs"]["step"] is None
                two = receive(worker(isolated_dir, "normal"), 0)
            else:
                assert second.returncode == 0
        assert one["change"] == two["change"]
        assert two["idempotency"]["replayed"]
        assert (isolated_dir / "executions").read_text() == "1"
        assert (
            len(json.loads((isolated_dir / "agentcad.json").read_text())["versions"])
            == 1
        )
    finally:
        for process in (first, second):
            if process and process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
