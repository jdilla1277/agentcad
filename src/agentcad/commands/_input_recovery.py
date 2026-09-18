"""Cheap, read-only recovery for commands given a missing STEP path."""

import json
import shlex

from agentcad.project import ProjectError, get_project


def _current_step(layout):
    """Use recorded metadata, never guess an artifact name or another version."""
    try:
        manifest = layout.read_manifest()
        current = manifest.get("current")
        if current is None:
            return None
        entry = next((
            item for item in reversed(manifest.get("versions", []))
            if item.get("label") == current and item.get("status") == "success"
        ), None)
        if entry is None:
            return None
        meta = json.loads((layout.version_dir(entry) / "meta.json").read_text())
        if not isinstance(meta, dict) or meta.get("status") != "success":
            return None
        outputs = meta.get("outputs")
        recorded = outputs.get("step") if isinstance(outputs, dict) else None
        if not isinstance(recorded, str) or not recorded:
            return None
        step = layout.artifact_path(recorded)
        return step if step.is_file() else None
    except (ProjectError, OSError, ValueError, RuntimeError):
        return None


def missing_step_payload(command, step_file, options):
    layout = get_project()
    step = _current_step(layout)
    message = f"STEP file '{step_file}' not found or is not a file. "
    if step is not None:
        message += (
            "To use the current successful version's recorded outputs.step, "
            "run the corrected command in next_actions."
        )
        argv = ["agentcad", command, str(step)]
        for option, value in options.items():
            if value is None or value is False:
                continue
            # Attached values also preserve negative angles and focus points.
            argv.append(option if value is True else f"{option}={value}")
        if layout.configured:
            argv.extend(["--build-dir", str(layout.build_root)])
        actions = [shlex.join(argv)]
    else:
        message += (
            "No existing outputs.step is recorded for the current successful "
            "version in the selected build directory. Supply an existing STEP "
            "path or create a successful build first."
        )
        actions = ["agentcad docs artifacts"]
    return {
        "command": command, "status": "error", "message": message,
        "next_actions": actions,
    }
