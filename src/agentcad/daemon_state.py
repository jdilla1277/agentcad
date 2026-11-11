"""Private daemon state, with no-follow reads and atomic metadata publication.

Default state lives beneath a checked, user-owned 0700 directory. Explicit
socket/PID overrides retain their paths; operators must secure their parents.
Leaf ownership, type, permissions and link counts are checked in both cases.
"""

from functools import wraps
import json
import os
from pathlib import Path
import secrets
import stat


class DaemonStateError(OSError):
    """An unsafe state path was preserved instead of being used or removed."""


def runtime_directory():
    root = os.environ.get("XDG_RUNTIME_DIR")
    return Path(root) / "agentcad" if root else Path(f"/tmp/agentcad-{os.getuid()}")


def _refuse(path, reason):
    raise DaemonStateError(
        f"Refused unsafe daemon state at {str(path)!r}: {reason}. "
        "Inspect the path and its owner/permissions before retrying; "
        "no unsafe state was removed."
    )


def _check_info(path, info, kind):
    allowed_type = {
        "directory": stat.S_ISDIR,
        "pid": stat.S_ISREG,
        "socket": stat.S_ISSOCK,
        "state": lambda mode: stat.S_ISREG(mode) or stat.S_ISSOCK(mode),
    }[kind]
    if not allowed_type(info.st_mode):
        _refuse(path, f"expected {kind}, found a different file type or symlink")
    if info.st_uid != os.getuid():
        _refuse(path, "path belongs to another user")
    expected_mode = 0o700 if kind == "directory" else 0o600
    if stat.S_IMODE(info.st_mode) != expected_mode:
        _refuse(path, f"permissions must be {expected_mode:04o}")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        _refuse(path, "metadata has multiple hard links")


def check_entry(path, kind):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        _refuse(path, str(exc))
    _check_info(path, info, kind)
    return True


def _check_runtime_parent(path, *, create=False):
    directory = runtime_directory()
    if os.path.abspath(Path(path).parent) != os.path.abspath(directory):
        return  # Explicit override: the operator owns parent-directory security.
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        if not os.path.isabs(xdg) or not check_entry(xdg, "directory"):
            _refuse(xdg, "XDG_RUNTIME_DIR must be an existing private absolute directory")
    if create:
        try:
            os.mkdir(directory, 0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            _refuse(directory, str(exc))
    check_entry(directory, "directory")


def check_paths(socket_path=None, pid_path=None, *, create=False):
    for path, kind in ((socket_path, "socket"), (pid_path, "pid")):
        if path is not None:
            _check_runtime_parent(path, create=create)
            check_entry(path, kind)


def guarded(result_key):
    """Keep lifecycle refusals structured, including during late cleanup."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except DaemonStateError as exc:
                return {
                    result_key: False,
                    "error_kind": "unsafe_daemon_state",
                    "state_preserved": True,
                    "message": str(exc),
                }
        return wrapped
    return decorate


def publish_metadata(path, metadata):
    _check_runtime_parent(path, create=True)
    check_entry(path, "pid")
    path = Path(path)
    temporary = f".agentcad-pid-{secrets.token_hex(16)}.tmp"
    parent_fd = None
    created = False
    try:
        # /tmp itself is a system symlink on macOS. Resolve the parent, then
        # pin that directory; the metadata basename is never followed.
        parent_fd = os.open(os.path.realpath(path.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=parent_fd,
        )
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(metadata, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        check_entry(path, "pid")
        os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        created = False
    except OSError as exc:
        if isinstance(exc, DaemonStateError):
            raise
        _refuse(path, str(exc))
    finally:
        if parent_fd is not None:
            try:
                if created:
                    os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            finally:
                os.close(parent_fd)


def read_metadata(path):
    _check_runtime_parent(path)
    if not check_entry(path, "pid"):
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _refuse(path, str(exc))
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        _check_info(path, os.fstat(handle.fileno()), "pid")
        try:
            return handle.read(4096).strip()
        except UnicodeError:
            return None


def unlink_state(path):
    _check_runtime_parent(path)
    if check_entry(path, "state"):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            _refuse(path, str(exc))
