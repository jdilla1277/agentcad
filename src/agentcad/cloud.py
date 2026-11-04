"""Bounded HTTPS adapter shared by the cloud CLI and local MCP tools."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

JSON_LIMIT = 8 * 1024 * 1024
SCRIPT_LIMIT = SPEC_LIMIT = 256 * 1024
IMPORT_LIMIT = 20 * 1024 * 1024
STEP_LIMIT = 150 * 1024 * 1024
OPERATIONS = {"create_project", "projects", "project", "run", "import_step", "measure", "inspect",
              "check_spec", "render", "diff", "job", "download"}


class CloudError(Exception):
    def __init__(self, reason, message, **details):
        super().__init__(message)
        self.reason, self.message, self.details = reason, message, details

    def payload(self, operation):
        return {"command": "cloud", "mode": "cloud", "operation": operation, "status": "error",
                "reason": self.reason, "message": self.message, **self.details,
                "more_at": "agentcad docs cloud"}


def _id(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r"_[0-9a-f]{24}", value):
        raise CloudError("invalid_reference", f"Use the returned {prefix} ID, not a local path.")
    return value


def _version(value):
    if type(value) is not int or value < 1:
        raise CloudError("invalid_version", "Version must be a positive integer.")
    return value


def _local_path(value, cwd=None):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CloudError("invalid_file", "Supply an explicit local file path.")
    base = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
    if not base.is_dir():
        raise CloudError("invalid_directory", "cwd must name an existing directory.")
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def _input(value, limit, cwd=None, suffixes=None):
    path = _local_path(value, cwd)
    try:
        if not path.is_file():
            raise CloudError("invalid_file", "The selected file is missing or is not a regular file.")
        if suffixes and path.suffix.lower() not in suffixes:
            raise CloudError("invalid_file", "Use a file ending in " + " or ".join(suffixes) + ".")
        with path.open("rb") as source:
            data = source.read(limit + 1)
    except OSError:
        raise CloudError("invalid_file", "The selected file could not be read.") from None
    if not data or len(data) > limit:
        raise CloudError("invalid_file", f"The file must be nonempty and no larger than {limit} bytes.")
    if len(path.name) > 128 or not path.name.isprintable() or "\\" in unquote(path.name) or "/" in unquote(path.name):
        raise CloudError("invalid_file", "Use a simple filename no longer than 128 characters.")
    return path.name, data


class CloudClient:
    def __init__(self, api_url, key_id, secret, *, transport=None):
        try:
            parsed = urlsplit(api_url)
            loopback = parsed.hostname == "localhost"
            try:
                loopback = loopback or ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                pass
            valid = (isinstance(api_url, str) and api_url.isprintable() and api_url.strip() == api_url
                     and parsed.hostname and parsed.port != 0 and parsed.username is None and parsed.password is None
                     and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
                     and (parsed.scheme == "https" or (parsed.scheme == "http" and loopback)))
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise CloudError("invalid_config", "AGENTCAD_API_URL must be an HTTPS origin without credentials, a path, query, or fragment. HTTP is allowed only for loopback development.")
        if not isinstance(key_id, str) or not re.fullmatch(r"key_[0-9a-f]{24}", key_id) or not isinstance(secret, str) or not 20 <= len(secret) <= 128:
            raise CloudError("invalid_config", "Set the issued AGENTCAD_KEY_ID and AGENTCAD_KEY_SECRET in the process environment.")
        try:
            import httpx
        except ImportError:
            raise CloudError("missing_dependency", "Install the cloud extra: pip install 'agentcad[cloud]' (included with agentcad[mcp]).") from None
        self.httpx, self.secret = httpx, secret
        self.http = httpx.Client(base_url=api_url.rstrip("/"), transport=transport, timeout=30,
                                 follow_redirects=False, headers={"X-AgentCAD-Key-ID": key_id, "X-AgentCAD-Key-Secret": secret})

    @classmethod
    def from_env(cls):
        values = [os.environ.get(name) for name in ("AGENTCAD_API_URL", "AGENTCAD_KEY_ID", "AGENTCAD_KEY_SECRET")]
        if not all(values):
            raise CloudError("missing_config", "Set AGENTCAD_API_URL, AGENTCAD_KEY_ID, and AGENTCAD_KEY_SECRET in the CLI or MCP server process environment. Do not put secrets in project files.")
        return cls(*values)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.http.close()

    def _redact(self, value):
        if isinstance(value, str):
            return value.replace(self.secret, "<redacted>")
        if isinstance(value, dict):
            return {self._redact(key): self._redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value

    def _response_error(self, response, raw):
        if 300 <= response.status_code < 400:
            raise CloudError("redirect_not_allowed", "The API redirected the request. Check AGENTCAD_API_URL; credentials were not forwarded.", http_status=response.status_code)
        if response.is_error:
            try:
                detail = json.loads(raw).get("detail", "API request rejected")
            except (ValueError, AttributeError, RecursionError):
                detail = "API request rejected"
            reason = detail.get("reason", "http_error") if isinstance(detail, dict) else "http_error"
            message = detail.get("message", "API request rejected") if isinstance(detail, dict) else str(detail)
            raise CloudError(self._redact(str(reason))[:80], self._redact(str(message))[:500], http_status=response.status_code)

    def _request(self, method, path, **kwargs):
        try:
            with self.http.stream(method, path, **kwargs) as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > JSON_LIMIT:
                        raise CloudError("response_too_large", "API JSON exceeds the 8 MiB client limit.")
                self._response_error(response, raw)
                try:
                    result = json.loads(raw)
                    if not isinstance(result, dict):
                        raise ValueError()
                    json.dumps(result, allow_nan=False)
                except (ValueError, UnicodeError, RecursionError):
                    raise CloudError("invalid_response", "The API did not return a finite JSON object.") from None
                return self._redact(result)
        except self.httpx.HTTPError:
            reason = "request_outcome_unknown" if method == "POST" else "request_failed"
            raise CloudError(reason, "The API response was not received. Retry the same submission with its idempotency key; poll the same job rather than submitting again.") from None

    def _project_route(self, project):
        return f"/v1/projects/{_id(project, 'prj')}"

    def _version_route(self, project, version):
        return self._project_route(project) + f"/versions/{_version(version)}"

    def _submit(self, path, *, kind, idempotency_key=None, **kwargs):
        retry_key = uuid.uuid4().hex if idempotency_key is None else idempotency_key
        if not isinstance(retry_key, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", retry_key):
            raise CloudError("invalid_idempotency_key", "Use 1 to 128 letters, digits, or . _ : - for the retry key.")
        try:
            result = self._request("POST", path, headers={"Idempotency-Key": retry_key}, **kwargs)
            if (result.get("status") not in {"queued", "running", "succeeded", "failed"}
                    or result.get("kind") != kind):
                raise CloudError("invalid_response", "The API did not return the expected cloud job.")
            try:
                _id(result.get("id"), "job")
                _id(result.get("project_id"), "prj")
                if result["project_id"] != path.split("/")[3]:
                    raise CloudError("invalid_response", "The API returned a job for another project.")
            except CloudError:
                raise CloudError("invalid_response", "The API returned an invalid project or job reference.") from None
        except CloudError as exc:
            exc.details["idempotency_key"] = self._redact(retry_key)
            raise
        result["idempotency_key"] = retry_key
        if result.get("id"):
            result.update(next_actions=[f"agentcad cloud job {result['project_id']} {result['id']} --wait"], more_at="agentcad docs cloud")
        return result

    def create_project(self, name, runtime="build123d"):
        if not isinstance(name, str) or not name.strip() or len(name) > 100 or runtime not in {"build123d", "cadquery"}:
            raise CloudError("invalid_project", "Supply a short project name and runtime build123d or cadquery.")
        try:
            return self._request("POST", "/v1/projects", json={"name": name, "runtime": runtime})
        except CloudError as exc:
            if exc.reason == "request_outcome_unknown":
                exc.message = "Project creation may have succeeded. Check cloud_projects before creating another project."
            raise

    def projects(self):
        return self._request("GET", "/v1/projects")

    def project(self, project):
        return self._request("GET", self._project_route(project))

    def run(self, project, script, label, *, cwd=None, idempotency_key=None):
        route = self._project_route(project)
        if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", label):
            raise CloudError("invalid_label", "Supply a short label starting with a letter and containing letters, digits, _ or -.")
        name, data = _input(script, SCRIPT_LIMIT, cwd, (".py",))
        try:
            data.decode("utf-8")
            if b"\x00" in data:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise CloudError("invalid_script", "The script must be UTF-8 Python source without NUL bytes.") from None
        return self._submit(route + "/runs", kind="run", idempotency_key=idempotency_key,
                            data={"label": label}, files={"file": (name, data, "text/x-python")})

    def import_step(self, project, file, *, cwd=None, idempotency_key=None):
        route = self._project_route(project)
        name, data = _input(file, IMPORT_LIMIT, cwd, (".step", ".stp"))
        return self._submit(route + "/imports", kind="import", idempotency_key=idempotency_key,
                            files={"file": (name, data, "model/step")})

    def measure(self, project, version):
        return self._request("GET", self._version_route(project, version) + "/measure")

    def inspect(self, project, version, *, ids=False, summary=False, limit=100, validate_only=False, idempotency_key=None):
        route = self._version_route(project, version)
        if any(type(value) is not bool for value in (ids, summary, validate_only)) or type(limit) is not int or not 1 <= limit <= 1000 or (validate_only and (ids or summary)):
            raise CloudError("invalid_options", "Use boolean inspection options and limit 1 to 1000; validate_only cannot be combined with ids or summary.")
        return self._submit(route + "/inspect", kind="inspect", idempotency_key=idempotency_key,
                            json={"ids": ids, "summary": summary, "limit": limit, "validate_only": validate_only})

    def check_spec(self, project, version, spec_file, *, cwd=None, idempotency_key=None):
        route = self._version_route(project, version)
        _, data = _input(spec_file, SPEC_LIMIT, cwd)
        try:
            spec = json.loads(data)
            if not isinstance(spec, dict):
                raise ValueError()
            json.dumps(spec, allow_nan=False)
        except (ValueError, UnicodeError, RecursionError):
            raise CloudError("invalid_spec", "The selected spec must be a finite JSON object.") from None
        return self._submit(route + "/check-spec", kind="check_spec", idempotency_key=idempotency_key, json=spec)

    def render(self, project, version, *, idempotency_key=None):
        return self._submit(self._version_route(project, version) + "/render", kind="render", idempotency_key=idempotency_key, json={})

    def diff(self, project, from_version, to_version, *, idempotency_key=None):
        route = self._project_route(project)
        return self._submit(route + "/diff", kind="diff", idempotency_key=idempotency_key,
                            json={"from_version": _version(from_version), "to_version": _version(to_version)})

    def job(self, project, job, *, wait_seconds=0):
        route = self._project_route(project) + f"/jobs/{_id(job, 'job')}"
        if type(wait_seconds) is not int or not 0 <= wait_seconds <= 60:
            raise CloudError("invalid_wait", "wait_seconds must be an integer from 0 to 60. Poll the same job again if it is still running.")
        deadline = time.monotonic() + wait_seconds
        while True:
            timeout = min(30, max(0.1, deadline - time.monotonic())) if wait_seconds else 30
            result = self._request("GET", route, timeout=timeout)
            if result.get("status") in {"succeeded", "failed"} or time.monotonic() >= deadline:
                break
            time.sleep(min(2, max(0, deadline - time.monotonic())))
            if time.monotonic() >= deadline:
                break
        if result.get("status") == "succeeded":
            try:
                if result.get("result_url"):
                    result["result"] = self._request("GET", route + "/result")
                elif result.get("measure_url"):
                    result["measurement"] = self.measure(project, result.get("version"))
            except CloudError as exc:
                exc.details["job"] = result
                raise
        elif result.get("status") in {"queued", "running"}:
            result.update(next_actions=[f"agentcad cloud job {project} {job} --wait"], more_at="agentcad docs cloud")
        return result

    def download(self, project, destination, *, job=None, kind="step", version=None, path=None, cwd=None, overwrite=False):
        route = self._project_route(project)
        if kind not in {"step", "render", "report"} or type(overwrite) is not bool:
            raise CloudError("invalid_artifact", "Choose artifact kind step, render, or report.")
        if job is not None:
            if version is not None or path is not None:
                raise CloudError("invalid_artifact", "Choose a job or a version/path reference, not both.")
            _id(job, "job")
            if kind == "step":
                record = self._request("GET", route + f"/jobs/{job}")
                artifact = record.get("artifact", {})
                if record.get("status") != "succeeded" or artifact.get("project") != project:
                    raise CloudError("artifact_unavailable", "This job has no completed STEP download.")
                version, path = artifact.get("version"), artifact.get("path")
            else:
                route += f"/jobs/{job}/artifacts/" + ("iso.png" if kind == "render" else "result.json")
        elif version is None or path is None or kind != "step":
            raise CloudError("invalid_artifact", "STEP needs a version/path reference or job ID; render/report needs a job ID.")
        if kind == "step":
            _version(version)
            if (not isinstance(path, str) or len(path) > 256 or "\\" in path or path.startswith("/")
                    or any(part in {".", ".."} for part in path.split("/")) or path != PurePosixPath(path).as_posix()
                    or not path.endswith("/output.step")):
                raise CloudError("invalid_artifact", "Use the returned version-relative output.step path.")
            route += f"/versions/{version}/artifacts/{quote(path, safe='/')}"
        target = _local_path(destination, cwd)
        if not target.parent.is_dir() or target.is_symlink() or target.is_dir():
            raise CloudError("invalid_destination", "Choose a file in an existing directory; symlinks and directories are not download destinations.")
        if target.exists() and not overwrite:
            raise CloudError("destination_exists", "The destination exists. Choose a new filename or explicitly set overwrite.")
        limit = STEP_LIMIT if kind == "step" else JSON_LIMIT
        temporary = None
        try:
            with self.http.stream("GET", route) as response:
                if response.status_code >= 300:
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > JSON_LIMIT:
                            raise CloudError("response_too_large", "API error exceeds the client limit.")
                    self._response_error(response, raw)
                digest, size = hashlib.sha256(), 0
                try:
                    declared = response.headers.get("Content-Length") if not response.headers.get("Content-Encoding") else None
                    expected = int(declared) if declared is not None else None
                except ValueError:
                    raise CloudError("invalid_response", "The API returned an invalid artifact length.") from None
                if expected is not None and (expected < 0 or expected > limit):
                    raise CloudError("artifact_too_large", "Artifact exceeds the client download limit.")
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".agentcad-download-", delete=False) as output:
                    temporary = Path(output.name)
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise CloudError("artifact_too_large", "Artifact exceeds the client download limit.")
                        digest.update(chunk)
                        output.write(chunk)
                if size == 0:
                    raise CloudError("invalid_response", "The API returned an empty artifact.")
                if expected is not None and size != expected:
                    raise CloudError("invalid_response", "The artifact ended before its declared length. No partial destination was published.")
                if overwrite:
                    os.replace(temporary, target)
                else:
                    os.link(temporary, target)
                return {"command": "cloud", "status": "success", "project": project, "kind": kind,
                        "destination": str(target.absolute()), "bytes": size, "sha256": digest.hexdigest()}
        except self.httpx.HTTPError:
            raise CloudError("download_failed", "The download was interrupted. No partial destination was published; retry this download.") from None
        except FileExistsError:
            raise CloudError("destination_exists", "The destination exists. Choose a new filename or explicitly set overwrite.") from None
        except OSError:
            raise CloudError("download_failed", "The destination could not be written. Check its directory and permissions.") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def cloud_call(operation, **kwargs):
    """One execution path for CLI and MCP, without changing the process directory."""
    if operation not in OPERATIONS:
        return CloudError("invalid_operation", "Unknown cloud operation.").payload(operation)
    try:
        with CloudClient.from_env() as client:
            result = getattr(client, operation)(**kwargs)
            result.setdefault("command", "cloud")
            result.setdefault("status", "success")
            return client._redact({**result, "mode": "cloud", "operation": operation})
    except CloudError as exc:
        return exc.payload(operation)
