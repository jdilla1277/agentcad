import json

from agentcad.cli import cli
from agentcad.experiments import Experiment, STATE_PATH


def _register(monkeypatch, name="rough-viewer"):
    from agentcad import experiments as experiments_mod

    experiment = Experiment(
        name=name,
        description="Try an unfinished review viewer.",
        introduced="0.5.0",
    )
    monkeypatch.setitem(experiments_mod.EXPERIMENTS, name, experiment)
    return experiment


def _init(runner):
    result = runner.invoke(cli, ["init", "--name", "experiments-test"])
    assert result.exit_code == 0, result.output


def test_experiments_are_off_by_default(runner, isolated_dir, monkeypatch):
    _register(monkeypatch)
    _init(runner)

    result = runner.invoke(cli, ["experiments", "list"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["experiments"]["enabled"] == []
    assert data["experiments"]["available"][0]["name"] == "rough-viewer"
    assert data["experiments"]["available"][0]["enabled"] is False
    assert not (isolated_dir / STATE_PATH).exists()


def test_enable_requires_explicit_unstable_acknowledgement(
    runner, isolated_dir, monkeypatch
):
    _register(monkeypatch)
    _init(runner)

    result = runner.invoke(cli, ["experiments", "enable", "rough-viewer"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["error_kind"] == "unstable_acknowledgement_required"
    assert data["requires_user_approval"] is True
    assert not (isolated_dir / STATE_PATH).exists()


def test_enable_is_project_local_and_visible_to_context(
    runner, isolated_dir, monkeypatch
):
    _register(monkeypatch)
    _init(runner)

    result = runner.invoke(cli, [
        "experiments", "enable", "rough-viewer", "--acknowledge-unstable",
    ])

    assert result.exit_code == 0, result.output
    state = json.loads((isolated_dir / STATE_PATH).read_text())
    assert state == {"schema_version": 1, "enabled": ["rough-viewer"]}
    response = json.loads(result.stdout)
    assert response["_meta"]["active_experiments"] == ["rough-viewer"]

    context = json.loads(runner.invoke(cli, ["context"]).stdout)
    assert context["experiments"]["enabled"] == ["rough-viewer"]


def test_disable_is_idempotent(runner, isolated_dir, monkeypatch):
    _register(monkeypatch)
    _init(runner)
    runner.invoke(cli, [
        "experiments", "enable", "rough-viewer", "--acknowledge-unstable",
    ])

    first = json.loads(
        runner.invoke(cli, ["experiments", "disable", "rough-viewer"]).stdout
    )
    second = json.loads(
        runner.invoke(cli, ["experiments", "disable", "rough-viewer"]).stdout
    )

    assert first["changed"] is True
    assert second["changed"] is False
    assert json.loads((isolated_dir / STATE_PATH).read_text())["enabled"] == []


def test_unknown_experiment_is_not_written(runner, isolated_dir):
    _init(runner)

    result = runner.invoke(cli, [
        "experiments", "enable", "not-shipped", "--acknowledge-unstable",
    ])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error_kind"] == "unknown_experiment"
    assert not (isolated_dir / STATE_PATH).exists()


def test_disabling_unknown_experiment_is_clear_and_does_not_create_state(
    runner, isolated_dir
):
    _init(runner)

    result = runner.invoke(cli, ["experiments", "disable", "old-feature"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["changed"] is False
    assert "does not register" in payload["advisory"]
    assert not (isolated_dir / STATE_PATH).exists()


def test_malformed_experiment_state_fails_closed(
    runner, isolated_dir, monkeypatch
):
    _register(monkeypatch)
    _init(runner)
    path = isolated_dir / STATE_PATH
    path.parent.mkdir(exist_ok=True)
    path.write_text("not-json")

    context = json.loads(runner.invoke(cli, ["context"]).stdout)

    assert context["experiments"]["enabled"] == []
    assert "config_error" in context["experiments"]
    assert "experiment_config_error" in context["_meta"]


def test_enabled_experiments_are_recorded_in_version_metadata(
    runner, isolated_dir, monkeypatch
):
    _register(monkeypatch)
    runner.invoke(
        cli,
        ["init", "--name", "p", "--runtime", "cadquery"],
    )
    runner.invoke(cli, [
        "experiments", "enable", "rough-viewer", "--acknowledge-unstable",
    ])
    (isolated_dir / "model.py").write_text(
        "import cadquery as cq\nshow_object(cq.Workplane('XY').box(1, 1, 1))\n"
    )

    result = runner.invoke(cli, [
        "run", "model.py", "--output", "one", "--no-preview", "--no-diff",
        "--no-view",
    ])

    assert result.exit_code == 0, result.output
    meta = json.loads((isolated_dir / "v1_one" / "meta.json").read_text())
    assert meta["experiments"] == ["rough-viewer"]
