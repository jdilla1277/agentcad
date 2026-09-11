"""Local viewer contracts: real HTTP, isolated runtime, no CAD dependency."""
import json
import socket
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from agentcad import project_viewer as live


def version(root, number, *, ready=True):
    root.mkdir(exist_ok=True)
    path = root / f"v{number}_part"
    path.mkdir()
    (path / "viewer.html").write_text(f"<html>Version {number}</html>")
    (path / "output.glb").write_bytes(b"glTF")
    (path / "meta.json").write_text(json.dumps({
        "status": "success", "version": number, "label": "part",
        "core": {"status": "success"},
        "artifacts": {"viewer": {"status": "success" if ready else "pending"}},
    }))
    return path


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCAD_VIEWER_HOME", str(tmp_path / "runtime"))
    yield
    live.stop_service()


def read(url):
    with urlopen(url, timeout=3) as response:
        return response.read()


def test_twenty_versions_same_url_restart_and_no_cad_import(service, tmp_path):
    root = tmp_path / "model"
    urls = []
    for number in range(1, 21):
        path = version(root, number)
        live.publish(root, path, started_ns=number)
        result = live.open_project(root, lambda url: True)
        assert result["latest_version"] == number
        urls.append(result["url"])
        state = json.loads(read(result["url"] + "state"))
        assert state["latest"]["version"] == number
        assert read(result["url"] + state["latest"]["artifact"]) == (
            f"<html>Version {number}</html>".encode()
        )
    assert len(set(urls)) == 1
    assert live.service_status()["cad_loaded"] is False
    assert live.stop_service()["status"] == "stopped"
    assert live.open_project(root, lambda url: True)["url"] == urls[0]
    assert json.loads(read(urls[0] + "state"))["latest"]["version"] == 20


def test_pending_failure_and_out_of_order_completion_keep_latest(service, tmp_path):
    root = tmp_path / "model"
    live.publish(root, version(root, 2), started_ns=20)
    url = live.open_project(root, lambda url: True)["url"]
    live.publish(root, version(root, 1), started_ns=10)
    assert not live.publish(root, version(root, 3, ready=False), started_ns=30)
    live.record_failure(root, "broken", "failed", started_ns=40)
    live.record_failure(root, "older", "failed", started_ns=5)
    state = json.loads(read(url + "state"))
    assert state["latest"]["version"] == 2
    assert state["attempt"]["label"] == "broken"
    live.publish(root, version(root, 4), started_ns=50)
    assert json.loads(read(url + "state"))["attempt"]["status"] == "success"


def test_project_isolation_and_only_registered_viewers_are_served(service, tmp_path):
    roots = [tmp_path / "one", tmp_path / "two"]
    with ThreadPoolExecutor() as executor:
        list(executor.map(lambda root: live.publish(root, version(root, 1), started_ns=1), roots))
    urls = [live.open_project(root, lambda url: True)["url"] for root in roots]
    assert urls[0] != urls[1]
    state = json.loads(read(urls[0] + "state"))
    artifact = state["latest"]["artifact"]
    assert read(urls[0] + artifact)
    for suffix in ("../state", "../../config.json", "%2e%2e/config.json", "artifacts/1/script.py", "artifacts/999/viewer.html"):
        with pytest.raises(HTTPError):
            read(urls[0] + suffix)
    snapshot = roots[0] / "v1_part" / "viewer.html"
    snapshot.unlink()
    snapshot.symlink_to(roots[1] / "v1_part" / "viewer.html")
    with pytest.raises(HTTPError):
        read(urls[0] + artifact)


def test_active_heartbeat_reuses_viewer_and_failed_launch_releases_claim(service, tmp_path):
    root = tmp_path / "model"
    live.publish(root, version(root, 1), started_ns=1)
    first = live.open_project(root, lambda url: False)
    opened = []
    second = live.open_project(root, lambda url: opened.append(url) or True)
    assert opened == [first["url"]]
    read(second["url"] + "state?client=test-client")
    third = live.open_project(root, lambda url: pytest.fail("duplicate tab"))
    assert third["reused"] is True
    assert third["opened"] is False
    read(Request(second["url"] + "leave", data=b'{"client":"test-client"}',
                 headers={"Content-Type":"application/json"}))
    fourth = live.open_project(root, lambda url: opened.append(url) or True)
    assert fourth["opened"]
    assert len(opened) == 2


