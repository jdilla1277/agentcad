"""Experimental working views: immutable media, live facts, no acceptance.

Only CLI preparation loads CAD. The loopback service reads retained evidence
and already published media; polling never executes source or completes checks.
"""

import hashlib
import json
from pathlib import Path
import tempfile

import click

from agentcad.change_history import ChangeHistory
from agentcad.change_store import Attempt, ChangeStore
from agentcad import project_viewer as live
from agentcad.versioning import atomic_write_json


def enable(root):
    root = Path(root).resolve()
    token = live.project_token(root)
    with live.locked():
        record = live.read_json(
            live.project_record(token), {"root": str(root), "versions": {}}
        )
        record["experimental_changes"] = True
        atomic_write_json(live.project_record(token), record)
    return token


def use_standard(root):
    """An ordinary edit returns the unpinned page to standard build follow.

    Published revision media stays addressable for previously opened pins.
    """
    if not (live.runtime_dir(create=False) / "config.json").is_file():
        return
    token = live.project_token(Path(root))
    with live.locked():
        record = live.read_json(live.project_record(token))
        if record and record.get("experimental_changes"):
            record["experimental_changes"] = False
            atomic_write_json(live.project_record(token), record)


def prepare(root, revision):
    """Publish a complete immutable HTML/GLB pair, never a partial render."""
    from agentcad.commands.view import _render_unified
    from agentcad.export import export_glb
    from agentcad.step_io import load_cad_shape
    from agentcad.import_requests import _locked
    from agentcad.native_io import silence_native_stdout

    store = ChangeStore(root)
    saved = store.read(revision)
    checked = store.read_checks(revision)
    geometry = (saved["candidate"] or {}).get("deliverables", {}).get("cad", [])
    token = enable(root)
    if not geometry or not checked["receipt"]:
        return None
    receipt = checked["receipt"]
    key = receipt["content_sha256"]
    parent = store._path(".agentcad/working_viewer/" + revision)
    parent.mkdir(parents=True, exist_ok=True)
    target = store._path(parent.relative_to(store.root) / key)
    with _locked(parent / "prepare.lock") as acquired:
        if not acquired:
            raise live.ViewerUnavailable(
                "Candidate view is being prepared; retry view REVISION."
            )
        if not target.exists():
            with tempfile.TemporaryDirectory(
                prefix="prepare-", dir=parent
            ) as temporary:
                directory = Path(temporary)
                source = Attempt(store, revision)._verify(geometry[0])
                with silence_native_stdout():
                    export_glb(load_cad_shape(source), str(directory / "output.glb"))
                _render_unified(
                    directory / "viewer.html",
                    directory / "output.glb",
                    label_a=revision,
                    default_mode="spec",
                    review={"change_receipt": receipt, "check_policy": saved["policy"]},
                )
                # Detect changed source/geometry/check dependencies before any
                # render is addressable. Rename the whole pair atomically.
                store.read(revision)
                directory.rename(target)
        html = store._path(target.relative_to(store.root) / "viewer.html")
        glb = store._path(target.relative_to(store.root) / "output.glb")
        previous = (
            live.read_json(live.project_record(token), {})
            .get("revision_artifacts", {})
            .get(f"revisions/{revision}/{key}/viewer.html")
        )
        if previous and (
            hashlib.sha256(html.read_bytes()).hexdigest() != previous["sha256"]
            or hashlib.sha256(glb.read_bytes()).hexdigest() != previous["glb_sha256"]
        ):
            raise live.ViewerUnavailable(
                "Published candidate media changed; retained source is unchanged."
            )
        item = {
            "revision_id": revision,
            "version": revision,
            "label": saved["request"]["label"],
            "receipt_sha256": key,
            "generation": saved["request"]["selection_generation"],
            "artifact": f"revisions/{revision}/{key}/viewer.html",
            "html": str(html.relative_to(store.root)),
            "sha256": hashlib.sha256(html.read_bytes()).hexdigest(),
            "glb_sha256": hashlib.sha256(glb.read_bytes()).hexdigest(),
        }
        with live.locked():
            record = live.read_json(live.project_record(token))
            record.setdefault("view_failures", {}).pop(revision, None)
            record.setdefault("revision_artifacts", {})[item["artifact"]] = item
            # A late rendering of an old receipt cannot overwrite the latest
            # prepared receipt for this revision.
            if store.read_checks(revision)["receipt"]["content_sha256"] == key:
                record.setdefault("working_snapshots", {})[revision] = item
            if store.working_revision_id == revision:
                record["last_working_visual"] = item
            atomic_write_json(live.project_record(token), record)
    return item


