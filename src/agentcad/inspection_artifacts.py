"""Read checked inspection artifacts through bounded, transport-neutral chunks."""

from __future__ import annotations

import base64
from contextlib import nullcontext
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex

from agentcad.geometry_manifest import MANIFEST_NAME, SCHEMA_VERSION, _digest
from agentcad.inspection_cache import cache_lease, _IMAGE
from agentcad.project import get_project

DEFAULT_CHUNK_BYTES = 3072
MAX_CHUNK_BYTES = 49152
MAX_JSON_BYTES = 32 * 1024 * 1024
_DERIVED = re.compile(r"(?:report_[0-9a-f]{16}|bundle_[0-9a-f]{16})\.json|" + _IMAGE + r"\.(?:json|png)")


class ArtifactUnavailable(ValueError):
    reason = "inspection_artifact_unavailable"


def _regular(path, boundary):
    """Check the lexical path before resolving, including intermediate links."""
    path, boundary = Path(os.path.abspath(path)), Path(os.path.abspath(boundary))
    if not path.is_relative_to(boundary):
        raise ArtifactUnavailable("Use a returned artifact from this baseline and build root.")
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ArtifactUnavailable("Inspection artifact reads cannot follow symlinks.")
        if candidate == boundary:
            break
    if not path.is_file():
        raise ArtifactUnavailable("Artifact is missing, expired or evicted. Request fresh evidence; old bytes are not regenerated.")
    return path


