"""Recover saved work as staged successors, with durable retry identities.

Recovery owns selection/provenance, not CAD execution. Repair delegates to run;
restore copies verified snapshots and uses the existing validation/check path.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shlex

import click

from agentcad.change_history import ChangeHistory
from agentcad.change_store import (
    _REVISION,
    Attempt,
    ChangeStore,
    ChangeStoreError,
    _write_bytes_once,
)
from agentcad.import_requests import _KEY, _digest, _locked, _read, _save
from agentcad.project import ProjectError, get_project, validate_version_label
from agentcad.versioning import _version_lock


def _error(reason, message):
    return ProjectError(
        reason,
        message,
        "Inspect saved work with agentcad changes list; see agentcad docs changes.",
    )


def _snapshot(attempt, descriptor):
    data = attempt._verify(descriptor).read_bytes()
    if hashlib.sha256(data).hexdigest() != descriptor["sha256"]:
        raise ChangeStoreError("Saved evidence changed during recovery capture.")
    return data


class RecoveryRequest:
    def __init__(self, action, key):
        if not _KEY.fullmatch(key):
            raise _error(
                "invalid_idempotency_key",
                "Use 1–128 letters, digits, '.', '_', ':', or '-', starting with a letter or digit.",
            )
        self.action, self.key = action, key
        self.layout = get_project()
        self.store = ChangeStore(self.layout.build_root)
        self.request_id = "recovery_" + hashlib.sha256(key.encode()).hexdigest()
        self.directory = self.store._path(
            ".agentcad/changes/recovery_requests/" + self.request_id
        )
        self.record = None

    def read(self, name):
        try:
            record = _read(self.directory / name, self.request_id)
        except ProjectError as exc:
            raise _error(
                "recovery_request_unavailable",
                f"Cannot verify saved recovery record {name}: {exc}",
            ) from exc
        if (
            name != "request.json"
            and record.get("fingerprint") != self.record["fingerprint"]
        ):
            raise _error(
                "recovery_request_unavailable",
                "Recovery records disagree about the original request.",
            )
        return record

    def save(self, name, **fields):
        _save(
            self.directory / name,
            {
                "schema_version": 1,
                "request_id": self.request_id,
                "fingerprint": self.record["fingerprint"],
                **fields,
            },
        )

    def bind_attempt(self, attempt):
        self.save("attempt.json", revision_id=attempt.revision_id)

    def _binding(self):
        if (self.directory / "attempt.json").exists():
            return self.read("attempt.json")["revision_id"]
        # begin() seals provenance before publishing working. A process can stop
        # after that publication but before bind_attempt() writes its sidecar.
        directory = self.store._path(".agentcad/changes/attempts")
        for path in directory.iterdir() if directory.exists() else []:
            if (
                not _REVISION.fullmatch(path.name)
                or not (path / "request.json").exists()
            ):
                continue
            request = Attempt(self.store, path.name)._read(
                "request.json", "attempt_request"
            )
            if request.get("recovery", {}).get("request_id") == self.request_id:
                return path.name
        return None

    def present(self, payload):
        revision = (payload.get("change") or {}).get("revision_id")
        root = " --build-dir " + shlex.quote(str(self.layout.build_root))
        actions = (
            [
                f"agentcad changes compare {self.record['plan']['source_revision_id']} {revision}{root} — compare saved evidence",
                f"agentcad view {revision}{root} — prepare a fixed candidate review",
            ]
            if revision and (payload.get("change") or {}).get("geometry")
            else (
                [
                    f"agentcad changes show {revision}{root} — inspect saved failure evidence"
                ]
                if revision
                else []
            )
        )
        return {
            **payload,
            "command": "changes",
            "action": self.action,
            "current_advanced": False,
            "viewer_opened": False,
            "recovery": self.record["plan"]["provenance"],
            "idempotency": {
                "key": self.key,
                "request_id": self.request_id,
                "request": str(self.directory / "request.json"),
            },
            "next_actions": actions,
            "more_at": "agentcad docs changes",
        }

    def complete(self, payload):
        from agentcad.working_viewer import finish_response
        payload = finish_response({"command": "changes", **payload})
        result = self.present(payload)
        code = 0 if result.get("status") in ("success", "staged") else 1
        self.save("response.json", payload=result, exit_code=code)
        return result

    def replay(self):
        if (self.directory / "response.json").exists():
            saved = self.read("response.json")
            payload, code = saved["payload"], saved["exit_code"]
        else:
            revision = self._binding()
            attempt = Attempt(self.store, revision) if revision else None
            saved = self.store.read(revision) if revision else None
            if saved and saved["outcome"]:
                payload = self.present(
                    self.restored_response(attempt.reference(original_checks=True))
                    if self.action == "restore"
                    else {
                        **saved["outcome"]["response"],
                        "change": attempt.reference(original_checks=True),
                    }
                )
                code = 0 if payload["status"] in ("success", "staged") else 1
            else:
                payload = self.present(
                    {
                        "status": "error",
                        "reason": "recovery_interrupted",
                        "message": "The original recovery stopped. It has not been executed again. Inspect its retained evidence; use a new key only for a deliberate new attempt.",
                        **(
                            {"change": attempt.reference(original_checks=True)}
                            if attempt
                            else {}
                        ),
                    }
                )
                code = 1
        click.echo(json.dumps({**payload, "replayed": True, "current_advanced": False}))
        if code:
            raise SystemExit(code)

    def status(self):
        # No mkdir, write, source lookup, checks, or selection. A missing lock
        # means there is no durable first request to inspect.
        if not (self.directory / "execution.lock").is_file():
            raise _error(
                "recovery_request_not_found",
                "No recovery exists for this key in the selected build directory.",
            )
        with _locked(self.directory / "execution.lock", wait_seconds=0) as acquired:
            # The request is atomically published and immutable. Even while
            # execution owns the lock, it can reject another operation's key.
            if (self.directory / "request.json").exists():
                self.record = self.read("request.json")
                if self.record["action"] != self.action:
                    raise _error(
                        "idempotency_conflict",
                        "This key belongs to a different recovery operation.",
                    )
            if not acquired:
                self.in_progress()
                return
            if self.record is None:
                self.record = self.read("request.json")
            self.replay()

    def in_progress(self):
        click.echo(
            json.dumps(
                {
                    "command": "changes",
                    "action": self.action,
                    "status": "in_progress",
                    "current_advanced": False,
                    "idempotency": {"key": self.key, "request_id": self.request_id},
                    "message": "The original recovery is still running. Read this key with --status; do not start another request.",
                    "more_at": "agentcad docs changes",
                }
            )
        )

    def run(self, revision, source, label, params):
        validate_version_label(label)
        semantic = {
            "action": self.action,
            "revision": revision,
            "label": label,
            "params": params,
        }
        source_bytes = None
        if self.action == "repair":
            path = Path(source).resolve()
            if path.suffix.lower() != ".py":
                raise _error(
                    "invalid_repair_source",
                    "Repair requires an edited Python CAD script. Use restore to recover an imported CAD snapshot.",
                )
            source_bytes = path.read_bytes()
            semantic.update(
                source_path=str(path),
                source_sha256=hashlib.sha256(source_bytes).hexdigest(),
            )
        fingerprint = _digest(semantic)
        self.directory.mkdir(parents=True, exist_ok=True)
        with _locked(self.directory / "execution.lock") as acquired:
            if (self.directory / "request.json").exists():
                self.record = self.read("request.json")
                if (
                    self.record["fingerprint"] != fingerprint
                    or self.record["action"] != self.action
                ):
                    raise _error(
                        "idempotency_conflict",
                        "This recovery key already names different inputs. Use --status for the original result, or a new key for new work.",
                    )
            if not acquired:
                self.in_progress()
                return
            if self.record is not None:
                self.replay()
                return
            plan, source_bytes, snapshots = self.plan(revision, source, source_bytes)
            self.record = {
                "schema_version": 1,
                "request_id": self.request_id,
                "fingerprint": fingerprint,
                "action": self.action,
                "semantic": semantic,
                "plan": plan,
            }
            snapshot = self.directory / "sources" / Path(plan["source_name"]).name
            _write_bytes_once(snapshot, source_bytes)
            for name, data in snapshots.items():
                _write_bytes_once(self.directory / name, data)
            _save(self.directory / "request.json", self.record)
            ctx = click.get_current_context()
            begin = {
                "recovery": plan["provenance"],
                "expected_selection": plan["selection"],
                "recovery_policy": plan["policy"],
            }
            ctx.meta["change_recovery_begin"] = begin
            ctx.meta["change_recovery_request"] = self
            try:
                if self.action == "restore":
                    self.restore(label, snapshot, snapshots)
                else:
                    from agentcad.commands.run import run

                    model = plan["model"]
                    ctx.invoke(
                        run,
                        script=str(snapshot),
                        label=label,
                        stage=True,
                        params=params if params is not None else model.get("params"),
                        runtime=model.get("runtime"),
                        validation_profile=model.get(
                            "validation_profile", "deliverable"
                        ),
                        no_daemon=True,
                        preview=False,
                        auto_diff=False,
                        open_view=False,
                        experimental_changes=True,
                    )
            except (ProjectError, OSError, ValueError) as exc:
                payload = (
                    exc.payload("changes")
                    if isinstance(exc, ProjectError)
                    else {
                        "status": "error",
                        "reason": getattr(exc, "reason", "recovery_failed"),
                        "message": str(exc),
                    }
                )
                attempt = ctx.meta.get("change_attempt")
                if attempt is not None:
                    payload["change"] = attempt.reference(original_checks=True)
                click.echo(json.dumps(self.complete(payload)))
                raise SystemExit(1)
            finally:
                ctx.meta.pop("change_recovery_request", None)
                ctx.meta.pop("change_recovery_begin", None)

    def plan(self, revision, source, source_bytes):
        history = ChangeHistory(self.layout.build_root)
        # Freeze the source and intended policy together; begin checks the
        # generation again immediately before selecting the successor.
        with _version_lock(self.store.root):
            selection, _ = history._selection()
            revision_id = history._resolve(revision, selection)
            saved, _, core = history._read(revision_id)
            request = saved["request"]
            selected = selection["working_revision_id"]
            policy = (
                Attempt(self.store, selected)
                ._read("request.json", "attempt_request")
                .get("check_policy")
                if selected
                else None
            )
            policy_revision = selected if policy else revision_id
            policy = policy or request.get("check_policy")
            model = deepcopy(request["model"])
            if policy:
                model["validation_profile"] = policy["checks"][0]["settings"][
                    "validation_profile"
                ]
            old_attempt = Attempt(self.store, revision_id)
            snapshots = {}
            artifacts = []
            if self.action == "restore":
                source_bytes = _snapshot(old_attempt, request["source"])
                geometry = (
                    (saved["candidate"] or {}).get("deliverables", {}).get("cad", [])
                )
                if geometry:
                    snapshots["geometry.step"] = _snapshot(old_attempt, geometry[0])
                for i, artifact in enumerate(
                    (saved["outcome"] or {}).get("artifacts", [])
                ):
                    name = f"artifacts/{i}{Path(artifact['path']).suffix}"
                    snapshots[name] = _snapshot(old_attempt, artifact)
                    artifacts.append(
                        {
                            "file": name,
                            "role": artifact["role"],
                            "name": artifact["name"],
                        }
                    )
            elif request["command"] != "run":
                raise _error(
                    "repair_requires_script",
                    "This revision was imported. Restore its CAD snapshot, or run an edited Python script as a separate staged change.",
                )
            provenance = {
                "action": self.action,
                "source_revision_id": revision_id,
                "source_request_sha256": request["content_sha256"],
                "request_id": self.request_id,
                "requirements_revision_id": policy_revision,
                "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "original_model": request["model"],
            }
            return (
                {
                    "source_revision_id": revision_id,
                    "source_name": (
                        request["source"]["name"]
                        if self.action == "restore"
                        else Path(source).name
                    ),
                    "selection": self.store._state(),
                    "policy": policy,
                    "model": model,
                    "command": request["command"],
                    "core": core,
                    "artifacts": artifacts,
                    "unavailable_artifacts": (saved["outcome"] or {}).get(
                        "unavailable_artifacts", []
                    ),
                    "provenance": provenance,
                },
                source_bytes,
                snapshots,
            )

    def restore(self, label, snapshot, snapshots):
        from agentcad.change_retention import begin_change
        from agentcad.core_build import validated_metrics
        from agentcad.step_io import load_cad_shape
        from agentcad.native_io import silence_native_stdout

        plan = self.record["plan"]
        response = deepcopy(plan["core"])
        # Preserve execution diagnostics while dropping stale delivery paths.
        for key in (
            "change",
            "outputs",
            "preview",
            "viewer",
            "viewer_glb",
            "renders",
            "diff",
            "next_actions",
            "suggestion",
            "promotion",
        ):
            response.pop(key, None)
        response.setdefault("status", "interrupted")
        response["command"] = plan["command"]
        response["label"] = label
        response["outputs"] = {"step": None}
        for group in ("parts", "groups"):
            for part in response.get(group) or []:
                part.pop("preview", None)
        geometry = (
            self.directory / "geometry.step" if "geometry.step" in snapshots else None
        )
        if geometry:
            try:
                with silence_native_stdout():
                    response["metrics"], response["validation"] = validated_metrics(
                        load_cad_shape(geometry),
                        profile=plan["model"].get("validation_profile", "deliverable"),
                        source_path=geometry,
                    )
            except (ValueError, RuntimeError, OSError) as exc:
                # A faithfully retained failed export is recoverable evidence,
                # not corruption of its snapshot. Keep its exact bytes and
                # leave validation unavailable so it cannot be promoted.
                response["metrics"] = None
                response["validation"] = {
                    "is_valid": None,
                    "status": "unavailable",
                    "reason": str(exc),
                }
        ctx = click.get_current_context()
        ctx.meta["change_stage"] = True
        attempt = begin_change(
            command=plan["command"],
            label=label,
            source=snapshot.read_bytes(),
            source_name=plan["source_name"],
            model=plan["model"],
        )
        attempt.prepare(response, geometry=geometry)
        artifacts = []
        for item in plan["artifacts"]:
            target = attempt.directory / item["file"]
            _write_bytes_once(target, snapshots[item["file"]])
            artifacts.append(attempt._artifact(target, item["role"], item["name"]))
        reference = attempt.finish(
            response,
            restored_artifacts=artifacts,
            unavailable_artifacts=plan["unavailable_artifacts"],
        )
        ctx.meta.pop("change_recovery_request", None)
        ctx.meta.pop("change_recovery_begin", None)
        result = self.complete(self.restored_response(reference))
        click.echo(json.dumps(result))

    @staticmethod
    def restored_response(reference):
        return {
            "status": "success",
            "change": reference,
            "message": "Saved source, model and available artifact bytes were restored into new staged work. The accepted model is unchanged.",
            "notes": [
                "Current intended requirements apply. Copied artifacts are historical evidence and may embed original IDs/paths; prepare a fresh review with view."
            ],
        }


def finish_response(payload):
    ctx = click.get_current_context(silent=True)
    request = ctx.meta.get("change_recovery_request") if ctx else None
    if request and payload.get("command") in ("run", "import"):
        return request.complete(payload)
    return payload
