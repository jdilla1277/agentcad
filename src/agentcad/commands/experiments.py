"""Manage deliberate, project-local opt-ins to unfinished features."""

from __future__ import annotations

import json

import click

from agentcad.experiments import (
    EXPERIMENTS,
    ExperimentConfigError,
    disable_experiment,
    enable_experiment,
    experiment_status,
)
from agentcad.manifest import load_manifest


def _emit(payload: dict, exit_code: int = 0) -> None:
    click.echo(json.dumps(payload))
    if exit_code:
        raise click.exceptions.Exit(exit_code)


def _require_project() -> None:
    load_manifest(command="experiments")


@click.group()
def experiments():
    """Inspect or change project-local experimental feature opt-ins."""


@experiments.command("list")
def list_experiments():
    """List available experiments and whether this project enabled them."""
    _require_project()
    _emit({
        "command": "experiments",
        "action": "list",
        "status": "success",
        "experiments": experiment_status(),
        "policy": (
            "Experiments are off by default. Agents must obtain explicit user "
            "approval before enabling one."
        ),
    })


@experiments.command("enable")
@click.argument("name")
@click.option(
    "--acknowledge-unstable",
    is_flag=True,
    help="Acknowledge that the feature may fail, change, or produce incomplete artifacts.",
)
def enable(name: str, acknowledge_unstable: bool):
    """Enable experiment NAME for this project only."""
    _require_project()
    if name not in EXPERIMENTS:
        _emit({
            "command": "experiments",
            "action": "enable",
            "status": "error",
            "error_kind": "unknown_experiment",
            "experiment": name,
            "available": sorted(EXPERIMENTS),
        }, exit_code=1)
        return
    if not acknowledge_unstable:
        _emit({
            "command": "experiments",
            "action": "enable",
            "status": "error",
            "error_kind": "unstable_acknowledgement_required",
            "experiment": name,
            "message": EXPERIMENTS[name].warning,
            "requires_user_approval": True,
            "suggestion": (
                f"After explicit user approval, run `agentcad experiments "
                f"enable {name} --acknowledge-unstable`."
            ),
        }, exit_code=1)
        return
    try:
        changed = enable_experiment(name)
    except ExperimentConfigError as exc:
        _emit({
            "command": "experiments",
            "action": "enable",
            "status": "error",
            "error_kind": "invalid_experiment_config",
            "message": str(exc),
        }, exit_code=1)
        return
    _emit({
        "command": "experiments",
        "action": "enable",
        "status": "success",
        "experiment": name,
        "changed": changed,
        "warning": EXPERIMENTS[name].warning,
        "experiments": experiment_status(),
    })


@experiments.command("disable")
@click.argument("name")
def disable(name: str):
    """Disable experiment NAME for this project."""
    _require_project()
    try:
        changed = disable_experiment(name)
    except ExperimentConfigError as exc:
        _emit({
            "command": "experiments",
            "action": "disable",
            "status": "error",
            "error_kind": "invalid_experiment_config",
            "message": str(exc),
        }, exit_code=1)
        return
    response = {
        "command": "experiments",
        "action": "disable",
        "status": "success",
        "experiment": name,
        "changed": changed,
        "experiments": experiment_status(),
    }
    if name not in EXPERIMENTS:
        response["advisory"] = (
            "This installed release does not register that experiment. If it "
            "was present in older local state, it has been removed."
        )
    _emit(response)


def require_experiment(name: str, command: str) -> None:
    """Reusable JSON gate for commands backed by an unfinished feature."""
    from agentcad.experiments import is_experiment_enabled

    if is_experiment_enabled(name):
        return
    experiment = EXPERIMENTS[name]
    _emit({
        "command": command,
        "status": "error",
        "error_kind": "experimental_feature_disabled",
        "experiment": name,
        "message": experiment.warning,
        "activation": {
            "requires_user_approval": True,
            "command": (
                f"agentcad experiments enable {name} --acknowledge-unstable"
            ),
        },
    }, exit_code=1)