def _json(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ArtifactUnavailable("Inspection JSON exceeds the 32 MiB retrieval validation limit.")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ArtifactUnavailable("Inspection artifact is not a JSON object.")
    return value, hashlib.sha256(raw).hexdigest()


def _read_chunk(path, offset, length):
    """Hash and take the chunk from the same open file, with bounded memory."""
    digest, chunk, size = hashlib.sha256(), b"", 0
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            start, end = max(offset - size, 0), min(offset + length - size, len(block))
            if start < end:
                chunk += block[start:end]
            size += len(block)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ArtifactUnavailable("Artifact changed during the read; restore its original bytes before retrying.")
    if not size or offset >= size:
        raise ArtifactUnavailable("artifact_offset must be smaller than the nonempty artifact's byte count.")
    return digest.hexdigest(), size, chunk


def _manifest(step):
    path = _regular(step.with_name(MANIFEST_NAME), step.parent)
    saved, file_sha256 = _json(path)
    checksum = saved.pop("manifest_id")
    if saved["schema_version"] != SCHEMA_VERSION or _digest(saved) != checksum or saved["baseline"]["file"] != step.name:
        raise ArtifactUnavailable("Geometry manifest integrity or baseline binding failed; restore the original manifest.")
    saved["manifest_id"] = checksum
    digest, _, _ = _read_chunk(step, 0, 0)
    if digest != saved["baseline"]["sha256"]:
        raise ArtifactUnavailable("Baseline changed; restore the original geometry before retrieving its artifacts.")
    return saved, file_sha256


def _binding(data, saved):
    if (not isinstance(data, dict) or data.get("manifest_id") != saved["manifest_id"]
            or data.get("baseline_sha256") != saved["baseline"]["sha256"]):
        raise ArtifactUnavailable("Artifact belongs to another geometry manifest or baseline.")


def _report(path, saved):
    data, file_sha256 = _json(path)
    checksum = data.pop("sha256")
    _binding(data, saved)
    if _digest(data) != checksum or path.name != f"report_{data['analysis_revision'][:16]}.json":
        raise ArtifactUnavailable("Inspection report integrity check failed.")
    return data, file_sha256


def _check_derived(path, saved, cache):
    if path.name.startswith("report_"):
        return _report(path, saved)[1]
    elif path.name.startswith("bundle_"):
        data, file_sha256 = _json(path)
        if path.name != f"bundle_{_digest(data)[:16]}.json":
            raise ArtifactUnavailable("Inspection bundle integrity check failed.")
        if "manifest_id" in data:
            _binding(data, saved)
        else:
            # Older catalogues did not carry their own baseline binding.
            report = _regular(path.with_name(f"report_{data['analysis_revision'][:16]}.json"), path.parent)
            cache.validate([report])
            if _report(report, saved)[0]["analysis_revision"] != data["analysis_revision"]:
                raise ArtifactUnavailable("Inspection bundle analysis binding failed.")
        return file_sha256
    else:
        metadata = _regular(path.with_suffix(".json"), path.parent)
        cache.validate([metadata])
        data, file_sha256 = _json(metadata)
        checksum = data.pop("metadata_sha256")
        _binding(data["request"], saved)
        if (_digest(data) != checksum or not path.stem.endswith("_" + _digest(data["request"])[:16])):
            raise ArtifactUnavailable("Inspection image evidence integrity check failed.")
        if path.suffix == ".png":
            return data["png_sha256"]
        return file_sha256


def read_artifact(file, artifact, output_root, *, offset=0, length=DEFAULT_CHUNK_BYTES, expected_sha256=None,
                  expected_expires_at=None):
    """Copy saved evidence only. No native geometry imports or regeneration."""
    if type(offset) is not int or offset < 0 or type(length) is not int or not 1 <= length <= MAX_CHUNK_BYTES:
        raise ArtifactUnavailable(f"Use nonnegative artifact_offset and artifact_bytes from 1 to {MAX_CHUNK_BYTES}.")
    if expected_sha256 is not None and (not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)):
        raise ArtifactUnavailable("artifact_sha256 must be the returned 64-character SHA-256.")
    if offset and expected_sha256 is None:
        raise ArtifactUnavailable("Continuation requires artifact_sha256 from the first chunk; never combine unpinned reads.")
    if expected_expires_at is not None:
        try:
            deadline = datetime.fromisoformat(expected_expires_at.replace("Z", "+00:00"))
            if deadline.tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ArtifactUnavailable("artifact_expires_at must be the expiry returned by the first chunk.") from None
    if (not isinstance(artifact, str) or not artifact or "\x00" in artifact
            or (os.name != "nt" and "\\" in artifact) or ".." in Path(artifact).parts):
        raise ArtifactUnavailable("Use manifest, baseline, or an exact returned inspection artifact reference.")
    if artifact.startswith("search_"):
        from agentcad.geometry_search import read_search_artifact
        return read_search_artifact(file, artifact, offset=offset, length=length,
                                    expected_sha256=expected_sha256, expected_expires_at=expected_expires_at)
    source = Path(os.path.abspath(file))
    # A caller can explicitly name a baseline, but cannot use a link to expose
    # some other file as that baseline or one of its companion artifacts.
    _regular(source, source.parent)
    step = source.resolve()
    saved, manifest_sha256 = _manifest(step)
    root = Path(output_root).absolute() / "inspection"
    if artifact in {"manifest", MANIFEST_NAME}:
        path = step.with_name(MANIFEST_NAME)
    elif artifact == "baseline":
        path = step
    else:
        selected = Path(artifact)
        path = root / selected if selected.name == artifact else Path(os.path.abspath(selected))
    path = Path(os.path.abspath(path))
    durable = path in {step, step.with_name(MANIFEST_NAME)}
    # A standalone input can already have a source-side cache. Explicitly
    # pinning the caller's build root on continuation must not redirect its
    # saved reference to a newly selected derived cache.
    allowed_roots = {root, step.parent / "inspection"}
    if not durable and (path.parent not in allowed_roots or not _DERIVED.fullmatch(path.name)):
        raise ArtifactUnavailable("Only this baseline, geometry_manifest.json, and returned inspection reports/bundles/images are retrievable.")
    if durable:
        _regular(path, step.parent)
    else:
        output_root = path.parent.parent
        _regular(path, Path(output_root).absolute())
    with (nullcontext(None) if durable else cache_lease(output_root, protect=[path])) as cache:
        required_digest = saved["baseline"]["sha256"] if path == step else manifest_sha256
        if cache is not None:
            cache.validate([path], {"expires_at": expected_expires_at} if expected_expires_at else None)
            required_digest = _check_derived(path, saved, cache)
        retention = {"state": "durable"} if durable else cache.reference()
        if expected_expires_at and retention.get("expires_at") != expected_expires_at:
            raise ArtifactUnavailable("Artifact retention changed since the first chunk. Start a new read; old evidence is not substituted.")
        if offset and retention.get("expires_at") and expected_expires_at is None:
            raise ArtifactUnavailable("Continuation of retained evidence requires artifact_expires_at; copy next.cli or next.mcp from the first chunk.")
        digest, total, chunk = _read_chunk(path, offset, length)
        if required_digest is not None and digest != required_digest:
            raise ArtifactUnavailable("Artifact bytes do not match their saved evidence hash.")
        if expected_sha256 is not None and digest != expected_sha256:
            raise ArtifactUnavailable("Artifact changed since the first chunk; restore the original bytes. No replacement was returned.")
        complete = offset + len(chunk) == total
        layout = get_project()
        args = {"file": str(step), "cwd": str(layout.project_root), "build_dir": str(layout.build_root),
                "artifact": str(path), "artifact_sha256": digest,
                "artifact_offset": offset + len(chunk), "artifact_bytes": length}
        command = ["agentcad", "inspect", str(step), "--artifact", str(path),
                   "--artifact-sha256", digest, "--artifact-offset", str(args["artifact_offset"]),
                   "--artifact-bytes", str(length), "--build-dir", str(layout.build_root)]
        if retention.get("expires_at"):
            args["artifact_expires_at"] = retention["expires_at"]
            command.extend(["--artifact-expires-at", retention["expires_at"]])
        return {"command": "inspect", "status": "success", "manifest_id": saved["manifest_id"],
                "version": saved["version"], "baseline_sha256": saved["baseline"]["sha256"],
                "artifact": {"reference": str(path), "sha256": digest, "total_bytes": total,
                             "mime_type": "model/step" if path == step else "image/png" if path.suffix == ".png" else "application/json",
                             "encoding": "base64", "offset": offset, "returned_bytes": len(chunk),
                             "data": base64.b64encode(chunk).decode("ascii"), "complete": complete,
                             "retention": retention},
                "next": None if complete else {"cli": shlex.join(command), "mcp": {"tool": "inspect", "arguments": args}}}