def _summary(shown):
    row = shown["change"]
    checked = shown["checks"]
    previously_accepted = any(
        item.get("promotion", {}).get("outcome") == "promoted"
        for item in shown["promotions"]
    )
    if row["is_accepted"]:
        status = "Accepted"
    elif previously_accepted:
        status = "Previously accepted"
    elif row["result_status"] == "not_promoted":
        status = "Not accepted"
    elif row["validation_state"] == "invalid":
        status = "Failed"
    elif row["check_lifecycle"] in ("pending", "running"):
        status = "Checking"
    elif row["validation_state"] == "valid":
        status = (
            "Staged" if row["promotion_mode"] == "staged" else "Awaiting acceptance"
        )
    else:
        status = "Unavailable"
    return {
        key: row[key]
        for key in (
            "revision_id",
            "label",
            "promotion_mode",
            "validation_state",
            "geometry_available",
            "result_status",
        )
    } | {
        "status": status,
        "promotion_ready": checked["promotion_ready"],
        "receipt": shown["records"]["original_receipt"],
    }


def _available(root, record, visual):
    if not visual:
        return False
    try:
        if visual.get("revision_id"):
            artifact(root, visual)
        else:
            path = Path(record["versions"][str(visual["version"])])
            if (
                path.resolve() != path
                or path.parent.parent != Path(root).resolve()
                or not path.is_file()
            ):
                return False
        return True
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return False


def _unavailable(revision, error):
    return {
        "revision_id": revision,
        "status": "Unavailable",
        "message": str(error)[:2000],
    }


def _read_summary(history, revision, selection, promotion_state):
    if revision is None:
        return None
    try:
        return _summary(history._show(revision, selection, promotion_state))
    except Exception as exc:
        return _unavailable(revision, exc)


def _verified_visual(root, record, chosen, *, follow, generation=None):
    """Use the same media checks even when revision evidence is unreadable."""
    visual = record.get("working_snapshots", {}).get(chosen)
    missing_preview = visual is not None and not _available(root, record, visual)
    if visual is not None and not missing_preview:
        return visual, False
    if not follow:
        return None, missing_preview
    candidates = [
        record.get("last_working_visual"),
        *sorted(
            record.get("working_snapshots", {}).values(),
            key=lambda item: item["generation"],
            reverse=True,
        ),
        record.get("latest"),
    ]
    seen = set()
    for candidate in candidates:
        if not candidate or candidate["artifact"] in seen:
            continue
        seen.add(candidate["artifact"])
        if generation is not None and candidate.get("generation", 0) > generation:
            continue
        if _available(root, record, candidate):
            return candidate, missing_preview
    return None, missing_preview


def state(root, record, revision=None):
    history = ChangeHistory(root)
    chosen, working_id, accepted, generation = revision, None, None, None
    try:
        selection, promotion_state = history._selection()
        working_id = selection["working_revision_id"]
        generation = selection["selection_generation"]
        chosen = revision or working_id
        accepted = {
            "revision_id": selection["accepted_revision_id"],
            "version": selection["accepted_version"]["version"],
            "label": selection["accepted_version"]["label"],
        }
    except Exception as exc:
        working = _unavailable(working_id, exc)
        selected = _unavailable(chosen, exc)
    else:
        # Each revision has its own evidence failure. Damage to newer work must
        # not suppress an independently verified historical review pin.
        working = _read_summary(history, working_id, selection, promotion_state)
        selected = (
            _read_summary(history, revision, selection, promotion_state)
            if revision and revision != working_id
            else working
        )
    visual, missing_preview = _verified_visual(
        root, record, chosen, follow=revision is None, generation=generation
    )
    if selected and chosen in record.get("view_failures", {}):
        selected.update(
            preview_state="unavailable", message=record["view_failures"][chosen]
        )
    if selected and missing_preview:
        selected["preview_state"] = "unavailable"
        selected.setdefault(
            "message",
            "Prepared viewer media is missing or changed; inspect retained evidence.",
        )
    attempt_status = (working or {}).get("result_status")
    if working and working["status"] == "Accepted":
        attempt_status = "success"
    elif not attempt_status:
        attempt_status = (
            "unavailable"
            if working and working["status"] == "Unavailable"
            else "checking"
        )
    return {
        "experimental_changes": True,
        "working": working,
        "selected": selected,
        "attempt": {
            "status": attempt_status,
            "label": (working or {}).get("label"),
        },
        "accepted": accepted,
        "displayed": visual,
        "latest": visual,
        "pinned_revision_id": revision,
        "older_geometry": bool(visual and visual.get("revision_id") != chosen),
    }


