"""Loopback-only viewer process. Run via project_viewer.ensure_service()."""
from __future__ import annotations

import json
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from agentcad import __version__
from agentcad import project_viewer as live
from agentcad.project_viewer_page import PAGE
from agentcad.versioning import atomic_write_json


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config):
        super().__init__(("127.0.0.1", config["port"]), Handler)
        self.secret = config["secret"]
        self.instance = secrets.token_hex(16)
        # Short-lived leases survive ordinary service restarts, so an open
        # command need not race an existing tab's first reconnecting heartbeat.
        self.clients = live.read_json(live.runtime_dir() / "clients.json", {})
        self.claims = {}
        self.guard = threading.Lock()

    def persist_clients(self):
        now = time.time()
        self.clients = {
            token: {client: seen for client, seen in clients.items() if 0 <= now - seen < 90}
            for token, clients in self.clients.items()
        }
        self.clients = {token: clients for token, clients in self.clients.items() if clients}
        atomic_write_json(live.runtime_dir() / "clients.json", self.clients)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass  # URLs contain private project capabilities.

    def reply(self, status, body, kind="application/json"):
        data = json.dumps(body).encode() if kind == "application/json" else body
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def allowed(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        if (self.headers.get("Host") != origin.removeprefix("http://")
                or self.headers.get("Origin", origin) != origin
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self.reply(403, {"error": "Local same-origin requests only"})
            return False
        return True

    def admin(self):
        if self.headers.get("Authorization") != f"Bearer {self.server.secret}":
            self.reply(403, {"error": "Unauthorized"})
            return False
        return True

    def do_GET(self):
        if not self.allowed():
            return
        path = urlsplit(self.path).path
        if path == "/health":
            if self.admin():
                self.reply(200, {"protocol": live.PROTOCOL, "instance": self.server.instance,
                                 "version": __version__, "cad_loaded": "OCP" in sys.modules})
            return
        match = re.fullmatch(r"/projects/([a-f0-9]{64})/(.*)", path)
        if not match:
            self.reply(404, {"error": "Unknown project"})
            return
        token, route = match.groups()
        try:
            record = live.read_json(live.project_record(token))
            if record is None:
                self.reply(404, {"error": "Unknown project"})
            elif route == "":
                self.reply(200, PAGE.encode(), "text/html; charset=utf-8")
            elif route == "state":
                client = parse_qs(urlsplit(self.path).query).get("client", [""])[0]
                if re.fullmatch(r"[a-zA-Z0-9-]{1,64}", client):
                    with self.server.guard:
                        now = time.time()
                        clients = self.server.clients.setdefault(token, {})
                        clients[client] = now
                        self.server.clients[token] = {key: seen for key, seen in clients.items() if now - seen < 90}
                        self.server.claims.pop(token, None)
                        self.server.persist_clients()
                self.reply(200, {"latest": record.get("latest"), "attempt": record.get("attempt")})
            else:
                artifact = re.fullmatch(r"artifacts/(\d+)/viewer.html", route)
                source = record.get("versions", {}).get(artifact[1]) if artifact else None
                if source is None:
                    self.reply(404, {"error": "Unknown viewer"})
                    return
                file = Path(source)
                root = Path(record["root"])
                # Serve only this exact registered snapshot; never source,
                # metadata, directory listings, or redirected symlinks.
                if file.resolve() != file or file.parent.parent != root or root.resolve() != root:
                    self.reply(403, {"error": "Viewer path changed"})
                    return
                self.reply(200, file.read_bytes(), "text/html; charset=utf-8")
        except (OSError, ValueError):
            self.reply(503, {"error": "Viewer artifact unavailable; retry after rebuilding"})

    def do_POST(self):
        if not self.allowed():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 4096:
                raise ValueError()
            payload = json.loads(self.rfile.read(length))
            route = urlsplit(self.path).path
            leaving = re.fullmatch(r"/projects/([a-f0-9]{64})/leave", route)
            if leaving:
                client = payload.get("client", "")
                if not isinstance(client, str):
                    raise ValueError()
                with self.server.guard:
                    self.server.clients.get(leaving[1], {}).pop(client, None)
                    self.server.persist_clients()
                self.reply(200, {"status": "disconnected"})
                return
            if not self.admin():
                return
            if route == "/stop":
                self.reply(200, {"status": "stopping"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            token = payload.get("project", "")
            if not re.fullmatch(r"[a-f0-9]{64}", token):
                raise ValueError()
            record = live.read_json(live.project_record(token), {})
            if not record.get("latest"):
                self.reply(404, {"error": "Unknown project"})
                return
            with self.server.guard:
                now = time.monotonic()
                if route == "/open":
                    reused = (any(0 <= time.time() - seen < 90 for seen in self.server.clients.get(token, {}).values())
                              or now - self.server.claims.get(token, float('-inf')) < 5)
                    if not reused:
                        self.server.claims[token] = now
                    self.reply(200, {"reused": reused, "version": record["latest"]["version"]})
                elif route == "/release":
                    self.server.claims.pop(token, None)
                    self.reply(200, {"status": "released"})
                else:
                    self.reply(404, {"error": "Unknown operation"})
        except (ValueError, TypeError, AttributeError):
            self.reply(400, {"error": "Invalid request"})


def main():
    # An OS lock is held for the process lifetime, including socket shutdown.
    with live.locked("service"):
        config = live.configuration()
        with Server(config) as server:
            config["port"] = server.server_port
            with live.locked():
                atomic_write_json(live.runtime_dir() / "config.json", config)
            server.serve_forever(poll_interval=0.1)


if __name__ == "__main__":
    main()
