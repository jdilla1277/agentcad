"""Explicit acceptance and status lookup for retained CAD candidates."""

import json
import shlex
import sys
import time

import click

from agentcad.change_promotion import PromotionStore, _current_target, _legacy_current
from agentcad.project import ProjectError, get_project, project_options


@click.command()
@click.argument("revision", required=False)
@click.option(
    "--idempotency-key",
    default=None,
    help="Required: save this key before sending; repeat it to resume or replay the same decision.",
)
@click.option(
    "--status",
    "status_only",
    is_flag=True,
    help="Read the saved operation without accepting or changing any selection.",
)
@click.option(
    "--target",
    "target_id",
    default="cad",
    show_default=True,
    help="Deliverable target; this adapter supports cad.",
)
@project_options
def promote(revision, idempotency_key, status_only, target_id):
    """Accept an exact retained REVISION without executing its source again.

    A stage remains selected but unaccepted even when its checks pass. Use a
    caller-known key to accept it explicitly; retrying that key resumes pending
    copying or returns its immutable receipt. New evidence after refusal needs
    a new key. --status omits REVISION and reads the original decision only.
    """
    store = PromotionStore(get_project().build_root)
    started_ns = time.time_ns()
    replayed = False
    try:
        if not idempotency_key:
            raise ProjectError(
                "promotion_key_required",
                "A caller-known --idempotency-key is required.",
                "Choose and save one key before requesting promotion; reuse it for retries and --status.",
            )
        if status_only:
            operation = store.read(idempotency_key)
            if (
                revision
                and revision != operation["request"]["candidate"]["revision_id"]
            ) or target_id != operation["request"]["target_id"]:
                raise ProjectError(
                    "promotion_request_conflict",
                    "This key names another revision.",
                    "Use the original revision or omit it for --status.",
                )
        else:
            if not revision:
                raise ProjectError(
                    "promotion_revision_required",
                    "Pass a retained revision ID, or use --status.",
                    "Inspect the staged result's change.revision_id.",
                )
            replayed = store.promote(
                revision, request_key=idempotency_key, target_id=target_id
            )["replayed"]
            operation = store.read(idempotency_key)
        result = operation["result"]
        manifest = store._manifest()
        now = _current_target(manifest, store._state(manifest), "cad")
        advanced = (
            not status_only and not replayed and result.get("outcome") == "promoted"
        )
        accepted = (
            operation["accepted_version"]
            if result.get("outcome") == "promoted"
            else None
        )
        payload = {
            "command": "promote",
            "status": (
                "not_promoted" if result.get("outcome") == "not_promoted" else "success"
            ),
            "request_key": idempotency_key,
            "revision_id": operation["request"]["candidate"]["revision_id"],
            **store.response_fields(idempotency_key),
            "current_advanced": advanced,
            "status_only": status_only,
            "replayed": replayed,
            "accepted_revision_now": now["revision_id"] if now else None,
            "accepted_version_now": _legacy_current(manifest)["version"],
            "working_revision_now": store.changes.working_revision_id,
            "outputs": {"step": accepted["path"] + "output.step" if accepted else None},
            "more_at": "agentcad docs promote",
        }
        root = (
            " --build-dir " + shlex.quote(str(store.root))
            if get_project().configured
            else ""
        )
        if accepted:
            payload.update(version=accepted["version"], label=accepted["label"])
            plan = operation.get("materialization")
            if plan:
                payload["outputs"] = dict(plan["meta"]["outputs"])
            if advanced:
                try:
                    from agentcad.project_viewer import record_failure

                    record_failure(
                        store.root,
                        f"v{accepted['version']} · {accepted['label']}",
                        "accepted_without_viewer",
                        started_ns=started_ns,
                    )
                except (OSError, TimeoutError) as exc:
                    payload["warnings"] = [
                        f"The model was accepted, but the live viewer status could not update: {exc}"
                    ]
        if replayed or status_only:
            payload["notes"] = [
                "The receipt and before/after versions describe the original decision; this lookup/replay changes no selection."
            ]
        if result.get("lifecycle") != "completed":
            payload["status"] = "pending"
            payload["suggestion"] = (
                f"agentcad promote {payload['revision_id']} --idempotency-key {shlex.quote(idempotency_key)}{root} — resume the same saved operation"
            )
        elif result.get("outcome") == "not_promoted":
            payload["suggestion"] = (
                f"agentcad check-spec {shlex.quote(payload['revision_id'])}{root} — inspect saved checks; after repair or new evidence, use a new promotion key"
            )
        elif not status_only:
            step = shlex.quote(
                get_project().response_path(store.root / payload["outputs"]["step"])
            )
            payload["next_actions"] = [
                f"agentcad view {step}{root} — inspect the accepted model",
                f"agentcad inspect {step}{root} — inspect the saved geometry",
            ]
        click.echo(json.dumps(payload))
        if payload["status"] == "not_promoted":
            sys.exit(1)
    except ProjectError as exc:
        click.echo(
            json.dumps(
                {
                    **exc.payload("promote"),
                    "request_key": idempotency_key,
                    "revision_id": revision,
                    "current_advanced": False,
                    "outputs": {"step": None},
                }
            )
        )
        sys.exit(1)
    except Exception as exc:
        click.echo(
            json.dumps(
                {
                    "command": "promote",
                    "status": "error",
                    "reason": "promotion_unavailable",
                    "message": f"Could not finish or read promotion: {exc}",
                    "request_key": idempotency_key,
                    "revision_id": revision,
                    "current_advanced": False,
                    "outputs": {"step": None},
                    "more_at": "agentcad docs promote",
                    "suggestion": f"Inspect the retained revision and retry the same key, or run agentcad promote --idempotency-key {shlex.quote(idempotency_key)} --status.",
                }
            )
        )
        sys.exit(1)
