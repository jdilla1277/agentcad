"""Version-bound local inspection jobs; OS ownership and verified stage checkpoints."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

from agentcad.geometry_manifest import _digest, read_manifest
from agentcad.import_requests import _locked
from agentcad.inspection_report import (
    DEFAULT_TEXT_BUDGET, REPORT_REVISION, _read_meta,
    analysis_inputs, compact_report, inspection_report,
)
from agentcad.geometry_images import IMAGE_REVISION
from agentcad.versioning import atomic_write_json
from agentcad.inspection_cache import cache_lease, CacheResourceLimit
from agentcad.project import ProjectLayout

STARTUP_SECONDS = 30
_JOB = re.compile(r"inspection_[0-9a-f]{64}\Z")


class JobError(ValueError):
    pass


class _Canceled(BaseException):
    pass


def _write(path, data):
    atomic_write_json(path, {**data, "checksum": _digest(data)})


def _read(path, job_id):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        checksum = data.pop("checksum")
        if checksum != _digest(data) or data["job_id"] != job_id or data["schema_version"] != 1:
            raise ValueError("record identity or checksum mismatch")
        return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise JobError(f"Inspection job evidence unavailable at {path}: {exc}") from exc


def _artifact(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _verify(artifacts):
    for row in artifacts:
        try:
            if _artifact(row["path"]) != row:
                raise ValueError("hash mismatch")
        except (OSError, ValueError) as exc:
            raise JobError(f"Completed inspection checkpoint unavailable: {row['path']}: {exc}") from exc


class InspectionJob:
    def __init__(self, directory, job_id):
        self.directory, self.job_id = Path(directory), job_id
        self.request = _read(self.directory / "request.json", job_id)
        if "inspection_" + _digest(self.request["identity"]) != job_id:
            raise JobError("Inspection job request does not match its ID.")

    @classmethod
    def prepare(cls, step, output_root, layout, *, images=True, max_images=9, meta=None):
        if not 0 <= max_images <= 9:
            raise JobError("max_images must be between 0 and 9.")
        step, output_root = Path(step).resolve(), Path(output_root).resolve()
        saved = read_manifest(step)
        meta = meta if meta is not None else _read_meta(step)
        # Freeze only report evidence, not mutable artifact lifecycle fields.
        evidence = {key: meta[key] for key in ("command", "version", "geometry_manifest", "sha256", "validation", "metrics") if key in meta}
        inputs = analysis_inputs(saved, evidence)
        identity = {
            "project_root": str(layout.project_root), "build_root": str(layout.build_root),
            "step": str(step), "output_root": str(output_root),
            "manifest_id": saved["manifest_id"], "version": saved["version"],
            "baseline_sha256": saved["baseline"]["sha256"],
            "analysis_revision": _digest(inputs), "report_revision": REPORT_REVISION,
            "image_revision": IMAGE_REVISION, "images": images, "max_images": max_images,
            "requested_stages": ["analysis", "sample_images" if images and max_images else "no_images", "catalogue"],
        }
        job_id = "inspection_" + _digest(identity)
        directory = output_root / "inspection" / "jobs" / job_id
        directory.mkdir(parents=True, exist_ok=True)
        with _locked(directory / "state.lock") as acquired:
            if not acquired:
                raise JobError("Inspection job state is busy; retry the same request.")
            if not (directory / "request.json").exists():
                state_path = directory / "state.json"
                if state_path.exists():
                    prior = _read(state_path, job_id)
                    if prior["status"] != "stopped" or prior["run_token"] is not None:
                        raise JobError("Inspection job request is missing; restore its original evidence before resuming.")
                elif any(directory.glob("result_*.json")):
                    raise JobError("Inspection job records are missing; restore the original evidence.")
                _write(directory / "state.json", {"schema_version": 1, "job_id": job_id,
                    "status": "stopped", "stages": {"analysis": {"status": "pending"}, "catalogue": {"status": "pending"}},
                    "cancel_requested": False, "run_token": None})
                _write(directory / "request.json", {"schema_version": 1, "job_id": job_id,
                                                       "identity": identity, "meta": evidence})
        return cls(directory, job_id)

    @classmethod
    def open(cls, step, output_root, layout, job_id):
        if not _JOB.fullmatch(job_id):
            raise JobError("Use the inspection job ID returned by import or inspect --report.")
        # Standalone reports live beside the baseline. Returned actions make
        # the build root explicit, which can change derived_dir for external
        # inputs. Check that original location too, without moving the job.
        roots = dict.fromkeys((Path(output_root).resolve(), Path(step).resolve().parent))
        for root in roots:
            directory = root / "inspection" / "jobs" / job_id
            if directory.exists():
                break
        else:
            directory = Path(output_root) / "inspection" / "jobs" / job_id
            root = Path(output_root).resolve()
        job = cls(directory, job_id)
        identity = job.request["identity"]
        if (identity["step"] != str(Path(step).resolve()) or identity["output_root"] != str(root)
                or identity["project_root"] != str(layout.project_root) or identity["build_root"] != str(layout.build_root)):
            raise JobError("Inspection job belongs to another baseline or project/build root.")
        return job

    @contextmanager
    def _state(self):
        with _locked(self.directory / "state.lock") as acquired:
            if not acquired:
                raise JobError("Inspection job state is busy; retry the same job.")
            state = _read(self.directory / "state.json", self.job_id)
            original = deepcopy(state)
            yield state
            if state != original:
                _write(self.directory / "state.json", state)

    def _owned(self):
        with _locked(self.directory / "execution.lock", wait_seconds=0) as acquired:
            return not acquired

    def status(self):
        with self._state() as state:
            owned = self._owned()
            if state["status"] == "running" and not owned:
                state["status"] = "canceled" if state["cancel_requested"] else "stopped"
                state["message"] = "Worker stopped; completed checkpoints are retained."
                for stage in state["stages"].values():
                    if stage["status"] == "running":
                        stage["status"] = "stopped"
            if state["status"] == "queued" and time.time() > state["launch_deadline"] and not owned:
                state.update(status="stopped", run_token=None,
                             message="Worker did not start within the 30-second startup allowance; resume this job.")
            snapshot = deepcopy(state)
        identity = self.request["identity"]
        base = ["agentcad", "inspect", identity["step"], "--report", "--job", self.job_id,
                "--build-dir", identity["build_root"]]
        return {
            "id": self.job_id, "status": snapshot["status"], "worker_running": owned,
            "cancel_requested": snapshot["cancel_requested"],
            "stages": {name: {"status": stage["status"], "completed_artifacts": len(stage.get("artifacts", [])),
                               **({"message": stage["message"]} if "message" in stage else {})}
                       for name, stage in snapshot["stages"].items()},
            "manifest_id": identity["manifest_id"], "version": identity["version"],
            "analysis_revision": identity["analysis_revision"],
            "configuration": {key: identity[key] for key in ("images", "max_images", "report_revision", "image_revision", "requested_stages")},
            "recognition": {"status": "not_available", "recognized_feature_count": None},
            "request": str(self.directory / "request.json"),
            "checkpoints": str(self.directory / "state.json"),
            "message": snapshot.get("message"),
            "actions": {action: shlex.join([*base, "--job-action", action]) for action in ("status", "wait", "resume", "cancel")},
        }

    def _validate(self, *, software=False):
        identity = self.request["identity"]
        saved = read_manifest(identity["step"])
        baseline_matches = (saved["manifest_id"] == identity["manifest_id"]
                            and saved["baseline"]["sha256"] == identity["baseline_sha256"])
        software_matches = (identity["report_revision"] == REPORT_REVISION and identity["image_revision"] == IMAGE_REVISION
                            and _digest(analysis_inputs(saved, self.request["meta"])) == identity["analysis_revision"])
        if not baseline_matches or software and not software_matches:
            raise JobError("Baseline or analysis software changed; this job cannot be resumed. Request a new report.")

    def _checkpoint(self, name, status, paths):
        with self._state() as state:
            canceled = state["cancel_requested"]
            if canceled and status == "running":
                raise _Canceled()
            previous = state["stages"].get(name, {})
            if previous.get("status") == "complete":
                _verify(previous["artifacts"])
                if status == "running":
                    return
            artifacts = [_artifact(p) for p in paths]
            if previous.get("status") == "complete" and artifacts != previous["artifacts"] and name != "catalogue":
                raise JobError("Completed inspection stage changed; retained evidence cannot be replaced.")
            state["stages"][name] = {"status": status, "artifacts": artifacts}
        if canceled:
            raise _Canceled()

    def _work(self, token):
        with _locked(self.directory / "execution.lock", wait_seconds=0) as acquired:
            if not acquired:
                return
            with self._state() as state:
                if state["run_token"] != token or state["status"] in {"complete", "canceled"}:
                    return
                if state["status"] == "queued" and time.time() > state["launch_deadline"]:
                    state.update(status="stopped", run_token=None, message="Worker startup allowance expired.")
                    return
                state.update(status="running", message=None)
            try:
                self._validate(software=True)
                identity = self.request["identity"]
                # This is a saved presentation template, not inline delivery.
                # Caller-specific text budgets are applied only when retrieved.
                layout = ProjectLayout(Path(identity["project_root"]), Path(identity["build_root"]))
                with cache_lease(identity["output_root"], layout=layout) as cache:
                    payload, prepared = inspection_report(
                        identity["step"], identity["output_root"], images=identity["images"],
                        max_images=identity["max_images"], text_budget=1_000_000,
                        meta=self.request["meta"], _checkpoint=self._checkpoint,
                    )
                    result = {"schema_version": 1, "job_id": self.job_id,
                              "payload": payload, "prepared": {**prepared, "report": str(prepared["report"]), "bundle": str(prepared["bundle"])}}
                    with self._state() as state:
                        if state["cancel_requested"]:
                            raise _Canceled()
                        result_path = self.directory / ("result_" + _digest(result)[:16] + ".json")
                        cache.publish_json(result_path, {**result, "checksum": _digest(result)})
                        state["result"] = _artifact(result_path)
                        state["status"] = "failed" if prepared["omitted"] and any(row["reason"] == "image_failed" for row in prepared["omitted"]) else "complete"
                        if state["status"] == "failed":
                            state["message"] = "Some inspection images failed; the report remains available. Resume this job to retry unfinished images."
                        else:
                            cache.mark_completed(state)
                    if state["status"] == "complete" and cache.managed:
                        cache.enforce()
            except _Canceled:
                with self._state() as state:
                    state["status"] = "canceled"
            except Exception as exc:
                with self._state() as state:
                    state.update(status="resource_limit" if isinstance(exc, CacheResourceLimit) else "failed", message=f"{type(exc).__name__}: {exc}")
                    for stage in state["stages"].values():
                        if stage["status"] == "running":
                            stage.update(status="failed", message=str(exc))

    def start(self, *, background=False, resume=False):
        self._validate(software=True)
        self.status()  # Reconcile released execution ownership/startup expiry.
        with self._state() as state:
            if self._owned() or state["status"] in {"complete", "queued"}:
                return
            if state["status"] in {"canceled", "failed", "resource_limit"} and not resume:
                return
            token = uuid.uuid4().hex
            state.update(status="queued", run_token=token, cancel_requested=False,
                         launch_deadline=time.time() + STARTUP_SECONDS, message=None)
            if background:
                try:
                    env = {**os.environ, "AGENTCAD_DAEMON": "1"}
                    # Editable checkouts and installed packages both work. Do
                    # not depend on the caller's relative PYTHONPATH/cwd.
                    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
                    with (self.directory / "worker.log").open("ab") as log:
                        subprocess.Popen([sys.executable, "-m", "agentcad.inspection_jobs", str(self.directory), self.job_id, token],
                            cwd=self.request["identity"]["project_root"], env=env, stdin=subprocess.DEVNULL,
                            stdout=log, stderr=log, close_fds=True,
                            **({"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}))
                except Exception as exc:
                    state.update(status="failed", message=f"Could not start inspection worker: {exc}")
                return
        self._work(token)

    def cancel(self):
        with self._state() as state:
            if state["status"] == "complete":
                return
            state["cancel_requested"] = True
            if not self._owned():
                state.update(status="canceled", run_token=None)
            else:
                state["message"] = "Cancellation requested; the current stage may finish before the worker stops."

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            snapshot = self.status()
            if snapshot["status"] not in {"queued", "running"}:
                return snapshot, False
            if deadline is not None and time.monotonic() >= deadline:
                return snapshot, True
            time.sleep(0.05)

    def result(self, *, text_budget=DEFAULT_TEXT_BUDGET, response_path=str, response_extra=None):
        identity = self.request["identity"]
        with self._state() as state:
            paths = [Path(a["path"]) for stage in state["stages"].values() for a in stage.get("artifacts", [])]
            if state.get("result"):
                paths.append(Path(state["result"]["path"]))
        layout = ProjectLayout(Path(identity["project_root"]), Path(identity["build_root"]))
        with cache_lease(identity["output_root"], layout=layout, protect=paths) as cache:
            result = self._result(text_budget=text_budget, response_path=response_path, response_extra=response_extra)
            if result is not None:
                cache.validate(paths)
                result[0]["retention"] = cache.reference()
                return compact_report(result[0]), result[1]
            return None

    def _result(self, *, text_budget=DEFAULT_TEXT_BUDGET, response_path=str, response_extra=None):
        self._validate()
        with self._state() as state:
            if "result" not in state:
                return None
            _verify([state["result"]])
            for stage in state["stages"].values():
                if stage["status"] == "complete":
                    _verify(stage["artifacts"])
            result = _read(Path(state["result"]["path"]), self.job_id)
        payload, prepared = result["payload"], result["prepared"]
        for key in ("manifest", "report", "bundle", "image_root"):
            payload["inventory"][key] = response_path(Path(payload["inventory"][key]))
        command = "agentcad inspect " + shlex.quote(response_path(Path(self.request["identity"]["step"])))
        identity = self.request["identity"]
        if identity["build_root"] != identity["project_root"]:
            command += " --build-dir " + shlex.quote(identity["build_root"])
        payload["next_actions"] = [command + " --id ID --image", command + " --report --no-images"]
        payload["budget"]["text_tokens"] = text_budget
        payload.update(response_extra or {})
        prepared["report"], prepared["bundle"] = Path(prepared["report"]), Path(prepared["bundle"])
        # Import consumers expect prepared media paths in the same path basis.
        for row in prepared["media"]:
            row["png"] = response_path(Path(row["png"]))
        return compact_report(payload), prepared


def request_report(step, output_root, layout, *, images=True, max_images=9, text_budget=2000,
                   meta=None, background=False, wait_timeout=None, job_id=None, action=None,
                   response_extra=None, on_prepared=None):
    if wait_timeout is not None and (not math.isfinite(wait_timeout) or wait_timeout < 0):
        raise JobError("Inspection wait timeout must be a finite nonnegative number of seconds.")
    job = (InspectionJob.open(step, output_root, layout, job_id) if job_id else
           InspectionJob.prepare(step, output_root, layout, images=images, max_images=max_images, meta=meta))
    if on_prepared is not None:
        on_prepared(job.status())
    if action == "cancel":
        job.cancel()
    elif action not in {"status", "wait"}:
        job.start(background=background or wait_timeout is not None, resume=action == "resume")
    expired = False
    if not background and action not in {"status", "cancel"}:
        snapshot, expired = job.wait(wait_timeout)
    else:
        snapshot = job.status()
    result = None
    if snapshot["status"] in {"complete", "failed"} and action not in {"status", "cancel"}:
        result = job.result(text_budget=text_budget, response_path=layout.response_path, response_extra=response_extra)
    return snapshot, result, expired


if __name__ == "__main__":
    InspectionJob(sys.argv[1], sys.argv[2])._work(sys.argv[3])
