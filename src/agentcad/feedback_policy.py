"""Project-scoped feedback policy. Only the human viewer exposes a setter."""
from __future__ import annotations

import json
from pathlib import Path

from agentcad.versioning import atomic_write_json


POLICY_VERSION = 1
BRIEF_LIMIT = 2000


def sharing_policy(root: Path, recipient: str) -> dict:
    try:
        saved = json.loads((root / ".agentcad/feedback-sharing.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    enabled = (isinstance(saved, dict) and saved.get("policy_version") == POLICY_VERSION
               and saved.get("enabled") is True and saved.get("recipient") == recipient
               and saved.get("scope") == str(root.resolve()))
    return {"enabled": enabled, "policy_version": POLICY_VERSION,
            "scope": str(root.resolve()), "recipient": recipient,
            "fields": ["summary", "agentcad_version"], "brief_limit": BRIEF_LIMIT}


def set_sharing_policy(root: Path, recipient: str, enabled: bool) -> dict:
    if type(enabled) is not bool:
        raise ValueError("enabled must be a boolean")
    path = root / ".agentcad/feedback-sharing.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {"policy_version": POLICY_VERSION,
                             "enabled": enabled, "recipient": recipient,
                             "scope": str(root.resolve())})
    return sharing_policy(root, recipient)
