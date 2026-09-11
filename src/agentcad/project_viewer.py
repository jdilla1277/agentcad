"""Local project viewer registration and process ownership (no CAD imports).

The persisted port and secret belong to the user, independently of Python
environments. Protocol mismatches require an explicit stop from the owning
installation; we never signal a PID obtained from a stale file.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from agentcad.versioning import atomic_write_json

PROTOCOL = 1


class ViewerUnavailable(RuntimeError):
    pass


def runtime_dir() -> Path:
    import click
    ctx = click.get_current_context(silent=True)
    layout = ctx.meta.get("project_layout") if ctx else None
    # Configured builds own their complete viewer service state. The child
    # receives this directory explicitly in its environment at startup.
    path = (layout.artifact_path(".agentcad/viewer") if layout and layout.configured
            else Path(os.environ.get("AGENTCAD_VIEWER_HOME", Path.home() / ".cache/agentcad/viewer")))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path.resolve()


@contextmanager
def locked(name="registry"):
    """OS-released locks survive process crashes without stale lock guessing."""
    with (runtime_dir() / f"{name}.lock").open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            # Windows locks deny reads too. Lock beyond EOF directly instead
            # of reading/initializing a byte another process may already own.
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
        deadline = time.monotonic() + 8
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ViewerUnavailable(f"Viewer {name} is busy; retry shortly.")
                time.sleep(0.05)
        try:
            yield
        finally:
            release()


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def configuration():
    with locked():
        path = runtime_dir() / "config.json"
        config = read_json(path)
        if config is None:
            config = {"port": 0, "secret": secrets.token_hex(32)}
            atomic_write_json(path, config)
        return config


def project_token(root: Path) -> str:
    return hmac.new(
        configuration()["secret"].encode(),
        str(root.resolve()).encode(), hashlib.sha256,
    ).hexdigest()


def project_record(token: str):
    # Callers on the HTTP boundary must validate tokens before path creation.
    return runtime_dir() / f"project-{token}.json"


def publish(root: Path, version_dir: Path, *, started_ns: int) -> bool:
    """Publish only complete, successful viewer snapshots; never regress N to N-1."""
    root, version_dir = Path(root).resolve(), Path(version_dir).absolute()
    meta = read_json(version_dir / "meta.json", {})
    if (meta.get("status") != "success"
            or meta.get("artifacts", {}).get("viewer", {}).get("status") != "success"
            or not (version_dir / "viewer.html").is_file()
            or not (version_dir / "output.glb").is_file()):
        return False
    if version_dir.parent != root or version_dir.resolve() != version_dir:
        raise ViewerUnavailable("Viewer snapshot must be inside the project.")
    token = project_token(root)
    with locked():
        record = read_json(project_record(token), {"root": str(root), "versions": {}})
        number = int(meta["version"])
        record["versions"][str(number)] = str(version_dir / "viewer.html")
        latest = record.get("latest", {})
        if number >= latest.get("version", 0):
            record["latest"] = {"version": number, "label": meta["label"],
                                "artifact": f"artifacts/{number}/viewer.html"}
        if started_ns >= record.get("attempt", {}).get("started_ns", 0):
            record["attempt"] = {"status": "success", "label": meta["label"],
                                 "started_ns": started_ns, "finished_at": time.time()}
        atomic_write_json(project_record(token), record)
    return True


def record_failure(root: Path, label: str, status: str, *, started_ns: int):
    # Failed runs never create or start a viewer. There must already be a
    # registered good build; keep full diagnostics in CLI output, not HTTP.
    if not (runtime_dir() / "config.json").exists():
        return
    token = project_token(Path(root))
    with locked():
        record = read_json(project_record(token))
        if record and started_ns >= record.get("attempt", {}).get("started_ns", 0):
            record["attempt"] = {"status": status, "label": label,
                                 "started_ns": started_ns, "finished_at": time.time()}
            atomic_write_json(project_record(token), record)


def request(config, route, payload=None, timeout=0.5):
    req = Request(
        f"http://127.0.0.1:{config['port']}/{route}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {config['secret']}", "Content-Type": "application/json"},
    )
    with build_opener(ProxyHandler({})).open(req, timeout=timeout) as response:
        return json.load(response)


def service_status():
    config = read_json(runtime_dir() / "config.json")
    if config and config.get("port"):
        try:
            state = request(config, "health")
            if state.get("protocol") != PROTOCOL:
                raise ViewerUnavailable("Viewer protocol mismatch. Stop it using the installation that started it, then retry.")
            return {"running": True, "port": config["port"], **state}
        except ViewerUnavailable:
            raise
        except (OSError, ValueError):
            pass
    return {"running": False, "port": config.get("port") if config else None}


def ensure_service():
    with locked("startup"):
        if service_status()["running"]:
            return configuration()
        configuration()
        env = os.environ.copy()
        env["AGENTCAD_VIEWER_HOME"] = str(runtime_dir())
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
        with (runtime_dir() / "service.log").open("ab") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "agentcad.viewer_service"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                cwd=runtime_dir(), env=env, start_new_session=True,
            )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if service_status()["running"]:
                return configuration()
            if process.poll() is not None:
                break
            time.sleep(0.05)
        # A child started by this call is safe to stop; never signal a PID from disk.
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=3)
        raise ViewerUnavailable(
            f"Could not start viewer on its saved port. Another process may own it. "
            f"See {runtime_dir() / 'service.log'}; the saved URL was preserved."
        )


def stop_service():
    with locked("startup"):
        state = service_status()
        if not state["running"]:
            return {"status": "stopped", "running": False}
        config = configuration()
        request(config, "stop", {})
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if not service_status()["running"]:
                return {"status": "stopped", "running": False}
            time.sleep(0.05)
        raise ViewerUnavailable("Viewer is still stopping; retry status shortly.")


def open_project(root: Path, open_browser):
    token = project_token(Path(root))
    if not read_json(project_record(token), {}).get("latest"):
        raise ViewerUnavailable("No completed project viewer. Run a build with viewing enabled first.")
    config = ensure_service()
    claim = request(config, "open", {"project": token})
    url = f"http://127.0.0.1:{config['port']}/projects/{token}/"
    opened = False
    if not claim["reused"]:
        try:
            opened = open_browser(url) is not False
        finally:
            if not opened:
                request(config, "release", {"project": token})
    return {"url": url, "latest_version": claim["version"],
            "opened": opened, "reused": claim["reused"]}


def handoff(root: Path, version_dir: Path, *, open_view: bool):
    """Best effort at the CLI boundary; failures must not invalidate a CAD build."""
    import click
    from agentcad.commands.view import _open_browser
    ctx = click.get_current_context(silent=True)
    started_ns = ctx.meta.get("viewer_started_ns", time.time_ns()) if ctx else time.time_ns()
    try:
        if publish(root, version_dir, started_ns=started_ns) and open_view:
            return open_project(root, _open_browser)
    except Exception as exc:
        return {"status": "unavailable", "opened": False, "reused": False,
                "message": f"Project viewer unavailable: {exc}"}
    return None