def evidence(root, revision):
    history = ChangeHistory(root)
    shown = history.show(revision)
    accepted = shown["selection"]["accepted_revision_id"]
    comparison = None
    if accepted:
        try:
            comparison = history.compare(accepted, revision)
        except Exception as exc:
            comparison = {"status": "unavailable", "message": str(exc)[:2000]}
    return {
        "revision_id": revision,
        "accepted": {
            "revision_id": accepted,
            "version": shown["selection"]["accepted_version"]["version"],
        },
        "checks": shown["checks"],
        "diagnostics": shown["diagnostics"],
        "comparison": comparison,
        "source": shown["source"],
        "records": shown["records"],
        "notes": [
            "Comparison uses saved measurements and check evidence; it is not a new occupied-space geometry analysis."
        ],
    }


def artifact(root, item):
    store = ChangeStore(root)
    file = store._path(item["html"])
    data = file.read_bytes()
    if hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise live.ViewerUnavailable(
            "Candidate viewer bytes changed; prepare the view again."
        )
    return data


def reference(root, revision, *, open_browser=False, pinned=False):
    from agentcad.commands.view import _open_browser

    token = enable(root)
    record = live.read_json(live.project_record(token))
    config = live.ensure_service() if open_browser else live.configuration()
    url = (
        f"http://127.0.0.1:{config['port']}/projects/{token}/"
        if config.get("port")
        else None
    )
    if pinned and url:
        url += "?revision=" + revision
    result = {
        "revision_id": revision,
        "opened": False,
        "pinned_revision_id": revision if pinned else None,
    }
    if url:
        result["url"] = url
    item = record.get("working_snapshots", {}).get(revision)
    if item:
        result["snapshot"] = str(Path(root) / item["html"])
    if open_browser:
        if pinned:
            result["opened"] = _open_browser(url) is not False
        else:
            result.update(live.open_project(root, _open_browser))
    return result


def finish_response(payload):
    """Prepare only newly executed/selected work, never replay/status/history."""
    ctx = click.get_current_context(silent=True)
    if (
        not ctx
        or not ctx.meta.get("experimental_changes")
        or ctx.meta.get("working_viewer_handled")
    ):
        return payload
    attempt = ctx.meta.get("change_attempt")
    if attempt is None or payload.get("command") not in (
        "run",
        "import",
        "changes",
        "check-spec",
    ):
        return payload
    if any(
        ctx.meta.get(key)
        for key in (
            "run_replay",
            "import_replay",
            "run_request_closed",
            "import_request_closed",
        )
    ):
        return payload
    ctx.meta["working_viewer_handled"] = True
    try:
        prepare(attempt.store.root, attempt.revision_id)
        result = reference(
            attempt.store.root,
            attempt.revision_id,
            open_browser=ctx.params.get("open_view", False),
        )
        result["opened"] = bool(
            result.get("opened") or (payload.get("project_viewer") or {}).get("opened")
        )
        return {**payload, "project_viewer": result, "viewer_opened": result["opened"]}
    except Exception as exc:
        try:
            token = enable(attempt.store.root)
            with live.locked():
                record = live.read_json(live.project_record(token))
                record.setdefault("view_failures", {})[attempt.revision_id] = str(exc)[
                    :2000
                ]
                atomic_write_json(live.project_record(token), record)
        except Exception:
            pass
        return {
            **payload,
            "project_viewer": {
                "status": "unavailable",
                "opened": False,
                "revision_id": attempt.revision_id,
                "message": str(exc),
            },
        }
