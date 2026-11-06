"""Add optional agent guidance to otherwise-stable command responses."""

from __future__ import annotations

import json

from agentcad.experiments import experiment_status
from agentcad.update_check import available_update_notice


def augment_json_response(message):
    """Return *message* with additive ``_meta`` fields when it is response JSON."""
    if not isinstance(message, str):
        return message
    try:
        payload = json.loads(message)
    except (TypeError, ValueError):
        return message
    if not isinstance(payload, dict) or "status" not in payload:
        return message

    experiment_state = experiment_status()
    enabled = experiment_state.get("enabled", [])
    config_error = experiment_state.get("config_error")
    meta = payload.get("_meta")
    if not isinstance(meta, dict):
        meta = {}

    # The foreground client may be newer than a daemon that produced this
    # payload. It owns these process-local fields and must replace stale child
    # metadata rather than merely appending to it.
    meta.pop("active_experiments", None)
    meta.pop("experiment_config_error", None)
    if enabled:
        meta["active_experiments"] = enabled
    if config_error:
        meta["experiment_config_error"] = config_error

    try:
        notice = available_update_notice()
    except Exception:
        # Optional release discovery must never change command behavior.
        notice = None
    notices = meta.get("notices")
    if not isinstance(notices, list):
        notices = []
    notices = [
        existing for existing in notices
        if not isinstance(existing, dict)
        or existing.get("id") not in {
            "agentcad.update_available",
            "agentcad.process_restart_required",
        }
    ]
    if notice:
        notices.append(notice)
    if notices:
        meta["notices"] = notices
    else:
        meta.pop("notices", None)

    if meta:
        payload["_meta"] = meta
        return json.dumps(payload)
    payload.pop("_meta", None)
    if payload != json.loads(message):
        return json.dumps(payload)
    return message
