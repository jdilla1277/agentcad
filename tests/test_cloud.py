"""Cloud contracts use explicit files/references and never retry a submission silently."""

import json
import os
from pathlib import Path

import httpx
import pytest

from agentcad.cloud import CloudClient, CloudError

PROJECT = "prj_" + "a" * 24
JOB = "job_" + "b" * 24
KEY = "key_" + "c" * 24
SECRET = "test-secret-" + "d" * 32
ROOT = f"/v1/projects/{PROJECT}"


def client(handler):
    return CloudClient("https://api.example.test", KEY, SECRET, transport=httpx.MockTransport(handler))


def queued(kind="run"):
    return {"id": JOB, "project_id": PROJECT, "kind": kind, "status": "queued",
            "poll_url": f"{ROOT}/jobs/{JOB}"}


def test_upload_is_explicit_and_retry_key_is_returned(tmp_path):
    (tmp_path / "part.py").write_text("show_object(Box(10,20,5))")
    (tmp_path / "unrelated.txt").write_text("not uploaded")
    requests = []
    def handle(request):
        requests.append(request)
        assert request.url.path == ROOT + "/runs" and request.method == "POST"
        assert request.headers["X-AgentCAD-Key-ID"] == KEY and request.headers["X-AgentCAD-Key-Secret"] == SECRET
        assert b"show_object" in request.content and b"unrelated.txt" not in request.content
        assert b'filename="part.py"' in request.content
        return httpx.Response(202, json=queued())
    before = Path.cwd()
    with client(handle) as api:
        result = api.run(PROJECT, "part.py", "first", cwd=str(tmp_path))
    assert Path.cwd() == before and len(requests) == 1
    assert result["status"] == "queued" and result["idempotency_key"] == requests[0].headers["Idempotency-Key"]
    assert result["next_actions"] and result["more_at"] == "agentcad docs cloud"
    assert SECRET not in json.dumps(result)


def test_lost_submission_returns_key_without_retry_and_redacts_exception(tmp_path):
    (tmp_path / "part.py").write_text("show_object(Box(10,20,5))")
    requests = []
    def lost(request):
        requests.append(request)
        raise httpx.ReadTimeout(SECRET, request=request)
    with client(lost) as api, pytest.raises(CloudError) as caught:
        api.run(PROJECT, "part.py", "first", cwd=str(tmp_path), idempotency_key="retry-this")
    assert len(requests) == 1
    payload = caught.value.payload("run")
    assert payload["idempotency_key"] == "retry-this" and payload["reason"] == "request_outcome_unknown"
    assert SECRET not in json.dumps(payload) and "retry" in payload["message"].lower()


@pytest.mark.parametrize("passed", [True, False, None])
def test_completed_job_preserves_verdicts_and_uses_own_result_route(passed):
    paths = []
    result = {"command": "check-spec", "status": "success", "geometry_valid": True,
              "passed": passed, "unsupported_requirements": [{"path": "material", "value": "steel"}] if passed is None else []}
    def handle(request):
        paths.append(str(request.url))
        if request.url.path.endswith("/result"):
            return httpx.Response(200, json=result)
        return httpx.Response(200, json={**queued("check_spec"), "status": "succeeded", "version": 1,
                                         "result_url": "https://elsewhere.example/steal-credentials"})
    with client(handle) as api:
        response = api.job(PROJECT, JOB)
    assert response["status"] == "succeeded" and response["result"] == result
    assert paths == [f"https://api.example.test{ROOT}/jobs/{JOB}", f"https://api.example.test{ROOT}/jobs/{JOB}/result"]


def test_queued_job_does_not_fetch_results_or_submit_again():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=queued())
    with client(handle) as api:
        result = api.job(PROJECT, JOB)
    assert result["status"] == "queued" and "result" not in result
    assert len(requests) == 1 and requests[0].method == "GET"


def test_completed_build_returns_its_measurement():
    measurement = {"command": "measure", "status": "success", "metrics": {"is_valid": True, "volume": 123}}
    def handle(request):
        if request.url.path.endswith("/measure"):
            return httpx.Response(200, json=measurement)
        return httpx.Response(200, json={**queued(), "status": "succeeded", "version": 2, "measure_url": "/ignored"})
    with client(handle) as api:
        assert api.job(PROJECT, JOB)["measurement"] == measurement


def test_spec_json_is_uploaded_without_dropping_unsupported_requirements(tmp_path):
    spec = {"expect_solids": 2, "material": "steel"}
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    def handle(request):
        assert request.url.path == ROOT + "/versions/1/check-spec"
        assert json.loads(request.content) == spec
        return httpx.Response(202, json=queued("check_spec"))
    with client(handle) as api:
        assert api.check_spec(PROJECT, 1, "spec.json", cwd=str(tmp_path))["status"] == "queued"


@pytest.mark.parametrize("fault", ["missing", "directory", "oversized", "non_utf8", "empty", "nonfinite_spec"])
def test_bad_local_inputs_fail_before_any_request(tmp_path, fault):
    path = tmp_path / "part.py"
    if fault == "directory":
        path.mkdir()
    elif fault == "oversized":
        path.write_bytes(b"x" * (256 * 1024 + 1))
    elif fault == "non_utf8":
        path.write_bytes(b"\xff\x00")
    elif fault == "empty":
        path.write_bytes(b"")
    elif fault == "nonfinite_spec":
        path.write_text('{"expect_solids": NaN}')
    with client(lambda _: pytest.fail("Bad input made an HTTP request")) as api, pytest.raises(CloudError):
        if fault == "nonfinite_spec":
            api.check_spec(PROJECT, 1, str(path))
        else:
            api.run(PROJECT, str(path), "first")


