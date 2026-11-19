"""A mistaken build override recommends the valid project without changing it."""
import asyncio
import json
import shlex
from pathlib import Path

import pytest

from agentcad.cli import cli


def invoke(runner, args):
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def files(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("command", ["context", "run", "parts"])
def test_first_action_uses_existing_project_without_mutation(
    runner, isolated_dir, monkeypatch, configured, nested, command
):
    root = isolated_dir / "project with 'quotes'"
    root.mkdir()
    monkeypatch.chdir(root)
    if configured:
        (root / "agentcad.toml").write_text('build_dir = "saved history"\n')
    invoke(runner, ["init", "--no-agent-setup"])
    existing = root / "saved history" if configured else root
    sources = root / "sources"
    sources.mkdir()
    if nested:
        monkeypatch.chdir(sources)
    before = files(root)
    requested = root / "mistaken history"
    args = {
        "context": ["context"],
        "run": ["run", "missing.py", "--label", "first", "--no-daemon"],
        "parts": ["parts", "--build-dir", str(requested), "list", "current"],
    }[command]
    if command != "parts":
        args += ["--build-dir", "mistaken history"]
    failed = runner.invoke(cli, args)
    assert failed.exit_code == 1, failed.output
    assert "Traceback" not in failed.stdout
    data = json.loads(failed.stdout)
    assert data["reason"] == "build_root_not_initialized"
    assert "Omit --build-dir" in data["suggestion"]
    assert data["next_actions"][0] == "agentcad context"
    assert data["recovery"] == {
        "detected_project_root": str(root), "requested_build_root": str(requested),
        "existing_build_root": str(existing), "action": "use_existing_project",
        "command": data["next_actions"][0],
    }
    if command == "run":
        assert not data["artifact_created"] and data["outputs"]["step"] is None
    # Failure must not create a new root, consume a version, or write a log.
    assert files(root) == before and not requested.exists()
    recovered = invoke(runner, shlex.split(data["next_actions"][0])[1:])
    assert recovered["build_root"] == str(existing) and recovered["version_count"] == 0
    assert not requested.exists()
    assert json.loads((existing / "agentcad.json").read_text())["versions"] == []
    # The alternative remains an executable, correctly quoted initialization.
    alternative = shlex.split(data["next_actions"][1])[1:]
    assert alternative == ["init", "--build-dir", str(requested)]
    invoke(runner, [*alternative, "--no-agent-setup"])
    assert (requested / "agentcad.json").is_file()
    assert json.loads((existing / "agentcad.json").read_text())["versions"] == []


@pytest.mark.parametrize("invalid", ["missing", "json", "shape", "owner", "escape", "config"])
def test_no_usable_default_history_keeps_initialization_first(runner, isolated_dir, invalid):
    root = isolated_dir
    # Anchor source identity so a foreign manifest cannot redefine the project.
    (root / "agentcad.toml").write_text("")
    manifest = {"project_root": str(root), "versions": []}
    if invalid == "shape":
        manifest["versions"] = "invalid"
    if invalid == "owner":
        manifest["project_root"] = str(root / "foreign")
    if invalid == "escape":
        manifest["versions"] = [{"path": "../outside"}]
    if invalid not in {"missing"}:
        (root / "agentcad.json").write_text("{" if invalid == "json" else json.dumps(manifest))
    if invalid == "config":
        (root / "agentcad.toml").write_text('build_dir = "uninitialized default"\n')
    before = files(root)
    failed = runner.invoke(cli, ["context", "--build-dir", "new history"])
    assert failed.exit_code == 1, failed.output
    data = json.loads(failed.stdout)
    assert data["reason"] == "build_root_not_initialized"
    assert "recovery" not in data and "Omit --build-dir" not in data["suggestion"]
    assert shlex.split(data["next_actions"][0]) == [
        "agentcad", "init", "--build-dir", str(root / "new history"),
    ]
    assert files(root) == before
    assert not (root / "new history").exists()
    invoke(runner, [*shlex.split(data["next_actions"][0])[1:], "--no-agent-setup"])
    assert (root / "new history/agentcad.json").is_file()


@pytest.mark.parametrize("manifest", [
    {"versions": []},
    {"name": None, "versions": []},
    {"name": "", "versions": []},
    {"name": "project", "versions": [{"path": "v1_first/"}]},
    *[
        {"name": "project", "versions": [{
            key: value for key, value in {
                "version": 1, "label": "first", "status": "success", "path": "v1_first/",
            }.items() if key != missing
        }]}
        for missing in ("version", "label", "status", "path")
    ],
    *[
        {"name": "project", "versions": [{
            "version": 1, "label": "first", "status": "success", "path": "v1_first/",
            **{field: value},
        }]}
        for field, value in (
            ("version", True), ("version", "1"), ("version", 0),
            ("label", []), ("status", None), ("path", ""),
        )
    ],
])
def test_partial_manifest_is_not_preferred_and_context_returns_json_error(runner, isolated_dir, manifest):
    from agentcad.project import ProjectError, resolve_project

    (isolated_dir / "agentcad.toml").write_text("")
    (isolated_dir / "agentcad.json").write_text(json.dumps(manifest))
    before = files(isolated_dir)
    failed = runner.invoke(cli, ["context", "--build-dir", "mistaken"])
    assert failed.exit_code == 1, failed.output
    payload = json.loads(failed.stdout)
    assert payload["reason"] == "build_root_not_initialized"
    assert "recovery" not in payload
    assert shlex.split(payload["next_actions"][0]) == [
        "agentcad", "init", "--build-dir", str(isolated_dir / "mistaken"),
    ]
    # The same validator protects direct context calls, rather than only
    # suppressing the bad recommendation and leaving the original crash.
    with pytest.raises(ProjectError):
        resolve_project().read_manifest(require_context=True)
    direct = runner.invoke(cli, ["context"])
    assert direct.exit_code == 1 and "Traceback" not in direct.stdout
    assert json.loads(direct.stdout)["reason"] == "invalid_manifest"
    assert files(isolated_dir) == before
    assert not (isolated_dir / "mistaken").exists()


@pytest.mark.parametrize("versions", [None, [], [
    {"version": 1, "label": "first", "status": "success", "path": "v1_first/"},
]])
def test_legacy_context_fields_remain_sufficient(runner, isolated_dir, versions):
    manifest = {"name": "legacy"}
    if versions is not None:
        manifest["versions"] = versions
    (isolated_dir / "agentcad.json").write_text(json.dumps(manifest))
    failed = runner.invoke(cli, ["context", "--build-dir", "mistaken"])
    assert failed.exit_code == 1, failed.output
    payload = json.loads(failed.stdout)
    assert payload["recovery"]["action"] == "use_existing_project"
    recovered = invoke(runner, shlex.split(payload["next_actions"][0])[1:])
    assert recovered["project"] == "legacy"
    assert recovered["version_count"] == len(versions or [])
    if versions:
        assert recovered["versions"][0]["source"] == "script"
    assert not (isolated_dir / "mistaken").exists()


def test_omitting_override_does_not_bypass_uninitialized_config(runner, isolated_dir):
    invoke(runner, ["init", "--no-agent-setup"])
    (isolated_dir / "agentcad.toml").write_text('build_dir = "new default"\n')
    before = files(isolated_dir)
    failed = runner.invoke(cli, ["context"])
    data = json.loads(failed.stdout)
    assert failed.exit_code == 1 and "recovery" not in data
    assert shlex.split(data["next_actions"][0])[1:] == [
        "init", "--build-dir", str(isolated_dir / "new default"),
    ]
    assert files(isolated_dir) == before


def test_run_without_mistaken_override_builds_in_original_history(runner, isolated_dir):
    invoke(runner, ["init", "--no-agent-setup"])
    script = isolated_dir / "model.py"
    script.write_text("show_object(Box(10, 20, 5))\n")
    args = ["run", str(script), "--label", "first", "--no-preview", "--no-diff", "--no-view", "--no-daemon"]
    before = files(isolated_dir)
    failed = runner.invoke(cli, [*args, "--build-dir=./build"])
    assert failed.exit_code == 1, failed.output
    data = json.loads(failed.stdout)
    assert not data["artifact_created"] and data["outputs"]["step"] is None
    assert files(isolated_dir) == before
    invoke(runner, shlex.split(data["next_actions"][0])[1:])
    built = invoke(runner, args)
    assert built["version"] == 1
    assert (isolated_dir / built["outputs"]["step"]).is_file()
    assert not (isolated_dir / "build").exists()


def test_registered_mcp_returns_same_executable_recovery(runner, isolated_dir):
    from agentcad.mcp.server import mcp

    invoke(runner, ["init", "--no-agent-setup"])
    def call(**arguments):
        result = asyncio.run(mcp.call_tool("context", {"cwd": str(isolated_dir), **arguments}))
        content = result[0] if isinstance(result, tuple) else result
        return json.loads(next(item.text for item in content if item.type == "text"))
    failed = call(build_dir="guessed build")
    assert failed["reason"] == "build_root_not_initialized"
    assert failed["recovery"]["existing_build_root"] == str(isolated_dir)
    recovered = invoke(runner, shlex.split(failed["next_actions"][0])[1:])
    assert recovered["build_root"] == str(isolated_dir)
    assert call()["build_root"] == str(isolated_dir)
    assert not (isolated_dir / "guessed build").exists()
