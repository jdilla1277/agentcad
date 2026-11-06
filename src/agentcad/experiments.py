"""Project-local opt-ins for unfinished agentcad features.

Experiments are deliberately local to one agentcad project.  They live under
``.agentcad/`` (already gitignored) so enabling rough functionality cannot
silently enroll teammates or other projects on the same machine.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from agentcad.versioning import atomic_write_json


STATE_SCHEMA_VERSION = 1
STATE_PATH = Path(".agentcad") / "experiments.json"


@dataclass(frozen=True)
class Experiment:
    name: str
    description: str
    introduced: str
    warning: str = (
        "This feature is unfinished and may fail, change, or produce "
        "incomplete artifacts."
    )


# Add experiments here when their dormant implementation ships.  Keeping the
# registry explicit makes every public opt-in reviewable and gives commands,
# context, docs, and feedback one source of truth.
EXPERIMENTS: dict[str, Experiment] = {}


class ExperimentConfigError(ValueError):
    pass


def state_path(project_dir: Path | None = None) -> Path:
    return Path(project_dir or Path.cwd()) / STATE_PATH


def _read_state(project_dir: Path | None = None) -> dict:
    path = state_path(project_dir)
    if not path.exists():
        return {"schema_version": STATE_SCHEMA_VERSION, "enabled": []}
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ExperimentConfigError(
            f"Could not read {STATE_PATH}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ExperimentConfigError(f"{STATE_PATH} must contain a JSON object.")
    if payload.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ExperimentConfigError(
            f"{STATE_PATH} has an unsupported schema_version; expected "
            f"{STATE_SCHEMA_VERSION}."
        )
    enabled = payload.get("enabled")
    if not isinstance(enabled, list) or any(
        not isinstance(name, str) for name in enabled
    ):
        raise ExperimentConfigError(
            f"{STATE_PATH}.enabled must be a list of experiment names."
        )
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "enabled": sorted(set(enabled)),
    }


def _write_enabled(names: list[str], project_dir: Path | None = None) -> None:
    atomic_write_json(state_path(project_dir), {
        "schema_version": STATE_SCHEMA_VERSION,
        "enabled": sorted(set(names)),
    })


def enabled_experiment_names(project_dir: Path | None = None) -> list[str]:
    """Return known enabled experiments, failing closed on bad local state."""
    try:
        enabled = _read_state(project_dir)["enabled"]
    except ExperimentConfigError:
        return []
    return [name for name in enabled if name in EXPERIMENTS]


def experiment_status(project_dir: Path | None = None) -> dict:
    """Return agent-facing experiment state, including malformed-state errors."""
    config_error = None
    try:
        configured = _read_state(project_dir)["enabled"]
    except ExperimentConfigError as exc:
        configured = []
        config_error = str(exc)

    enabled = [name for name in configured if name in EXPERIMENTS]
    unknown = [name for name in configured if name not in EXPERIMENTS]
    available = []
    for name in sorted(EXPERIMENTS):
        record = asdict(EXPERIMENTS[name])
        record["enabled"] = name in enabled
        record["stability"] = "experimental"
        available.append(record)

    result = {
        "enabled": enabled,
        "available": available,
        "config_path": str(STATE_PATH),
    }
    if unknown:
        result["unknown_enabled"] = unknown
    if config_error:
        result["config_error"] = config_error
    return result


def enable_experiment(name: str, project_dir: Path | None = None) -> bool:
    if name not in EXPERIMENTS:
        raise KeyError(name)
    state = _read_state(project_dir)
    changed = name not in state["enabled"]
    _write_enabled([*state["enabled"], name], project_dir)
    return changed


def disable_experiment(name: str, project_dir: Path | None = None) -> bool:
    state = _read_state(project_dir)
    changed = name in state["enabled"]
    if changed:
        _write_enabled(
            [enabled for enabled in state["enabled"] if enabled != name],
            project_dir,
        )
    return changed


def is_experiment_enabled(name: str, project_dir: Path | None = None) -> bool:
    if name not in EXPERIMENTS:
        raise KeyError(f"Experiment {name!r} is not registered.")
    return name in enabled_experiment_names(project_dir)


def add_experiment_metadata(payload: dict, project_dir: Path | None = None) -> dict:
    """Stamp version metadata with the rough features active for that work."""
    enabled = enabled_experiment_names(project_dir)
    if enabled:
        payload["experiments"] = enabled
    return payload
