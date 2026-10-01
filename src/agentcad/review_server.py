"""Loopback-only HTTP bridge between generated viewers and local review JSON."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agentcad.reviews import (
    create_comment,
    delete_draft_comment,
    list_comments,
    reply_to_comment,
    submit_drafts,
    transition_comment,
    update_draft_comment,
)
from agentcad.versioning import atomic_write_json


STATE_FILE = Path(".agentcad/review-server.json")
_START_LOCK_TIMEOUT_S = 6.0
_START_LOCK_STALE_S = 30.0


def _state_path(project_dir: Path) -> Path:
    return Path(project_dir) / STATE_FILE


def _request_json(url: str, token: str, timeout: float = 0.5) -> dict | None:
    request = urllib.request.Request(url, headers={"X-AgentCAD-Review-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None


def _read_live_state(project_dir: Path) -> dict | None:
    path = _state_path(project_dir)
    try:
        state = json.loads(path.read_text())
        url = f"http://127.0.0.1:{int(state['port'])}/api/ping"
        response = _request_json(url, str(state["token"]))
        if response and response.get("status") == "ok":
            return state
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        pass
    return None


@contextmanager
def _server_start_lock(project_dir: Path):
    lock = Path(project_dir) / ".agentcad" / "review-server.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _START_LOCK_TIMEOUT_S
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > _START_LOCK_STALE_S:
                    lock.rmdir()
                    continue
            except (FileNotFoundError, OSError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Timed out waiting to start the review server")
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.rmdir()


def ensure_review_server(project_dir: Path, *, timeout_s: float = 5.0) -> dict:
    project_dir = Path(project_dir).resolve()
    live = _read_live_state(project_dir)
    if live:
        return live
    with _server_start_lock(project_dir):
        live = _read_live_state(project_dir)
        if live:
            return live
        token = secrets.token_urlsafe(24)
        state_path = _state_path(project_dir)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = (state_path.parent / "review-server.log").open("a")
        env = os.environ.copy()
        src_root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = os.pathsep.join(
            value for value in (src_root, env.get("PYTHONPATH")) if value
        )
        try:
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "agentcad.review_server",
                    "--project",
                    str(project_dir),
                    f"--token={token}",
                    "--state-file",
                    str(state_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=log_handle,
                start_new_session=True,
                close_fds=True,
                env=env,
            )
        finally:
            log_handle.close()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            live = _read_live_state(project_dir)
            if live and live.get("token") == token:
                return live
            time.sleep(0.05)
        raise RuntimeError("Review server did not become ready")


def viewer_url(
    viewer_path: Path,
    *,
    project_dir: Path | None = None,
    require_review: bool = False,
) -> str:
    viewer_path = Path(viewer_path).resolve()
    if os.environ.get("AGENTCAD_REVIEW_SERVER", "1") in {"0", "false", "False"}:
        return viewer_path.as_uri()
    project_dir = Path(project_dir or Path.cwd()).resolve()
    try:
        relative = viewer_path.relative_to(project_dir)
    except ValueError:
        return viewer_path.as_uri()
    if not (project_dir / "agentcad.json").exists():
        return viewer_path.as_uri()
    try:
        state = ensure_review_server(project_dir)
    except RuntimeError:
        if require_review:
            raise
        return viewer_path.as_uri()
    query = urllib.parse.urlencode({"path": relative.as_posix(), "token": state["token"]})
    return f"http://127.0.0.1:{state['port']}/viewer?{query}"


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "AgentCADReview/1"

    @property
    def project_dir(self) -> Path:
        return self.server.project_dir

    def log_message(self, format, *args):
        return

    def _token(self) -> str:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return self.headers.get("X-AgentCAD-Review-Token") or query.get("token", [""])[0]

    def _authorized(self) -> bool:
        authorized = secrets.compare_digest(self._token(), self.server.review_token)
        if authorized:
            self.server.last_activity = time.monotonic()
        return authorized

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise ValueError("Review request is too large")
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("Review request body must be a JSON object")
        return payload

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if not self._authorized():
            self._json(403, {"status": "error", "message": "Invalid review token"})
            return
        if parsed.path == "/api/ping":
            self._json(200, {"status": "ok"})
            return
        if parsed.path == "/api/comments":
            self._json(200, {"status": "success", "comments": list_comments(self.project_dir)})
            return
        if parsed.path == "/viewer":
            query = urllib.parse.parse_qs(parsed.query)
            relative = query.get("path", [""])[0]
            candidate = (self.project_dir / relative).resolve()
            try:
                candidate.relative_to(self.project_dir)
            except ValueError:
                self._json(403, {"status": "error", "message": "Viewer path is outside project"})
                return
            if candidate.suffix.lower() != ".html" or not candidate.is_file():
                self._json(404, {"status": "error", "message": "Viewer not found"})
                return
            body = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(404, {"status": "error", "message": "Not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if not self._authorized():
            self._json(403, {"status": "error", "message": "Invalid review token"})
            return
        try:
            payload = self._body()
            if parsed.path == "/api/comments":
                comment = create_comment(self.project_dir, payload)
                self._json(201, {"status": "success", "comment": comment})
                return
            if parsed.path == "/api/reviews/submit":
                batch = submit_drafts(self.project_dir, payload.get("comment_ids"))
                self._json(200, {"status": "success", **batch})
                return
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "comments"]:
                if parts[3] == "update":
                    comment = update_draft_comment(self.project_dir, parts[2], payload)
                    self._json(200, {"status": "success", "comment": comment})
                    return
                if parts[3] == "delete":
                    comment = delete_draft_comment(self.project_dir, parts[2])
                    self._json(200, {"status": "success", "comment": comment})
                    return
                if parts[3] == "reply":
                    comment = reply_to_comment(
                        self.project_dir,
                        parts[2],
                        payload.get("message", ""),
                        actor="human",
                        version=payload.get("version"),
                    )
                    self._json(200, {"status": "success", "comment": comment})
                    return
                comment = transition_comment(
                    self.project_dir,
                    parts[2],
                    parts[3],
                    version=payload.get("version"),
                    actor="human",
                    message=payload.get("message"),
                )
                self._json(200, {"status": "success", "comment": comment})
                return
            self._json(404, {"status": "error", "message": "Not found"})
        except KeyError as exc:
            self._json(404, {"status": "error", "message": f"Comment {exc.args[0]} not found"})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"status": "error", "message": str(exc)})


def serve(project_dir: Path, token: str, state_file: Path) -> None:
    project_dir = Path(project_dir).resolve()
    server = ThreadingHTTPServer(("127.0.0.1", 0), ReviewHandler)
    server.project_dir = project_dir
    server.review_token = token
    server.last_activity = time.monotonic()
    server.daemon_threads = True
    server.timeout = 1.0
    state = {"pid": os.getpid(), "port": server.server_port, "token": token, "project": str(project_dir)}
    atomic_write_json(state_file, state)
    try:
        idle_timeout = float(os.environ.get("AGENTCAD_REVIEW_IDLE_TIMEOUT_S", "1800"))
        while time.monotonic() - server.last_activity < idle_timeout:
            server.handle_request()
    finally:
        server.server_close()
        try:
            current = json.loads(state_file.read_text())
            if current.get("pid") == os.getpid() and current.get("token") == token:
                state_file.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--state-file", required=True)
    args = parser.parse_args(argv)
    serve(Path(args.project), args.token, Path(args.state_file))


if __name__ == "__main__":
    main()