def test_closing_one_of_two_clients_keeps_the_other_active(service, tmp_path):
    root = tmp_path / "model"
    live.publish(root, version(root, 1), started_ns=1)
    url = live.open_project(root, lambda _: True)["url"]
    read(url + "state?client=one")
    read(url + "state?client=two")
    read(Request(url + "leave", data=b'{"client":"one"}'))
    assert live.open_project(root, lambda _: pytest.fail("other client still active"))["reused"]


def test_restart_reuses_existing_tab_before_its_next_poll(service, tmp_path):
    root = tmp_path / "model"
    live.publish(root, version(root, 1), started_ns=1)
    url = live.open_project(root, lambda _: True)["url"]
    read(url + "state?client=connected")
    live.stop_service()
    # No browser heartbeat after restart: opening itself starts the service.
    result = live.open_project(root, lambda _: pytest.fail("duplicate during reconnect"))
    assert result["url"] == url
    assert result["reused"]


def test_port_collision_does_not_change_bookmark_or_stop_other_listener(service, tmp_path):
    root = tmp_path / "model"
    live.publish(root, version(root, 1), started_ns=1)
    original = live.open_project(root, lambda url: True)["url"]
    port = live.service_status()["port"]
    live.stop_service()
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen()
        with pytest.raises(live.ViewerUnavailable, match="port|Port"):
            live.open_project(root, lambda url: True)
        assert listener.getsockname()[1] == port
    assert live.open_project(root, lambda url: True)["url"] == original


def test_service_rejects_cross_origin_and_bad_host(service, tmp_path):
    root = tmp_path / "model"
    live.publish(root, version(root, 1), started_ns=1)
    url = live.open_project(root, lambda url: True)["url"]
    for headers in ({"Origin": "https://example.com"}, {"Host": "attacker.example"}):
        with pytest.raises(HTTPError) as error:
            read(Request(url + "state", headers=headers))
        assert error.value.code == 403


def test_publish_and_failure_do_not_start_service(service, tmp_path):
    root = tmp_path / "model"
    live.publish(root, version(root, 1), started_ns=1)
    live.record_failure(root, "failed", "failed", started_ns=2)
    assert live.service_status()["running"] is False


def test_concurrent_service_starts_share_one_instance(service, tmp_path):
    root = tmp_path / "model"
    live.publish(root, version(root, 1), started_ns=1)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: live.ensure_service(), range(4)))
    assert len({result["port"] for result in results}) == 1
    assert live.service_status()["running"] is True


def test_service_crash_releases_ownership_and_keeps_url(service, tmp_path):
    root = tmp_path / "model"
    live.publish(root, version(root, 1), started_ns=1)
    original = live.open_project(root, lambda _: True)["url"]
    live.stop_service()
    # Own this exact child, so a simulated crash cannot touch another daemon.
    child = subprocess.Popen([sys.executable, "-m", "agentcad.viewer_service"],
                             env=os.environ.copy(), stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while not live.service_status()["running"] and time.monotonic() < deadline:
            time.sleep(.05)
        assert live.service_status()["running"]
        child.kill()
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
    assert live.open_project(root, lambda _: True)["url"] == original


def test_incompatible_protocol_requires_explicit_action(service, monkeypatch):
    config = live.configuration()
    config["port"] = 12345
    live.atomic_write_json(live.runtime_dir() / "config.json", config)
    with monkeypatch.context() as patch:
        patch.setattr(live, "request", lambda *args, **kwargs: {"protocol": 999})
        with pytest.raises(live.ViewerUnavailable, match="protocol mismatch"):
            live.ensure_service()
    config["port"] = 0
    live.atomic_write_json(live.runtime_dir() / "config.json", config)
