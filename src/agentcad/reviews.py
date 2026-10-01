"""Local persistence for shared human/agent review comments.

Review state deliberately lives in a plain JSON file under ``.agentcad``.
The browser talks to it through the loopback-only project viewer; agents use
the CLI helpers in this module through ``agentcad review``.
"""

from __future__ import annotations

import json
import math
import base64
import binascii
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from agentcad.versioning import atomic_write_json


REVIEW_FILE = Path(".agentcad/reviews/comments.json")
_LOCK_TIMEOUT_S = 5.0
_STATUSES = {"draft", "open", "addressed", "resolved"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_store() -> dict:
    return {"schema_version": 1, "next_comment": 1, "next_batch": 1, "comments": []}


def review_path(project_dir: Path) -> Path:
    return Path(project_dir) / REVIEW_FILE


@contextmanager
def _review_lock(project_dir: Path):
    state_dir = Path(project_dir) / ".agentcad"
    state_dir.mkdir(parents=True, exist_ok=True)
    # OS-owned locks are released on crashes; never steal a slow writer's lock.
    with (state_dir / "reviews-write.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            def acquire():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(handle, fcntl.LOCK_UN)
        deadline = time.monotonic() + _LOCK_TIMEOUT_S
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Review is busy; retry shortly")
                time.sleep(0.01)
        try:
            yield
        finally:
            release()


def _load_unlocked(project_dir: Path) -> dict:
    path = review_path(project_dir)
    if not path.exists():
        return _empty_store()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read review comments: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("comments"), list):
        raise ValueError("Review comments file has an invalid shape")
    data.setdefault("schema_version", 1)
    data.setdefault("next_comment", len(data["comments"]) + 1)
    data.setdefault("next_batch", 1)
    return data


def load_reviews(project_dir: Path) -> dict:
    return _load_unlocked(Path(project_dir))


def _save_unlocked(project_dir: Path, data: dict) -> None:
    atomic_write_json(review_path(project_dir), data)


def _store_screenshot(project_dir: Path, comment_id: str, value) -> str | None:
    if not value:
        return None
    if not isinstance(value, str) or not value.startswith("data:image/"):
        raise ValueError("Comment screenshot must be an image data URL")
    try:
        header, encoded = value.split(",", 1)
        mime = header.split(";", 1)[0].split(":", 1)[1]
        suffix = {"image/jpeg": ".jpg", "image/png": ".png"}[mime]
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, KeyError, binascii.Error) as exc:
        raise ValueError("Comment screenshot is not a valid JPEG or PNG data URL") from exc
    if len(image) > 1_000_000:
        raise ValueError("Comment screenshot exceeds the 1 MB limit")
    relative = Path(".agentcad/reviews/screenshots") / f"{comment_id}{suffix}"
    target = Path(project_dir) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(image)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return relative.as_posix()


def list_comments(project_dir: Path, *, status: str | None = None) -> list[dict]:
    if status is not None and status not in _STATUSES:
        raise ValueError(f"Unknown review status '{status}'")
    comments = _load_unlocked(Path(project_dir))["comments"]
    if status is not None:
        comments = [comment for comment in comments if comment.get("status") == status]
    return comments


def get_comment(project_dir: Path, comment_id: str) -> dict | None:
    return next(
        (comment for comment in list_comments(project_dir) if comment.get("id") == comment_id),
        None,
    )


