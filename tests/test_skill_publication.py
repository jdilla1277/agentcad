"""Stable skill publication must match a successfully published package."""
import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_mirror_has_no_main_push_or_independent_release_trigger():
    workflow = yaml.load((ROOT / ".github/workflows/sync-skill.yml").read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_call", "workflow_dispatch"}
    assert workflow["on"]["workflow_dispatch"]["inputs"]["tag"]["required"] == "true"
    publish = yaml.load((ROOT / ".github/workflows/publish.yml").read_text(), Loader=yaml.BaseLoader)
    job = publish["jobs"]["sync-skill"]
    assert job["needs"] == "publish"
    assert job["uses"] == "./.github/workflows/sync-skill.yml"
    assert job["with"]["tag"] == "${{ github.event.release.tag_name }}"
    steps = workflow["jobs"]["sync"]["steps"]
    checkout = next(s for s in steps if s.get("with", {}).get("path") == "released")
    assert checkout["with"]["ref"] == "${{ steps.release.outputs.source_revision }}"
    assert any("prepare --source released" in s.get("run", "") for s in steps)
    assert "--mirror public-skill" in steps[-1]["run"]


@pytest.fixture
def publisher():
    spec = importlib.util.spec_from_file_location("skill_release", ROOT / "scripts/prepare_skill_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def release(tmp_path):
    source = tmp_path / "released"
    source.mkdir()
    for name in ("src/agentcad/guide.py", "src/agentcad/commands/skill.py", "scripts/generate_skill_md.py"):
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (source / "pyproject.toml").write_text('[project]\nversion = "0.7.2"\n')
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "release"], cwd=source, check=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as wheel:
        for name in ("guide.py", "commands/skill.py"):
            wheel.writestr("agentcad/" + name, (source / "src/agentcad" / name).read_bytes())
        wheel.writestr("agentcad-0.7.2.dist-info/METADATA", "Name: agentcad\nVersion: 0.7.2\n")
    data = stream.getvalue()
    wheel = {"filename": "agentcad-0.7.2-py3-none-any.whl", "packagetype": "bdist_wheel", "yanked": False,
             "url": "https://files.pythonhosted.org/release.whl", "digests": {"sha256": hashlib.sha256(data).hexdigest()}}
    state = {"source": source, "sha": sha, "wheel": data, "publish": "success", "latest": "0.7.2", "draft": False, "run_sha": sha}

    class Client:
        def json(self, url):
            if "/releases/tags/" in url:
                return {"tag_name": "v0.7.2", "draft": state["draft"], "prerelease": False}
            if "/git/ref/tags/" in url:
                if state.get("annotated"):
                    return {"object": {"type": "tag", "sha": "b" * 40}}
                return {"object": {"type": "commit", "sha": state["sha"]}}
            if "/git/tags/" in url:
                return {"object": {"type": "commit", "sha": state["sha"]}}
            if "/workflows/publish.yml/runs?" in url:
                return {"workflow_runs": [{"id": 1, "head_sha": state["run_sha"], "event": "release"}]}
            if "/actions/runs/1/jobs?" in url:
                return {"jobs": [{"name": "publish", "conclusion": state["publish"]}]}
            if url == "https://pypi.org/pypi/agentcad/json":
                return {"info": {"version": state["latest"]}}
            if url == "https://pypi.org/pypi/agentcad/0.7.2/json":
                return {"info": {"version": "0.7.2"}, "urls": [wheel]}
            raise AssertionError(url)

        def bytes(self, url):
            assert url == wheel["url"]
            return state["wheel"]

    state["client"] = Client()
    state["wheel_metadata"] = wheel
    return state


def test_success_and_retry_generate_identical_skill_and_provenance(publisher, release, tmp_path):
    manifest = publisher.resolve_release("v0.7.2", release["client"])
    output = tmp_path / "prepared"
    publisher.prepare(release["source"], manifest, output, release["client"])
    skill = (output / "SKILL.md").read_bytes()
    provenance = (output / "release.json").read_bytes()
    assert b"version: 0.7.2" in skill
    assert json.loads(provenance)["source_revision"] == release["sha"]
    publisher.prepare(release["source"], manifest, output, release["client"])
    assert (output / "SKILL.md").read_bytes() == skill
    assert (output / "release.json").read_bytes() == provenance


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", None])
def test_failed_or_pending_publication_refuses_prepare(publisher, release, conclusion):
    release["publish"] = conclusion
    with pytest.raises(ValueError, match="successful package publication"):
        publisher.resolve_release("v0.7.2", release["client"])


@pytest.mark.parametrize("field,value", [("draft", True), ("latest", "0.7.3"), ("run_sha", "a" * 40)])
def test_wrong_or_unpublished_release_is_refused(publisher, release, field, value):
    release[field] = value
    with pytest.raises(ValueError):
        publisher.resolve_release("v0.7.2", release["client"])


@pytest.mark.parametrize("damage", ["wheel", "source", "revision"])
def test_mismatched_artifact_or_source_preserves_prepared_files(publisher, release, tmp_path, damage):
    manifest = publisher.resolve_release("v0.7.2", release["client"])
    output = tmp_path / "prepared"
    output.mkdir()
    (output / "SKILL.md").write_text("stable")
    if damage == "wheel":
        release["wheel"] = b"wrong bytes"
    elif damage == "source":
        (release["source"] / "src/agentcad/guide.py").write_text("unreleased guide")
    else:
        manifest["source_revision"] = "a" * 40
    with pytest.raises(ValueError):
        publisher.prepare(release["source"], manifest, output, release["client"])
    assert (output / "SKILL.md").read_text() == "stable"


def test_later_publication_invalidates_an_already_prepared_retry(publisher, release):
    manifest = publisher.resolve_release("v0.7.2", release["client"])
    release["latest"] = "0.7.3"
    with pytest.raises(ValueError):
        publisher.verify_current(manifest, release["client"])


def test_annotated_release_tag_resolves_to_exact_commit(publisher, release):
    release["annotated"] = True
    assert publisher.resolve_release("v0.7.2", release["client"])["source_revision"] == release["sha"]


@pytest.mark.parametrize("tag", ["main", "v0.7.3rc1", "v0.7.2\nsource_revision=bad"])
def test_unreleased_or_malformed_tag_is_refused(publisher, tag):
    with pytest.raises(ValueError):
        publisher.resolve_release(tag)


def test_mirror_guard_allows_identical_retry_but_prevents_regression(publisher, release, tmp_path):
    manifest = publisher.resolve_release("v0.7.2", release["client"])
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    provenance = mirror / "release.json"
    provenance.write_text(json.dumps({"version": "0.7.2", "source_revision": release["sha"]}))
    publisher.guard_mirror(manifest, mirror)
    provenance.write_text(json.dumps({"version": "0.7.3", "source_revision": "a" * 40}))
    with pytest.raises(ValueError, match="newer"):
        publisher.guard_mirror(manifest, mirror)
    provenance.write_text(json.dumps({"version": "0.7.2", "source_revision": "a" * 40}))
    with pytest.raises(ValueError, match="different source"):
        publisher.guard_mirror(manifest, mirror)


@pytest.mark.parametrize("entry,content", [
    ("agentcad/guide.py", b"different packaged guide"),
    ("agentcad-0.7.2.dist-info/METADATA", b"Name: agentcad\nVersion: 0.7.3\n"),
])
def test_checksum_alone_does_not_prove_matching_package(publisher, release, tmp_path, entry, content):
    stream = BytesIO()
    with zipfile.ZipFile(BytesIO(release["wheel"])) as old, zipfile.ZipFile(stream, "w") as new:
        for name in old.namelist():
            new.writestr(name, content if name == entry else old.read(name))
    release["wheel"] = stream.getvalue()
    release["wheel_metadata"]["digests"]["sha256"] = hashlib.sha256(release["wheel"]).hexdigest()
    manifest = publisher.resolve_release("v0.7.2", release["client"])
    with pytest.raises(ValueError, match="differs|metadata"):
        publisher.prepare(release["source"], manifest, tmp_path / "out", release["client"])
    assert not (tmp_path / "out").exists()


def test_unreleased_generator_edit_is_refused(publisher, release, tmp_path):
    manifest = publisher.resolve_release("v0.7.2", release["client"])
    (release["source"] / "scripts/generate_skill_md.py").write_text("raise AssertionError('must not execute')")
    with pytest.raises(ValueError, match="local changes"):
        publisher.prepare(release["source"], manifest, tmp_path / "out", release["client"])


def test_pypi_metadata_waits_for_propagation(publisher, monkeypatch):
    from urllib.error import HTTPError

    url = publisher.PYPI + "/0.8.1/json"
    responses = [HTTPError(url, 404, "Not found", {}, None)] * 3 + [b'{"info":{"version":"0.8.1"}}']
    calls, sleeps = [], []
    client = publisher.Client()

    def read(requested):
        calls.append(requested)
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(client, "bytes", read)
    import time
    monkeypatch.setattr(time, "sleep", sleeps.append)
    assert client.json(url)["info"]["version"] == "0.8.1"
    assert calls == [url] * 4
    assert sleeps == [15, 30, 60]


@pytest.mark.parametrize("url,code,attempts", [
    ("https://pypi.org/pypi/agentcad/json", 404, 4),
    ("https://pypi.org/pypi/agentcad/0.8.1/json", 403, 1),
    ("https://api.github.com/repos/jdilla1277/agentcad/releases/tags/v0.8.1", 404, 1),
    ("https://files.pythonhosted.org/release.whl", 404, 1),
])
def test_metadata_retry_is_bounded_and_does_not_hide_other_failures(publisher, monkeypatch, url, code, attempts):
    from urllib.error import HTTPError
    import time

    calls, sleeps = [], []
    error = HTTPError(url, code, "Not available", {}, None)
    client = publisher.Client()

    def read(requested):
        calls.append(requested)
        raise error

    monkeypatch.setattr(client, "bytes", read)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    with pytest.raises(HTTPError) as caught:
        client.json(url)
    assert caught.value is error
    assert calls == [url] * attempts
    assert sleeps == ([15, 30, 60] if attempts == 4 else [])