@pytest.mark.parametrize("url", ["http://api.example.test", "https://user:secret@api.example.test", "https://api.example.test/?key=secret", "https://api.example.test/path", "https://api.example.test/#secret"])
def test_credentials_require_an_explicit_safe_origin(url):
    with pytest.raises(CloudError):
        CloudClient(url, KEY, SECRET)


def test_missing_configuration_never_prints_configured_credentials(monkeypatch):
    monkeypatch.setenv("AGENTCAD_KEY_SECRET", SECRET)
    monkeypatch.delenv("AGENTCAD_API_URL", raising=False)
    monkeypatch.delenv("AGENTCAD_KEY_ID", raising=False)
    with pytest.raises(CloudError) as caught:
        CloudClient.from_env()
    assert SECRET not in json.dumps(caught.value.payload("projects"))


@pytest.mark.parametrize("status", [401, 404, 409])
def test_api_errors_keep_status_without_leaking_a_reflected_secret(status):
    def handle(_):
        return httpx.Response(status, json={"detail": {"reason": "rejected", "message": "Rejected " + SECRET}})
    with client(handle) as api, pytest.raises(CloudError) as caught:
        api.project(PROJECT)
    payload = caught.value.payload("project")
    assert payload["http_status"] == status and SECRET not in json.dumps(payload)


def test_redirect_is_not_followed_with_credentials():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://elsewhere.example/"})
    with client(handle) as api, pytest.raises(CloudError):
        api.projects()
    assert len(requests) == 1


@pytest.mark.parametrize("project,version", [("../projects/other", 1), (PROJECT, True), (PROJECT, 0)])
def test_cloud_references_cannot_be_paths_or_invalid_versions(project, version):
    with client(lambda _: pytest.fail("Invalid reference made a request")) as api, pytest.raises(CloudError):
        api.measure(project, version)


def test_poll_wait_is_bounded():
    with client(lambda _: pytest.fail("Invalid wait made a request")) as api, pytest.raises(CloudError):
        api.job(PROJECT, JOB, wait_seconds=61)


def test_explicit_artifact_download_is_complete_and_not_overwritten(tmp_path):
    destination = tmp_path / "model.step"
    content = b"ISO-10303-21;\ncomplete model"
    def handle(request):
        assert request.url.path == ROOT + "/versions/2/artifacts/v2_model/output.step"
        return httpx.Response(200, content=content, headers={"Content-Type": "model/step"})
    with client(handle) as api:
        result = api.download(PROJECT, str(destination), version=2, path="v2_model/output.step")
        assert result["bytes"] == len(content) and destination.read_bytes() == content
        with pytest.raises(CloudError):
            api.download(PROJECT, str(destination), version=2, path="v2_model/output.step")
    assert destination.read_bytes() == content
    assert sorted(p.name for p in tmp_path.iterdir()) == ["model.step"]


def test_interrupted_download_leaves_no_partial_destination(tmp_path):
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield b"ISO-10303-21;"
            raise httpx.ReadTimeout(SECRET)
    def handle(_):
        return httpx.Response(200, stream=Broken(), headers={"Content-Type": "model/step"})
    with client(handle) as api, pytest.raises(CloudError):
        api.download(PROJECT, str(tmp_path / "model.step"), version=1, path="v1_model/output.step")
    assert list(tmp_path.iterdir()) == []


def test_cloud_download_rejects_traversal_and_ambiguous_sources(tmp_path):
    with client(lambda _: pytest.fail("Invalid artifact made a request")) as api:
        for params in ({"version": 1, "path": "../secret"}, {"version": 1, "path": "/secret"},
                       {"version": 1, "path": "v1_model/output.step", "job": JOB}):
            with pytest.raises(CloudError):
                api.download(PROJECT, str(tmp_path / "output"), **params)


def test_truncated_declared_download_is_not_published(tmp_path):
    def handle(_):
        return httpx.Response(200, content=b"short", headers={"Content-Length": "100"})
    with client(handle) as api, pytest.raises(CloudError):
        api.download(PROJECT, str(tmp_path / "model.step"), version=1, path="v1_model/output.step")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("project", ["$(untrusted-command)", "prj_" + "e" * 24])
def test_submission_requires_returned_job_ids_before_suggesting_a_command(tmp_path, project):
    (tmp_path / "part.py").write_text("show_object(Box(10,20,5))")
    def handle(_):
        return httpx.Response(202, json={**queued(), "project_id": project})
    with client(handle) as api, pytest.raises(CloudError) as caught:
        api.run(PROJECT, str(tmp_path / "part.py"), "first")
    assert caught.value.payload("run")["idempotency_key"]


def test_symlink_destination_never_replaces_its_target(tmp_path):
    original = tmp_path / "original.step"
    original.write_bytes(b"keep me")
    destination = tmp_path / "download.step"
    destination.symlink_to(original)
    with client(lambda _: pytest.fail("Symlink destination made a request")) as api, pytest.raises(CloudError):
        api.download(PROJECT, str(destination), version=1, path="v1_model/output.step", overwrite=True)
    assert original.read_bytes() == b"keep me"