def _validate_payload(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Comment must be an object")
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 20000:
        raise ValueError("Comment text is required (maximum 20000 characters)")
    anchor = payload.get("anchor")
    if not isinstance(anchor, dict) or anchor.get("kind") not in {"surface", "part", "view"}:
        raise ValueError("Comment anchor must be surface, part, or view")
    for key, size in (("point_mm", 3), ("normal", 3), ("part_relative", 3), ("screen", 2)):
        value = anchor.get(key)
        if value is not None and (not isinstance(value, list) or len(value) != size or any(
            not isinstance(n, (float, int)) or isinstance(n, bool) or not math.isfinite(n)
            for n in value
        )):
            raise ValueError(f"Anchor {key} must contain {size} finite numbers")
    if anchor["kind"] == "surface" and anchor.get("point_mm") is None:
        raise ValueError("Surface anchors require point_mm")
    target = payload.get("target") or {}
    if not isinstance(target, dict) or target.get("model") not in {None, "a", "b", "both"}:
        raise ValueError("Invalid comment target")
    if target.get("scope") not in {None, "current", "previous", "both"}:
        raise ValueError("Invalid comment scope")
    if target.get("source_model") not in {None, "a", "b"}:
        raise ValueError("Invalid source model")
    if target.get("part_id") is not None and not isinstance(target["part_id"], str):
        raise ValueError("Part id must be a string")
    if anchor["kind"] == "part" and not target.get("part_id"):
        raise ValueError("Part anchors require a part id")
    view = payload.get("view", {})
    if not isinstance(view, dict):
        raise ValueError("Comment view must be an object")
    for key in ("position", "target"):
        vector = view.get(key)
        if vector is not None and (not isinstance(vector, list) or len(vector) != 3 or any(
            not isinstance(n, (float, int)) or isinstance(n, bool) or not math.isfinite(n)
            for n in vector
        )):
            raise ValueError(f"View {key} must contain three finite numbers")


def create_comment(
    project_dir: Path,
    payload: dict,
    *,
    actor: str = "human",
    status: str | None = None,
) -> dict:
    _validate_payload(payload)
    text = str(payload.get("text", "")).strip()
    if not text:
        raise ValueError("Comment text is required")
    if actor not in {"human", "agent"}:
        raise ValueError("Comment actor must be human or agent")
    initial_status = status or ("open" if actor == "agent" else "draft")
    if initial_status not in {"draft", "open"}:
        raise ValueError("A new comment must be draft or open")
    anchor = payload.get("anchor")
    if not isinstance(anchor, dict) or anchor.get("kind") not in {"surface", "part", "view"}:
        raise ValueError("Comment anchor must be surface, part, or view")

    project_dir = Path(project_dir)
    with _review_lock(project_dir):
        data = _load_unlocked(project_dir)
        comment_id = f"C{int(data['next_comment'])}"
        data["next_comment"] = int(data["next_comment"]) + 1
        now = _now()
        screenshot = _store_screenshot(project_dir, comment_id, payload.get("screenshot"))
        comment = {
            "id": comment_id,
            "status": initial_status,
            "author": actor,
            "text": text,
            "source_version": payload.get("source_version"),
            "source_label": payload.get("source_label"),
            "target": payload.get("target") or {},
            "anchor": anchor,
            "view": payload.get("view") or {},
            "screenshot": screenshot,
            "created_at": now,
            "updated_at": now,
            "events": [{"action": "created", "at": now, "actor": actor}],
            "replies": [],
        }
        data["comments"].append(comment)
        _save_unlocked(project_dir, data)
        return comment


def reply_to_comment(
    project_dir: Path,
    comment_id: str,
    text: str,
    *,
    actor: str,
    version: str | int | None = None,
) -> dict:
    if not isinstance(text, str) or len(text) > 20000:
        raise ValueError("Reply must be text (maximum 20000 characters)")
    message = text.strip()
    if not message:
        raise ValueError("Reply text is required")
    if actor not in {"human", "agent"}:
        raise ValueError("Reply actor must be human or agent")
    project_dir = Path(project_dir)
    with _review_lock(project_dir):
        data = _load_unlocked(project_dir)
        comment = next(
            (entry for entry in data["comments"] if entry.get("id") == comment_id),
            None,
        )
        if comment is None:
            raise KeyError(comment_id)
        if comment.get("status") == "draft":
            raise ValueError("Send the draft before replying to it")
        if comment.get("status") == "resolved":
            raise ValueError("Reopen the comment before replying to it")
        now = _now()
        reply = {"actor": actor, "text": message, "at": now}
        if version is not None:
            reply["version"] = version
        comment.setdefault("replies", []).append(reply)
        if comment.get("status") == "addressed":
            comment["status"] = "open"
            comment.pop("addressed_in", None)
        comment["updated_at"] = now
        event = {"action": "replied", "at": now, "actor": actor}
        if version is not None:
            event["version"] = version
        comment.setdefault("events", []).append(event)
        _save_unlocked(project_dir, data)
        return comment


def update_draft_comment(project_dir: Path, comment_id: str, payload: dict) -> dict:
    _validate_payload(payload)
    text = str(payload.get("text", "")).strip()
    if not text:
        raise ValueError("Comment text is required")
    anchor = payload.get("anchor")
    if not isinstance(anchor, dict) or anchor.get("kind") not in {"surface", "part", "view"}:
        raise ValueError("Comment anchor must be surface, part, or view")

    project_dir = Path(project_dir)
    with _review_lock(project_dir):
        data = _load_unlocked(project_dir)
        comment = next(
            (entry for entry in data["comments"] if entry.get("id") == comment_id),
            None,
        )
        if comment is None:
            raise KeyError(comment_id)
        if comment.get("status") != "draft":
            raise ValueError(f"Only draft comments can be edited; {comment_id} is {comment.get('status')}")
        now = _now()
        comment.update({
            "text": text,
            "source_version": payload.get("source_version"),
            "source_label": payload.get("source_label"),
            "target": payload.get("target") or {},
            "anchor": anchor,
            "view": payload.get("view") or {},
            "updated_at": now,
        })
        if payload.get("screenshot"):
            comment["screenshot"] = _store_screenshot(
                project_dir, comment_id, payload["screenshot"]
            )
        comment.setdefault("events", []).append(
            {"action": "edited", "at": now, "actor": "human"}
        )
        _save_unlocked(project_dir, data)
        return comment


def delete_draft_comment(project_dir: Path, comment_id: str) -> dict:
    project_dir = Path(project_dir)
    with _review_lock(project_dir):
        data = _load_unlocked(project_dir)
        index = next(
            (i for i, entry in enumerate(data["comments"]) if entry.get("id") == comment_id),
            None,
        )
        if index is None:
            raise KeyError(comment_id)
        comment = data["comments"][index]
        if comment.get("status") != "draft":
            raise ValueError(f"Only draft comments can be deleted; {comment_id} is {comment.get('status')}")
        data["comments"].pop(index)
        _save_unlocked(project_dir, data)
        screenshot = comment.get("screenshot")
        if screenshot:
            candidate = (project_dir / screenshot).resolve()
            try:
                candidate.relative_to((project_dir / ".agentcad" / "reviews" / "screenshots").resolve())
            except ValueError:
                pass
            else:
                candidate.unlink(missing_ok=True)
        return comment


def submit_drafts(project_dir: Path, comment_ids: list[str] | None = None) -> dict:
    if comment_ids is not None and (not isinstance(comment_ids, list) or any(
        not isinstance(value, str) for value in comment_ids
    )):
        raise ValueError("comment_ids must be a list of comment ids")
    project_dir = Path(project_dir)
    wanted = set(comment_ids or [])
    with _review_lock(project_dir):
        data = _load_unlocked(project_dir)
        drafts = [
            comment for comment in data["comments"]
            if comment.get("status") == "draft"
            and (not wanted or comment.get("id") in wanted)
        ]
        if not drafts:
            raise ValueError("No draft comments to submit")
        batch_id = f"R{int(data['next_batch'])}"
        data["next_batch"] = int(data["next_batch"]) + 1
        now = _now()
        for comment in drafts:
            comment["status"] = "open"
            comment["batch_id"] = batch_id
            comment["updated_at"] = now
            comment.setdefault("events", []).append(
                {"action": "submitted", "at": now, "actor": "human", "batch_id": batch_id}
            )
        _save_unlocked(project_dir, data)
        return {"batch_id": batch_id, "comments": drafts}


def transition_comment(
    project_dir: Path,
    comment_id: str,
    action: str,
    *,
    version: str | int | None = None,
    actor: str | None = None,
    message: str | None = None,
) -> dict:
    if message is not None and (not isinstance(message, str) or len(message) > 20000):
        raise ValueError("Message must be text (maximum 20000 characters)")
    transitions = {
        "address": ({"open"}, "addressed", actor or "agent"),
        "resolve": ({"open", "addressed"}, "resolved", actor or "human"),
        "reopen": ({"addressed", "resolved"}, "open", actor or "human"),
    }
    if action not in transitions:
        raise ValueError(f"Unknown review action '{action}'")

    project_dir = Path(project_dir)
    with _review_lock(project_dir):
        data = _load_unlocked(project_dir)
        comment = next(
            (entry for entry in data["comments"] if entry.get("id") == comment_id),
            None,
        )
        if comment is None:
            raise KeyError(comment_id)
        allowed, new_status, event_actor = transitions[action]
        if comment.get("status") not in allowed:
            raise ValueError(
                f"Comment {comment_id} cannot be {action}ed from status "
                f"'{comment.get('status')}'"
            )
        if action == "address" and version is None:
            raise ValueError("An addressed comment requires a version")
        now = _now()
        comment["status"] = new_status
        comment["updated_at"] = now
        if action == "address":
            comment["addressed_in"] = version
        elif action == "reopen":
            comment.pop("addressed_in", None)
        event = {"action": action, "at": now, "actor": event_actor}
        if version is not None:
            event["version"] = version
        note = str(message or "").strip()
        if note:
            reply = {"actor": event_actor, "text": note, "at": now, "action": action}
            if version is not None:
                reply["version"] = version
            comment.setdefault("replies", []).append(reply)
            event["message"] = note
        comment.setdefault("events", []).append(event)
        _save_unlocked(project_dir, data)
        return comment


def review_summary(project_dir: Path) -> dict:
    comments = list_comments(project_dir)
    counts = {status: 0 for status in _STATUSES}
    for comment in comments:
        status = comment.get("status")
        if status in counts:
            counts[status] += 1
    pending = [comment for comment in comments if comment.get("status") in {"open", "addressed"}]
    batches = [comment.get("batch_id") for comment in pending if comment.get("batch_id")]
    return {
        "counts": counts,
        "open_review_comments": counts["open"],
        "addressed_review_comments": counts["addressed"],
        "pending_review_batch": batches[-1] if batches else None,
    }
