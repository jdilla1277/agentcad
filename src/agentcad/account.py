"""Browser-assisted CLI installation approval. Credentials never enter command output."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_SITE = "https://agentcad.dev"
CREDENTIAL = re.compile(r"^acli_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.[A-Za-z0-9_-]{43}$")


class AccountError(Exception):
    def __init__(self, reason, message):
        self.reason, self.message = reason, message
        super().__init__(message)


def origin(value):
    try:
        parsed = urlsplit(value)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (parsed.hostname and parsed.port != 0 and not parsed.username and not parsed.password
                and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
                and value.isprintable() and value.strip() == value
                and (parsed.scheme == "https" or (local and parsed.scheme == "http"))):
            return value.rstrip("/")
    except (TypeError, ValueError, AttributeError):
        pass
    raise AccountError("invalid_origin", "Use an HTTPS origin; HTTP is allowed only for loopback development.")


class AccountState:
    """One origin-bound profile in a private file, independent of CAD projects."""
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("AGENTCAD_ACCOUNT_STATE") or Path.home() / ".config" / "agentcad" / "account.json").expanduser().absolute()

    def load(self):
        try:
            descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError:
            return {}
        except OSError:
            raise AccountError("unsafe_state", "Account state must be a private regular file, not a symlink.") from None
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or (hasattr(os, "getuid") and info.st_uid != os.getuid())
                    or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 16384):
                raise AccountError("unsafe_state", "Account state must be owned by you and readable only by you (chmod 600).")
            with os.fdopen(descriptor, "r", encoding="utf-8") as file:
                descriptor = None
                value = json.load(file)
                if not isinstance(value, dict):
                    raise ValueError()
                return value
        except (ValueError, UnicodeError):
            raise AccountError("invalid_state", "Account state could not be read. Preserve it for recovery or choose a new AGENTCAD_ACCOUNT_STATE path.") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @contextmanager
    def lock(self):
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        descriptor = None
        try:
            descriptor = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077
                    or (hasattr(os, "getuid") and info.st_uid != os.getuid())):
                raise AccountError("unsafe_state", "Account lock must be a private file owned by you.")
            try:
                if os.name == "nt":
                    import msvcrt
                    os.write(descriptor, b"0"); os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise AccountError("account_busy", "Another account command is using this profile. Let it finish and retry.") from None
            yield
        except OSError:
            raise AccountError("unsafe_state", "Could not open the private account lock. Check your state path.") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def save(self, value):
        self.load()  # Never replace an unsafe existing destination.
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, prefix=".account-", delete=False) as file:
                temporary = file.name
                os.chmod(temporary, 0o600)
                json.dump(value, file)
                file.flush(); os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise AccountError("state_write_failed", "Could not save private account state. Keep the existing profile and retry.") from None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


class AccountClient:
    def __init__(self, state=None, *, site=None, transport=None):
        self.state = state or AccountState()
        self.saved = self.state.load()
        self.site = origin(site or os.environ.get("AGENTCAD_ACCOUNT_URL") or self.saved.get("site") or DEFAULT_SITE)
        if self.saved.get("site") and self.saved["site"] != self.site:
            raise AccountError("profile_origin_mismatch", "This profile belongs to another account service. Use a separate AGENTCAD_ACCOUNT_STATE path.")
        try:
            import httpx
        except ImportError:
            raise AccountError("missing_dependency", "Install pip install 'agentcad[cloud]' to connect your account.") from None
        self.httpx = httpx
        self.http = httpx.Client(base_url=self.site, transport=transport, timeout=15, follow_redirects=False)

    def __enter__(self):
        self.lock = self.state.lock()
        try:
            self.lock.__enter__()
        except Exception:
            self.http.close()
            raise
        try:
            self.saved = self.state.load()
            self._validate_state()
            if self.saved.get("site") and self.saved["site"] != self.site:
                raise AccountError("profile_origin_mismatch", "This profile belongs to another account service. Use a separate AGENTCAD_ACCOUNT_STATE path.")
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        try:
            self.http.close()
        finally:
            self.lock.__exit__(*args)

    def _validate_state(self):
        pending = self.saved.get("pending")
        if pending is not None:
            valid = isinstance(pending, dict)
            if valid:
                for key in ("id", "installation_id"):
                    try:
                        value = uuid.UUID(pending.get(key, ""))
                        valid = valid and value.version == 4 and str(value) == pending[key]
                    except (ValueError, TypeError, AttributeError):
                        valid = False
                valid = valid and isinstance(pending.get("secret"), str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{43}", pending["secret"]))
                valid = valid and isinstance(pending.get("name"), str) and 1 <= len(pending["name"]) <= 80
            if not valid:
                raise AccountError("invalid_state", "The pending account state is invalid. Preserve it for recovery or choose a new profile.")

    def _request(self, method, path, *, credential=None, **kwargs):
        headers = {"Authorization": "Bearer " + credential} if credential else {}
        try:
            with self.http.stream(method, path, headers=headers, **kwargs) as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 16384:
                        raise AccountError("invalid_response", "The account service returned an oversized response.")
                if response.status_code in {401, 403, 404, 410}:
                    message = ("This CLI installation is revoked, disabled, or unavailable. Clear its local state with agentcad account disconnect --forget, then connect again."
                               if credential else "This approval request is expired, canceled, or unavailable. Clear pending state with agentcad account disconnect --forget, then connect again.")
                    raise AccountError("not_connected", message)
                if response.status_code == 429:
                    raise AccountError("rate_limited", "Too many connection requests. Keep the existing request and try later.")
                if not 200 <= response.status_code < 300:
                    raise AccountError("account_unavailable", f"The account service returned HTTP {response.status_code}. Your saved account state is preserved; retry later.")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError()
                return result
        except self.httpx.HTTPError:
            raise AccountError("request_failed", "The response was not received. Your saved account state is preserved; retry the same command.") from None
        except (ValueError, UnicodeError):
            raise AccountError("invalid_response", "The account service returned an invalid response. Retry without deleting your saved state.") from None

    def _output(self, connection, **fields):
        actions = ["agentcad cloud projects list"] if connection == "connected" else ["agentcad account connect" if connection == "not_connected" else "agentcad account status"]
        return {"command": "account", "status": "success", "connection": connection, **fields,
                "next_actions": actions, "more_at": "agentcad docs account"}

    def connect(self, name=None):
        if self.saved.get("credential"):
            return self.status()
        if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise AccountError("invalid_name", "Use a nonempty installation name of at most 80 characters without control characters.")
        if not self.saved.get("pending"):
            self.saved = {"site": self.site, "pending": {"id": str(uuid.uuid4()), "installation_id": str(uuid.uuid4()),
                "secret": secrets.token_urlsafe(32), "name": name or socket.gethostname()[:80] or "AgentCAD CLI"}}
            self.state.save(self.saved)  # Save proof BEFORE the network request, including lost-response recovery.
        pending = self.saved["pending"]
        self._request("POST", "/api/account/cli/requests", json={
            "id": pending["id"], "installation_id": pending["installation_id"], "name": pending["name"],
            "challenge": hashlib.sha256(pending["secret"].encode()).hexdigest()})
        return self._output("approval_pending", approval_url=self.site + "/account/connect?cli_request=" + pending["id"],
                            message="Open approval_url, sign up or sign in, then explicitly approve this CLI installation. Run account status afterward.",
                            credential_storage="private_file")

    def status(self):
        credential = self.saved.get("credential")
        if credential:
            if not isinstance(credential, str) or not CREDENTIAL.fullmatch(credential):
                raise AccountError("invalid_state", "The saved credential is invalid. Choose a new account state path or disconnect.")
            result = self._request("GET", "/api/account/cli/me", credential=credential)
        elif self.saved.get("pending"):
            pending = self.saved["pending"]
            result = self._request("POST", "/api/account/cli/exchange", json={"id": pending["id"], "secret": pending["secret"]})
            if result.get("state") == "approval_pending":
                return self._output("approval_pending", approval_url=self.site + "/account/connect?cli_request=" + pending["id"])
            if result.get("state") != "connected":
                raise AccountError("invalid_response", "The account service did not confirm connection.")
            workspace = result.get("workspace_id")
            if not isinstance(workspace, str) or not re.fullmatch(r"wsp_[0-9a-f]{24}", workspace):
                raise AccountError("invalid_response", "The account service did not return a valid workspace. Your pending connection is preserved.")
            api_url = origin(result.get("api_url"))
            self.saved = {"site": self.site, "api_url": api_url, "credential": f"acli_{pending['id']}.{pending['secret']}"}
            self.state.save(self.saved)
        else:
            return self._output("not_connected", message="Run agentcad account connect to sign up or connect an existing account.")
        workspace = result.get("workspace_id")
        if result.get("state") != "connected" or not isinstance(workspace, str) or not re.fullmatch(r"wsp_[0-9a-f]{24}", workspace):
            raise AccountError("invalid_response", "The account service did not return a valid workspace.")
        return self._output("connected", workspace_id=workspace, credential_storage="private_file")

    def disconnect(self, *, forget=False):
        credential = self.saved.get("credential")
        if not credential and self.saved.get("pending"):
            pending = self.saved["pending"]
            credential = f"acli_{pending['id']}.{pending['secret']}"
        if credential and not forget:
            try:
                self._request("POST", "/api/account/cli/disconnect", credential=credential, json={})
            except AccountError as error:
                if error.reason != "not_connected":
                    raise
        self.saved = {}
        self.state.save({})
        return self._output("not_connected", message="Local connection cleared." if forget else "Installation disconnected.",
                            server_revoked=bool(credential and not forget))
