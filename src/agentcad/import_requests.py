"""Durable, project-scoped retry identities for local CAD imports.

The OS lock owns execution, not a timestamp or PID. A stopped process leaves
evidence to recover, never permission to start the import a second time.
Analysis resumption is deliberately separate from request replay.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import time

import click

from agentcad.project import ProjectError, get_project
from agentcad.versioning import atomic_write_json

_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_LOCK_WAIT_SECONDS = 10.0


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def _save(path, body):
    atomic_write_json(path, {**body, "checksum": _digest(body)})


def _read(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        body = {k: v for k, v in data.items() if k != "checksum"}
        if data["checksum"] != _digest(body) or body["schema_version"] != 1:
            raise ValueError("record integrity check failed")
        return body
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ProjectError("import_request_unavailable", f"Cannot read import retry record {path}: {exc}",
                           "Restore the retry record; do not use a new key unless another import is intended.") from exc


@contextmanager
def _locked(path):
    with path.open("a+b") as handle:
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

        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        while True:
            try:
                acquire()
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(0.05)
            except OSError as exc:
                # Windows reports a contended byte-range lock as EACCES.
                if os.name != "nt" or exc.errno not in (13, 36):
                    raise
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(0.05)
        try:
            yield True
        finally:
            release()


class ImportRequest:
    def __init__(self, key, *, file, semantic):
        if not _KEY.fullmatch(key):
            raise ProjectError("invalid_idempotency_key", "Import key must be 1–128 letters, digits, '.', '_', ':', or '-', starting with a letter or digit.",
                               "Use one key per intended import and preserve it for retries.")
        self.layout = get_project()
        self.request_id = "import_" + hashlib.sha256(key.encode()).hexdigest()
        self.directory = self.layout.artifact_path(".agentcad/import_requests/" + self.request_id)
        self.key = key
        try:
            if not Path(file).is_file():
                raise OSError("source is not a readable regular file")
            self.source = Path(file).read_bytes()
        except OSError as exc:
            raise ProjectError("import_source_unavailable", f"Cannot read import source {file}: {exc}",
                               "Keep the original source available with identical bytes when retrying.") from exc
        self.semantic = {**semantic, "source_filename": Path(file).name,
                         "source_sha256": hashlib.sha256(self.source).hexdigest(),
                         "project_root": str(self.layout.project_root)}
        self.fingerprint = _digest(self.semantic)
        self.source_path = self.directory / ("source" + Path(file).suffix.lower())
        self.replayed = False
        self.recovery = False

    def reference(self, state):
        return {"key": self.key, "request_id": self.request_id, "state": state,
                "replayed": self.replayed, "request": str(self.directory / "request.json")}

    def bind_attempt(self, attempt):
        _save(self.directory / "attempt.json", {"schema_version": 1, "fingerprint": self.fingerprint,
                                                "revision_id": attempt.revision_id})

    def complete(self, payload, exit_code):
        state = "recovered" if self.recovery else "complete"
        if payload.get("reason") == "import_interrupted":
            state = "interrupted"
        payload = {**payload, "idempotency": self.reference(state)}
        _save(self.directory / "response.json", {"schema_version": 1, "fingerprint": self.fingerprint,
                                                 "exit_code": exit_code, "payload": payload})
        return payload

    def recover(self):
        """Prefer a terminal retained outcome, then the committed version.

        The request tag is written with version metadata before registration,
        so a crash immediately after commit cannot lose the version binding.
        """
        self.recovery = True
        attempt = None
        attempt_path = self.directory / "attempt.json"
        if attempt_path.exists():
            from agentcad.change_store import Attempt, ChangeStore

            binding = _read(attempt_path)
            if binding["fingerprint"] != self.fingerprint:
                raise ProjectError("import_request_unavailable", "Retained attempt does not match this import request.",
                                   "Restore the retry records before continuing.")
            store = ChangeStore(self.layout.build_root)
            attempt = Attempt(store, binding["revision_id"])
            saved = store.read(binding["revision_id"])
            if saved["outcome"]:
                return {**saved["outcome"]["response"],
                        "change": attempt.reference()}
        if self.layout.manifest_path.exists():
            for entry in self.layout.read_manifest().get("versions", []):
                if entry.get("import_request") != {"request_id": self.request_id, "fingerprint": self.fingerprint}:
                    continue
                version_dir = self.layout.version_dir(entry)
                meta = json.loads((version_dir / "meta.json").read_text(encoding="utf-8"))
                if meta.get("import_request") != {"request_id": self.request_id, "fingerprint": self.fingerprint}:
                    raise ProjectError("import_request_unavailable", "Committed version metadata does not match its import request.",
                                       "Restore the original metadata; no import was started.")
                result = deepcopy(meta)
                interrupted = False
                for artifact in result.get("artifacts", {}).values():
                    if artifact.get("status") == "pending":
                        interrupted = True
                        artifact.update(status="interrupted", message="Import stopped before this artifact finished.")
                result["warnings"] = [*result.get("warnings", []),
                                      ("Recovered the existing version. Optional work was interrupted and has not been resumed."
                                       if interrupted else "Recovered the existing version after its response was interrupted.")]
                # Only mutable artifact state changes. Geometry and issued IDs
                # remain untouched, and current/working selections never move.
                atomic_write_json(version_dir / "meta.json", result)
                result["path"] = entry["path"]
                result["viewer_opened"] = False
                step = (result.get("outputs") or {}).get("step")
                if step:
                    result["next_actions"] = [shlex.join(["agentcad", "inspect", str(version_dir / "output.step"),
                                                         "--ids", "--build-dir", str(self.layout.build_root)])]
                return self._finish_recovery(result, attempt)
        return self._finish_recovery({
            "command": "import", "status": "error", "reason": "import_interrupted",
            "message": "The original import stopped before a version was committed. It has not been run again.",
            "suggestion": "Inspect the retained request/source and any change attempt. Use a new key only to deliberately start another import.",
            "source": str(self.source_path),
            "notes": ["Any change.geometry is retained diagnostic geometry, not an accepted version."],
        }, attempt)

    def _finish_recovery(self, payload, attempt):
        if self.replayed and attempt is not None:
            # Close a stopped attempt once without selecting it as new work.
            response = {k: v for k, v in payload.items() if k != "change"}
            payload = {**response, "change": attempt.finish(response)}
        return payload

    def run(self, operation):
        try:
            return self._run(operation)
        except ProjectError:
            raise
        except Exception as exc:
            raise ProjectError("import_request_unavailable", f"Cannot recover import retry evidence: {exc}",
                               "Restore the retry records; no second import was started.") from exc

    def _run(self, operation):
        ctx = click.get_current_context()
        self.directory.mkdir(parents=True, exist_ok=True)
        with _locked(self.directory / "execution.lock") as acquired:
            if not acquired:
                request_path = self.directory / "request.json"
                if request_path.exists() and _read(request_path)["fingerprint"] != self.fingerprint:
                    raise ProjectError("idempotency_conflict", "This import key is already bound to a different source or options.",
                                       "Retry with the original bytes/options, or choose a new key for a new import.")
                ctx.meta["import_replay"] = True
                self.replayed = True
                click.echo(json.dumps({"command": "import", "status": "in_progress",
                                       "message": "This import is still running. Retry the same key and original options; no second import was started.",
                                       "idempotency": self.reference("in_progress")}))
                raise SystemExit(1)
            request_path = self.directory / "request.json"
            if request_path.exists():
                saved = _read(request_path)
                if saved["fingerprint"] != self.fingerprint:
                    raise ProjectError("idempotency_conflict", "This import key is already bound to a different source or options.",
                                       "Retry with the original bytes/options, or choose a new key for a new import.")
                if hashlib.sha256(self.source_path.read_bytes()).hexdigest() != self.semantic["source_sha256"]:
                    raise ProjectError("import_request_unavailable", "The retained import source no longer matches its request.",
                                       "Restore the retained source before retrying; no import was started.")
                ctx.meta["import_replay"] = True
                self.replayed = True
                response_path = self.directory / "response.json"
                if response_path.exists():
                    saved = _read(response_path)
                    if saved["fingerprint"] != self.fingerprint:
                        raise ProjectError("import_request_unavailable", "The import response does not match its request.",
                                           "Restore the retry records before continuing.")
                    payload, exit_code = saved["payload"], saved["exit_code"]
                    payload["idempotency"]["replayed"] = True
                else:
                    payload = self.recover()
                    exit_code = 0 if payload.get("status") == "success" else 1
                    payload = self.complete(payload, exit_code)
                if "viewer_opened" in payload:
                    payload["viewer_opened"] = False
                if payload.get("project_viewer"):
                    payload["project_viewer"]["opened"] = False
                click.echo(json.dumps(payload))
                raise SystemExit(exit_code)

            # A crash before publishing request.json cannot have started CAD.
            # A complete request always precedes parsing or version reservation.
            with self.source_path.open("wb") as handle:
                handle.write(self.source)
                handle.flush()
                os.fsync(handle.fileno())
            _save(request_path, {"schema_version": 1, "request_id": self.request_id,
                                 "fingerprint": self.fingerprint, "semantic": self.semantic})
            ctx.meta["import_request"] = self
            ctx.meta["import_request_source"] = self.source_path
            try:
                return operation()
            except Exception as exc:
                # Keep terminal recording inside the execution lock, including
                # unexpected errors. SystemExit/interrupts leave recovery evidence.
                if isinstance(exc, ProjectError):
                    payload = exc.payload("import")
                else:
                    payload = self.recover()
                    payload.setdefault("warnings", []).append(f"Import stopped: {type(exc).__name__}: {exc}")
                ctx.meta["import_exit_code"] = 0 if payload.get("status") == "success" else 1
                click.echo(json.dumps(payload))
                raise SystemExit(ctx.meta["import_exit_code"])


def mark_core(payload):
    ctx = click.get_current_context(silent=True)
    request = ctx.meta.get("import_request") if ctx else None
    if request:
        payload["import_request"] = {"request_id": request.request_id, "fingerprint": request.fingerprint}


def finish_response(payload):
    ctx = click.get_current_context(silent=True)
    request = ctx.meta.get("import_request") if ctx else None
    if not request or ctx.meta.get("import_replay") or payload.get("command") != "import":
        return payload
    exit_code = ctx.meta.get("import_exit_code", 0 if payload.get("status") == "success" else 1)
    try:
        return request.complete(payload, exit_code)
    except OSError as exc:
        return {**payload, "idempotency": {**request.reference("recording_failed"), "message": str(exc)},
                "warnings": [*payload.get("warnings", []), "Import result could not be cached; retry the same key to recover it."]}
