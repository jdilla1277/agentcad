"""Retain immutable source, core geometry, and diagnostics for each attempt.

This store does not replace version directories or perform promotion. An
attempt request is saved before execution; core records are sealed once their
inputs are known, and the final command response is a separate immutable record.
An interrupted attempt stays addressable without pretending it completed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

from agentcad import change_contract as contract
from agentcad.versioning import _version_lock, atomic_write_json

_STATE = Path(".agentcad/changes")
_REVISION = re.compile(r"r_[0-9a-f]{32}\Z")


class ChangeStoreError(ValueError):
    """Retention failed or a retained record/input is no longer trustworthy."""


def _safe_json(value):
    # Diagnostics retain unusual native values as text rather than emitting
    # nonstandard NaN/Infinity JSON. Model/check identities use finite JSON.
    if isinstance(value, dict):
        return {str(k): _safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or type(value) in (str, bool, int, float):
        return value
    return str(value)


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def _sealed(kind: str, **fields) -> dict:
    result = _safe_json({"schema_version": 1, "kind": kind, **fields})
    return {**result, "content_sha256": _digest(result)}


def _write_bytes_once(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data:
                raise ChangeStoreError(f"Retained artifact cannot be overwritten: {path}")
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_once(path: Path, record: dict) -> None:
    try:
        atomic_write_json(path, record, overwrite=False)
    except FileExistsError:
        if path.is_symlink() or json.loads(path.read_text()) != record:
            raise ChangeStoreError(f"Retained record cannot be overwritten: {path}")


class ChangeStore:
    def __init__(self, build_root: str | Path):
        self.root = Path(build_root).resolve()

    def _path(self, relative: str | Path) -> Path:
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root):
            raise ChangeStoreError("Change history path escapes the selected build root.")
        for item in (path, *path.parents):
            if item == self.root:
                break
            if item.is_symlink():
                raise ChangeStoreError(f"Change history must not follow symlinks: {item}")
        return path

    def _state(self) -> dict:
        path = self._path(_STATE / "working.json")
        if not path.exists():
            return {"schema_version": 1, "revision_id": None, "selection_generation": 0}
        try:
            state = json.loads(path.read_text())
            if not isinstance(state, dict):
                raise ValueError("working selection must be an object")
            if (
                state["schema_version"] != 1
                or type(state["selection_generation"]) is not int
                or state["selection_generation"] < 0
                or (
                    state["revision_id"] is not None
                    and not _REVISION.fullmatch(state["revision_id"])
                )
            ):
                raise ValueError("invalid working selection")
            return state
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ChangeStoreError(f"Cannot read change working selection: {exc}") from exc

    @property
    def working_revision_id(self) -> str | None:
        return self._state()["revision_id"]

    def begin(
        self,
        *,
        command: str,
        label: str,
        source: bytes,
        source_name: str,
        model: dict,
        legacy_baseline=None,
        promotion_mode: str = "automatic",
        requirements: dict | None = None,
        defer_checks: bool = False,
    ) -> Attempt:
        if not (self.root / "agentcad.json").is_file():
            raise ChangeStoreError(
                "Initialize the selected build history before retaining changes."
            )
        if command not in ("run", "import") or not isinstance(source, bytes):
            raise ChangeStoreError("An attempt needs a run/import command and exact source bytes.")
        if promotion_mode not in ("automatic", "staged") or not isinstance(model, dict):
            raise ChangeStoreError(
                "An attempt needs an explicit promotion mode and model settings."
            )
        # Validate containment before the shared lock can create .agentcad.
        self._path(_STATE)
        revision_id = "r_" + uuid.uuid4().hex
        attempt = Attempt(self, revision_id)
        with _version_lock(self.root):
            state = self._state()
            from agentcad.change_checks import compile_policy

            previous = None
            if state["revision_id"]:
                parent = Attempt(self, state["revision_id"])._read(
                    "request.json", "attempt_request"
                )
                previous = parent.get("check_policy")
            policy = compile_policy(
                requirements,
                previous=previous,
                core=True,
                profile=model.get("validation_profile", "deliverable"),
            )
            if defer_checks and policy is None:
                raise ChangeStoreError(
                    "--defer-checks requires declared or inherited requirements."
                )
            baseline_inputs, baseline_errors = [], {}
            for spec in (policy or {}).get("checks", []):
                if spec["settings"].get("kind") != "preserve_cylinders":
                    continue
                check_id = spec["check_id"]
                try:
                    baseline_id = spec["settings"]["baseline_revision_id"]
                    baseline = self.read(baseline_id)
                    baseline_artifact = next(
                        a for a in baseline["candidate"]["inputs"] if a["role"] == "geometry"
                    )
                    data = Attempt(self, baseline_id)._verify(baseline_artifact).read_bytes()
                    if hashlib.sha256(data).hexdigest() != baseline_artifact["sha256"]:
                        raise ChangeStoreError("Preservation baseline changed during capture.")
                    path = self._path(
                        attempt.directory.relative_to(self.root) / f"baselines/{check_id}.step"
                    )
                    _write_bytes_once(path, data)
                    baseline_inputs.append(attempt._artifact(path, "input", "baseline:" + check_id))
                except (OSError, ValueError, TypeError, KeyError, StopIteration) as exc:
                    baseline_errors[check_id] = str(exc)
            source_path = attempt.directory / ("source" + Path(source_name).suffix.lower())
            _write_bytes_once(source_path, source)
            request = _sealed(
                "attempt_request",
                revision_id=revision_id,
                parent_revision_id=state["revision_id"],
                selection_generation=state["selection_generation"] + 1,
                started_ns=time.time_ns(),
                command=command,
                label=label,
                model=model,
                legacy_baseline=legacy_baseline,
                promotion_mode=promotion_mode,
                source=attempt._artifact(source_path, "source", source_name),
                check_policy=policy,
                check_inputs=baseline_inputs,
                baseline_errors=baseline_errors,
                defer_checks=bool(defer_checks),
            )
            _write_json_once(attempt.directory / "request.json", request)
            atomic_write_json(
                self._path(_STATE / "working.json"),
                {
                    "schema_version": 1,
                    "revision_id": revision_id,
                    "selection_generation": request["selection_generation"],
                },
            )
        return attempt

    def read(self, revision_id: str) -> dict:
        try:
            attempt = Attempt(self, revision_id)
            request = attempt._read("request.json", "attempt_request")
            if request["revision_id"] != revision_id:
                raise ChangeStoreError("Retained request identifies a different attempt.")
            attempt._verify(request["source"])
            for artifact in request.get("check_inputs", []):
                attempt._verify(artifact)
            if request.get("check_policy"):
                contract.validate_document(request["check_policy"], kind="target_policy")
            core_path = attempt.directory / "core.json"
            core = attempt._read("core.json", "attempt_core") if core_path.exists() else None
            candidate = receipt = policy = None
            if core is not None:
                candidate = attempt._read("candidate.json", "candidate", shared=True)
                receipt = attempt._read("receipt.json", "change_receipt", shared=True)
                policy = attempt._read("policy.json", "target_policy", shared=True)
                for name, record in (
                    ("candidate", candidate),
                    ("receipt", receipt),
                    ("policy", policy),
                ):
                    if core[name + "_sha256"] != record["content_sha256"]:
                        raise ChangeStoreError(
                            "Core record references different retained evidence."
                        )
                if (
                    candidate["revision_id"] != revision_id
                    or candidate["parent_revision_id"] != request["parent_revision_id"]
                    or candidate["model"] != request["model"]
                    or candidate["promotion_mode"] != request["promotion_mode"]
                    or request["source"] not in candidate["inputs"]
                    or any(a not in candidate["inputs"] for a in request.get("check_inputs", []))
                ):
                    raise ChangeStoreError("Candidate does not match its retained attempt.")
                if request.get("check_policy") and policy != request["check_policy"]:
                    raise ChangeStoreError(
                        "Core policy does not match the original request policy."
                    )
                if (
                    receipt["candidate"]["content_sha256"] != candidate["content_sha256"]
                    or receipt["candidate"]["model_fingerprint"] != candidate["model_fingerprint"]
                    or receipt["target_id"] != policy["target_id"]
                    or receipt["policy"]["sha256"] != policy["content_sha256"]
                    or candidate["target_policies"].get(policy["target_id"]) != receipt["policy"]
                ):
                    raise ChangeStoreError(
                        "Retained receipt, policy, and candidate do not share the same scope."
                    )
                for item in candidate["inputs"]:
                    attempt._verify(item)
            outcome_path = attempt.directory / "outcome.json"
            outcome = (
                attempt._read("outcome.json", "attempt_outcome") if outcome_path.exists() else None
            )
            if outcome is not None and (
                core is None or outcome["core_sha256"] != core["content_sha256"]
            ):
                raise ChangeStoreError("Outcome is not bound to this attempt's core snapshot.")
            if outcome is not None:
                for item in outcome["artifacts"]:
                    attempt._verify(item)
            return {
                "status": "completed" if outcome else "in_progress",
                "request": request,
                "candidate": candidate,
                "receipt": receipt,
                "policy": policy,
                "outcome": outcome,
            }
        except (OSError, ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, ChangeStoreError):
                raise
            raise ChangeStoreError(f"Cannot read retained change {revision_id}: {exc}") from exc

    def list_attempts(self) -> list[dict]:
        directory = self._path(_STATE / "attempts")
        if not directory.exists():
            return []
        requests = [
            self.read(p.name)["request"]
            for p in directory.iterdir()
            if _REVISION.fullmatch(p.name) and (p / "request.json").exists()
        ]
        return sorted(requests, key=lambda r: r["selection_generation"])

    def read_checks(self, revision_id: str) -> dict:
        saved = self.read(revision_id)
        attempt = Attempt(self, revision_id)
        terminal = attempt.directory / "checks_complete.json"
        receipt = saved["receipt"]
        lifecycle = (
            "pending"
            if receipt is None or any(r["state"] == "pending" for r in receipt["checks"])
            else "completed"
        )
        path = attempt.directory / "receipt.json" if receipt else None
        if terminal.exists():
            receipt = attempt._read("checks_complete.json", "change_receipt", shared=True)
            original_core = [
                r
                for r in saved["receipt"]["checks"]
                if r["check_id"] in ("execution", "written_geometry")
            ]
            if [
                r for r in receipt["checks"] if r["check_id"] in ("execution", "written_geometry")
            ] != original_core:
                raise ChangeStoreError("Completed checks changed original core evidence.")
            lifecycle, path = "completed", terminal
        elif (attempt.directory / "checks_running.json").exists():
            running = attempt._read("checks_running.json", "checks_running")
            if running["candidate_sha256"] != saved["candidate"]["content_sha256"]:
                raise ChangeStoreError("Running checks identify another candidate.")
            lifecycle = "running"
        if (
            receipt is not None
            and contract.make_change_receipt(saved["candidate"], saved["policy"], receipt["checks"])
            != receipt
        ):
            raise ChangeStoreError("Check receipt does not match its pinned candidate and policy.")
        return {
            "revision_id": revision_id,
            "lifecycle": lifecycle,
            "receipt": receipt,
            "receipt_path": str(path) if path else None,
        }

    def complete_checks(self, revision_id: str) -> dict:
        # Validate before creating even a lock directory for an unknown ID.
        observed = self.read_checks(revision_id)
        if observed["lifecycle"] == "completed":
            return observed
        attempt = Attempt(self, revision_id)
        # Per-attempt lock: independent revisions can evaluate concurrently.
        with _version_lock(attempt.directory):
            current = self.read_checks(revision_id)
            if current["lifecycle"] == "completed":
                return current
            saved = self.read(revision_id)
            if saved["candidate"] is None:
                raise ChangeStoreError(
                    "The build has not published its core snapshot; wait before completing checks."
                )
            _write_json_once(
                attempt.directory / "checks_running.json",
                _sealed("checks_running", candidate_sha256=saved["candidate"]["content_sha256"]),
            )
            response = attempt._read("core.json", "attempt_core")["response"]
            results = [
                r
                for r in saved["receipt"]["checks"]
                if r["check_id"] in ("execution", "written_geometry")
            ]
            results.extend(attempt._evaluate(saved["candidate"], saved["policy"], response))
            receipt = contract.make_change_receipt(saved["candidate"], saved["policy"], results)
            _write_json_once(attempt.directory / "checks_complete.json", receipt)
            return self.read_checks(revision_id)


class Attempt:
    def __init__(self, store: ChangeStore, revision_id: str):
        if not isinstance(revision_id, str) or not _REVISION.fullmatch(revision_id):
            raise ChangeStoreError("Use a returned retained-change revision ID.")
        self.store = store
        self.revision_id = revision_id
        self.directory = store._path(_STATE / "attempts" / revision_id)

    def _read(self, name: str, kind: str, *, shared: bool = False) -> dict:
        path = self.store._path(self.directory.relative_to(self.store.root) / name)
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                raise ValueError("record must be an object")
            if shared:
                return contract.validate_document(data, kind=kind)
            body = {k: v for k, v in data.items() if k != "content_sha256"}
            if (
                data.get("kind") != kind
                or data.get("schema_version") != 1
                or data.get("content_sha256") != _digest(body)
            ):
                raise ValueError("record integrity check failed")
            return data
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ChangeStoreError(f"Cannot read retained {name}: {exc}") from exc

    def _artifact(self, path: Path, role: str, name: str) -> dict:
        data = path.read_bytes()
        return {
            "name": name,
            "role": role,
            "path": path.relative_to(self.store.root).as_posix(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }

    def _verify(self, artifact: dict) -> Path:
        path = self.store._path(artifact["path"])
        if not path.is_relative_to(self.directory):
            raise ChangeStoreError("Retained input points outside its attempt snapshot.")
        observed = self._artifact(path, artifact["role"], artifact["name"])
        if observed != artifact:
            raise ChangeStoreError(f"Retained artifact bytes no longer match: {path}")
        return path

    @property
    def source_path(self) -> Path:
        return self._verify(self._read("request.json", "attempt_request")["source"])

    def capture_geometry(self, path: str | Path) -> None:
        """Keep emitted bytes even when subsequent parsing/validation fails."""
        target = self.store._path(
            self.directory.relative_to(self.store.root) / "geometry/output.step"
        )
        _write_bytes_once(target, Path(path).read_bytes())

    def prepare(self, response: dict, *, geometry: Path | None = None) -> dict:
        """Seal core evidence before the existing version/current commit."""
        request = self._read("request.json", "attempt_request")
        if (self.directory / "core.json").exists():
            saved = self.store.read(self.revision_id)
            if geometry is not None:
                expected = saved["candidate"]["deliverables"]["cad"]
                if (
                    not expected
                    or hashlib.sha256(Path(geometry).read_bytes()).hexdigest()
                    != expected[0]["sha256"]
                ):
                    raise ChangeStoreError("A prepared geometry snapshot cannot be replaced.")
            return self.reference()
        inputs = [request["source"], *request.get("check_inputs", [])]
        for artifact in inputs:
            self._verify(artifact)
        target = self.store._path(
            self.directory.relative_to(self.store.root) / "geometry/output.step"
        )
        if geometry is not None:
            self.capture_geometry(geometry)
        if target.exists():
            inputs.append(self._artifact(target, "geometry", "output.step"))
        settings = {"validation_profile": request["model"].get("validation_profile", "deliverable")}
        policy = request.get("check_policy") or contract.make_policy(
            target_id="cad",
            policy_id="legacy-core-retention-" + settings["validation_profile"],
            version=1,
            checks=[
                {
                    "check_id": name,
                    "definition_version": 1,
                    "settings": settings,
                    "require_completion": True,
                    "blocks_on_failure": True,
                }
                for name in ("execution", "written_geometry")
            ],
        )
        candidate = contract.make_candidate(
            revision_id=self.revision_id,
            parent_revision_id=request["parent_revision_id"],
            inputs=inputs,
            model=request["model"],
            policies=[policy],
            requested_change=request["label"],
            promotion_mode=request["promotion_mode"],
            metadata={"command": request["command"], "source_name": inputs[0]["name"]},
        )
        status = response.get("status")
        execution = "pass" if status in ("success", "invalid_geometry") else "fail"
        verdict = (response.get("validation") or {}).get(
            "is_valid", (response.get("metrics") or {}).get("is_valid")
        )
        state = (
            "fail"
            if verdict is False
            else "unavailable" if not target.exists() or verdict is None else "pass"
        )
        evidence = [
            {"artifact": (self.directory / "core.json").relative_to(self.store.root).as_posix()}
        ]
        checks = [
            contract.make_check_result(
                candidate, policy, "execution", execution, observed=status, evidence=evidence
            ),
            contract.make_check_result(
                candidate, policy, "written_geometry", state, observed=verdict, evidence=evidence
            ),
        ]
        if request.get("check_policy"):
            from agentcad.change_checks import CORE_IDS

            if request.get("defer_checks") and execution == "pass" and state == "pass":
                checks.extend(
                    contract.make_check_result(
                        candidate,
                        policy,
                        spec["check_id"],
                        "pending",
                        observed={
                            "reason": "Deferred; complete this revision with check-spec --complete."
                        },
                        actions=[{"action": "complete_checks", "revision_id": self.revision_id}],
                    )
                    for spec in policy["checks"]
                    if spec["check_id"] not in CORE_IDS
                )
            else:
                checks.extend(self._evaluate(candidate, policy, response))
        receipt = contract.make_change_receipt(candidate, policy, checks)
        for name, record in (("policy", policy), ("candidate", candidate), ("receipt", receipt)):
            _write_json_once(
                self.store._path(self.directory.relative_to(self.store.root) / (name + ".json")),
                record,
            )
        _write_json_once(
            self.store._path(self.directory.relative_to(self.store.root) / "core.json"),
            _sealed(
                "attempt_core",
                candidate_sha256=candidate["content_sha256"],
                receipt_sha256=receipt["content_sha256"],
                policy_sha256=policy["content_sha256"],
                response=response,
            ),
        )
        return self.reference()

    def _evaluate(self, candidate, policy, response):
        from agentcad.change_checks import evaluate_checks, measure_geometry

        request = self._read("request.json", "attempt_request")
        measurement, baselines, reason = None, {}, "Written geometry is not valid or available."
        verdict = (response.get("validation") or {}).get(
            "is_valid", (response.get("metrics") or {}).get("is_valid")
        )
        if verdict is True:
            try:
                artifact = next(a for a in candidate["inputs"] if a["role"] == "geometry")
                measurement = measure_geometry(self._verify(artifact))
                reason = None
            except Exception as exc:
                reason = f"Geometry measurement unavailable: {exc}"
        for artifact in request.get("check_inputs", []):
            try:
                baselines[artifact["name"].removeprefix("baseline:")] = measure_geometry(
                    self._verify(artifact)
                )
            except Exception:
                pass  # The individual preservation check reports unavailable.
        return evaluate_checks(candidate, policy, measurement, baselines=baselines, reason=reason)

    def finish(self, response: dict) -> dict:
        if (self.directory / "outcome.json").exists():
            saved = self.store.read(self.revision_id)
            if saved["outcome"]["response"] != _safe_json(response):
                raise ChangeStoreError(
                    "A completed outcome cannot be replaced with another response."
                )
            return self.reference()
        self.prepare(response)
        core = self._read("core.json", "attempt_core")
        artifacts, unavailable = self._capture_artifacts(response)
        _write_json_once(
            self.store._path(self.directory.relative_to(self.store.root) / "outcome.json"),
            _sealed(
                "attempt_outcome",
                core_sha256=core["content_sha256"],
                response=response,
                artifacts=artifacts,
                unavailable_artifacts=unavailable,
            ),
        )
        return self.reference()

    def _capture_artifacts(self, response: dict) -> tuple[list, list]:
        """Copy declared derived outputs, not arbitrary files or directories."""
        declared = {}

        def paths(value, name):
            if isinstance(value, str):
                declared[name] = value
            elif isinstance(value, dict):
                for key, child in value.items():
                    paths(child, f"{name}.{key}")
            elif isinstance(value, list):
                for i, child in enumerate(value):
                    paths(child, f"{name}.{i}")

        outputs = response.get("outputs") or {}
        if isinstance(outputs, dict):
            for name, value in outputs.items():
                if name not in ("step", "source", "script"):
                    paths(value, "outputs." + name)
        for name in ("preview", "viewer", "viewer_glb", "renders"):
            paths(response.get(name), name)
        for group in ("parts", "groups"):
            for i, item in enumerate(response.get(group) or []):
                if isinstance(item, dict):
                    paths(item.get("preview"), f"{group}.{i}.preview")
        comparison = response.get("diff") or {}
        if isinstance(comparison, dict):
            for name in ("side_by_side", "overlay", "volume_glb", "volume_png"):
                paths(comparison.get(name), "diff." + name)
        artifacts, unavailable = [], []
        sheet = response.get("inspection_image") or {}
        if isinstance(sheet, dict):
            paths(sheet.get("metadata"), "inspection_image.metadata")
        # The saved catalogue includes all prepared inspection images, even
        # when the inline report is disabled or trimmed by its text budget.
        bundle = outputs.get("inspection_bundle") if isinstance(outputs, dict) else None
        if isinstance(bundle, str):
            try:
                catalogue = json.loads(self.store._path(bundle).read_text())
                for i, row in enumerate(catalogue["media"]):
                    png = row["png"]
                    paths(png, f"inspection.media.{i}.png")
                    paths(str(Path(png).with_suffix(".json")), f"inspection.media.{i}.metadata")
            except (OSError, ValueError, TypeError, KeyError) as exc:
                unavailable.append({"name": "inspection.media", "path": bundle, "reason": str(exc)})
        for name, value in declared.items():
            try:
                source = self.store._path(value)
                if not source.is_file():
                    raise ChangeStoreError("Declared output is unavailable.")
                token = hashlib.sha256(value.encode()).hexdigest()[:20]
                target = self.store._path(
                    self.directory.relative_to(self.store.root)
                    / ("artifacts/" + token + source.suffix)
                )
                _write_bytes_once(target, source.read_bytes())
                artifacts.append(self._artifact(target, "input", name))
            except (OSError, ChangeStoreError) as exc:
                unavailable.append({"name": name, "path": value, "reason": str(exc)})
        return artifacts, unavailable

    def reference(self) -> dict:
        saved = self.store.read(self.revision_id)
        geometry = (saved["candidate"] or {}).get("deliverables", {}).get("cad", [])
        reference = {
            "revision_id": self.revision_id,
            "parent_revision_id": saved["request"]["parent_revision_id"],
            "selection_generation": saved["request"]["selection_generation"],
            "status": saved["status"],
            "request": str(self.directory / "request.json"),
            "source": str(self.source_path),
            "geometry": str(self.store.root / geometry[0]["path"]) if geometry else None,
            "candidate": str(self.directory / "candidate.json") if saved["candidate"] else None,
            "receipt": str(self.directory / "receipt.json") if saved["receipt"] else None,
            "diagnostics": str(self.directory / "outcome.json") if saved["outcome"] else None,
        }
        if saved["request"].get("check_policy") and saved["receipt"]:
            checked = self.store.read_checks(self.revision_id)
            receipt = checked["receipt"]
            reference["validation_checks"] = {
                "lifecycle": checked["lifecycle"],
                "receipt": checked["receipt_path"],
                "validation_state": receipt["validation_state"],
                "promotion_ready": receipt["promotion_ready"],
                "checks": receipt["checks"],
                "blockers": receipt["blockers"],
                "warnings": receipt["warnings"],
            }
        return reference
