"""Durable execution identities for authored CAD, separate from promotion keys.

The per-key OS lock owns execution. Once the request is published, a retry can
only read/recover its evidence; process death never permits another execution.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex

import click

from agentcad.change_store import Attempt, ChangeStore
from agentcad.import_requests import _KEY, _digest, _locked, _save
from agentcad.project import ProjectError, get_project

_LOCK_WAIT_SECONDS = 10.0


class RunRequest:
    def __init__(self, key, *, script=None, semantic=None):
        if not _KEY.fullmatch(key):
            raise ProjectError(
                "invalid_idempotency_key",
                "Run key must be 1–128 letters, digits, '.', '_', ':', or '-', starting with a letter or digit.",
                "Save one key per intended run; use a new key for an edited script.",
            )
        self.layout = get_project()
        self.layout.read_manifest()
        self.store = ChangeStore(self.layout.build_root)
        self.key = key
        self.request_id = "run_" + hashlib.sha256(key.encode()).hexdigest()
        self.relative = Path(".agentcad/run_requests") / self.request_id
        self.directory = self.store._path(self.relative)
        self.semantic = None
        self.fingerprint = None
        self.source = None
        self.replayed = False
        if script is not None:
            try:
                self.source = Path(script).read_bytes()
            except OSError as exc:
                raise self.unavailable(f"Cannot read run source: {exc}") from exc
            self.semantic = {
                **semantic,
                "script": str(Path(script).resolve()),
                "source_sha256": hashlib.sha256(self.source).hexdigest(),
                "project_root": str(self.layout.project_root),
                "execution_cwd": str(Path.cwd().resolve()),
            }
            self.fingerprint = _digest(self.semantic)

    def path(self, name):
        return self.store._path(self.relative / name)

    def unavailable(self, message):
        return ProjectError(
            "run_request_unavailable",
            message,
            "Inspect and restore the saved run records. Use a new key only to deliberately execute another run.",
        )

    def read(self, name):
        try:
            data = json.loads(self.path(name).read_text(encoding="utf-8"))
            body = {k: v for k, v in data.items() if k != "checksum"}
            if (
                data["checksum"] != _digest(body)
                or body["schema_version"] != 1
                or body["request_id"] != self.request_id
            ):
                raise ValueError("record integrity check failed")
            if self.fingerprint is not None and body["fingerprint"] != self.fingerprint:
                raise ValueError("record does not match the original request")
            return body
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise self.unavailable(f"Cannot read saved run {name}: {exc}") from exc

    def save(self, name, **fields):
        _save(
            self.path(name),
            {
                "schema_version": 1,
                "request_id": self.request_id,
                "fingerprint": self.fingerprint,
                **fields,
            },
        )

    def reference(self, state):
        return {
            "key": self.key,
            "request_id": self.request_id,
            "state": state,
            "replayed": self.replayed,
            "request": str(self.path("request.json")),
        }

    def bind_attempt(self, attempt):
        self.save("attempt.json", revision_id=attempt.revision_id)

    def complete(self, payload):
        code = 0 if payload.get("status") in ("success", "staged") else 1
        state = (
            "interrupted" if payload.get("reason") == "run_interrupted" else "complete"
        )
        payload = {**payload, "idempotency": self.reference(state)}
        self.save("response.json", payload=payload, exit_code=code)
        return payload, code

    def recover(self):
        """Observe saved outcomes/commits without changing any saved revision."""
        attempt = None
        if self.path("attempt.json").exists():
            binding = self.read("attempt.json")
            attempt = Attempt(self.store, binding["revision_id"])
            saved = self.store.read(binding["revision_id"])
            if saved["outcome"]:
                return {
                    **saved["outcome"]["response"],
                    "change": attempt.reference(original_checks=True),
                }
        tag = {"request_id": self.request_id, "fingerprint": self.fingerprint}
        for entry in self.layout.read_manifest().get("versions", []):
            if entry.get("run_request") != tag:
                continue
            version_dir = self.layout.version_dir(entry)
            meta = json.loads(
                self.store._path(Path(entry["path"]) / "meta.json").read_text()
            )
            if meta.get("run_request") != tag:
                raise self.unavailable(
                    "Version metadata does not match its run request."
                )
            result = deepcopy(meta)
            result.update(command="run", path=entry["path"])
            if meta.get("promotion_request_key"):
                from agentcad.change_promotion import PromotionStore

                result.update(
                    PromotionStore(self.layout.build_root).response_fields(
                        meta["promotion_request_key"]
                    )
                )
            for artifact in result.get("artifacts", {}).values():
                if artifact.get("status") == "pending":
                    artifact.update(
                        status="interrupted",
                        message="Run stopped before this artifact finished; it was not resumed.",
                    )
            result.setdefault("warnings", []).append(
                "Recovered the existing version. CAD and optional artifact work were not rerun."
            )
            if attempt:
                result["change"] = attempt.reference(original_checks=True)
            return result
        result = {
            "command": "run",
            "status": "error",
            "reason": "run_interrupted",
            "message": "The original run stopped before a version or terminal result was recorded. It has not been executed again.",
            "suggestion": "Inspect the saved source and any diagnostic geometry. Use a new key only to deliberately start another run.",
            "source": str(self.path("source.py")),
            "outputs": {"step": None},
        }
        if attempt:
            result["change"] = attempt.reference(original_checks=True)
        return result

    def _load_response(self):
        if self.path("response.json").exists():
            record = self.read("response.json")
            return record["payload"], record["exit_code"]
        payload = self.recover()
        return payload, 0 if payload.get("status") in ("success", "staged") else 1

    def _emit_saved(self, payload, code, *, status_only=False, state=None):
        payload = deepcopy(payload)
        original = payload.get("idempotency", {})
        recovered_state = (
            "interrupted" if payload.get("reason") == "run_interrupted" else "recovered"
        )
        payload["idempotency"] = {
            **self.reference(state or original.get("state", recovered_state)),
            "status_only": status_only,
        }
        payload["current_advanced"] = False
        payload["more_at"] = "agentcad docs run-retries"
        payload.setdefault("outputs", {}).setdefault("step", None)
        payload["artifact_created"] = payload["outputs"]["step"] is not None
        payload["viewer_opened"] = False
        if payload.get("project_viewer"):
            payload["project_viewer"]["opened"] = False
        payload.setdefault("notes", []).append(
            "This is the original run's saved result, not a new build or acceptance. Its version, artifacts and check results may differ from current work."
        )
        click.echo(json.dumps(payload))
        raise SystemExit(code)

    def _running(self, *, status_only=False):
        self._emit_saved(
            {
                "command": "run",
                "status": "in_progress",
                "outputs": {"step": None},
                "message": "The original run still owns execution; no second run was started.",
                "suggestion": shlex.join(
                    [
                        "agentcad",
                        "run",
                        "--idempotency-key",
                        self.key,
                        "--status",
                        "--build-dir",
                        str(self.layout.build_root),
                    ]
                ),
            },
            1,
            status_only=status_only,
            state="in_progress",
        )

    def lookup(self):
        """Read by key without reading source, completing records, or selecting work."""
        try:
            return self._lookup()
        except ProjectError:
            raise
        except Exception as exc:
            raise self.unavailable(f"Cannot read saved run evidence: {exc}") from exc

    def _lookup(self):
        ctx = click.get_current_context()
        ctx.meta["run_replay"] = True
        self.replayed = True
        if not self.path("request.json").exists():
            raise ProjectError(
                "run_request_not_found",
                "No saved run has this key in the selected build root.",
                "Check the key and build directory; status does not start a run.",
            )
        saved = self.read("request.json")
        self.fingerprint = saved["fingerprint"]
        with _locked(self.path("execution.lock"), wait_seconds=0) as acquired:
            if not acquired:
                self._running(status_only=True)
            payload, code = self._load_response()
            self._emit_saved(payload, code, status_only=True)

    def run(self, operation):
        ctx = click.get_current_context()
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with _locked(
                self.path("execution.lock"), wait_seconds=_LOCK_WAIT_SECONDS
            ) as acquired:
                exists = self.path("request.json").exists()
                if exists:
                    # Read before setting the expected fingerprint so an
                    # intentional input conflict is distinct from corruption.
                    expected, self.fingerprint = self.fingerprint, None
                    saved = self.read("request.json")
                    self.fingerprint = expected
                    if saved["fingerprint"] != expected:
                        raise ProjectError(
                            "idempotency_conflict",
                            "This run key is already bound to different source or options.",
                            "Read the original result with run --status --idempotency-key KEY, or use a new key for the edited run.",
                        )
                if not acquired:
                    ctx.meta["run_replay"] = True
                    self.replayed = True
                    self._running()
                if exists:
                    ctx.meta["run_replay"] = True
                    self.replayed = True
                    payload, code = self._load_response()
                    if not self.path("response.json").exists():
                        payload, code = self.complete(payload)
                    self._emit_saved(payload, code)
                # Source and request are durable before any script validation,
                # execution, attempt allocation or accepted-version reservation.
                with self.path("source.py").open("wb") as handle:
                    handle.write(self.source)
                    handle.flush()
                    os.fsync(handle.fileno())
                self.save("request.json", semantic=self.semantic)
                ctx.meta["run_request"] = self
                ctx.meta["run_request_source_bytes"] = self.source
                try:
                    return operation()
                except ProjectError as exc:
                    from agentcad.commands.run import _emit_run

                    _emit_run(exc.payload("run"))
                    raise SystemExit(1)
        except ProjectError:
            raise
        except Exception as exc:
            raise self.unavailable(f"Cannot recover saved run evidence: {exc}") from exc
        finally:
            # Outer error formatting cannot finish evidence after releasing
            # execution ownership, including errors in recovery itself.
            ctx.meta["run_request_closed"] = True


def mark_core(meta, entry):
    ctx = click.get_current_context(silent=True)
    request = ctx.meta.get("run_request") if ctx else None
    if request:
        tag = {"request_id": request.request_id, "fingerprint": request.fingerprint}
        meta["run_request"] = tag
        entry["run_request"] = tag


def finish_response(payload):
    ctx = click.get_current_context(silent=True)
    request = ctx.meta.get("run_request") if ctx else None
    if (
        not request
        or ctx.meta.get("run_replay")
        or ctx.meta.get("run_request_closed")
        or payload.get("command") != "run"
    ):
        return payload
    try:
        return request.complete(payload)[0]
    except OSError as exc:
        return {
            **payload,
            "idempotency": {
                **request.reference("recording_failed"),
                "message": str(exc),
            },
            "warnings": [
                *payload.get("warnings", []),
                "Run result was not cached; retry the same key to recover saved evidence.",
            ],
        }
