"""Advisory, cached checks for a newer agentcad release on PyPI."""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from urllib.request import Request, urlopen

from packaging.version import InvalidVersion, Version

from agentcad import __version__
from agentcad.versioning import atomic_write_json


_DEFAULT_URL = "https://pypi.org/pypi/agentcad/json"
_DEFAULT_INTERVAL_S = 24 * 60 * 60
_DEFAULT_NOTICE_INTERVAL_S = 3 * 24 * 60 * 60
_DEFAULT_TIMEOUT_S = 0.75
_NOTICE_ID = "agentcad.update_available"
_RESTART_NOTICE_ID = "agentcad.process_restart_required"


def _cache_dir() -> Path:
    override = os.environ.get("AGENTCAD_UPDATE_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "agentcad"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Caches" / "agentcad"
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA")
        if root:
            return Path(root) / "agentcad" / "Cache"
    return Path.home() / ".cache" / "agentcad"


def _cache_path() -> Path:
    return _cache_dir() / "update-check.json"


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _read_cache() -> dict:
    try:
        payload = json.loads(_cache_path().read_text())
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def _cache_is_fresh(payload: dict) -> bool:
    checked_at = payload.get("checked_at")
    if not isinstance(checked_at, (int, float)):
        return False
    interval = _float_env(
        "AGENTCAD_UPDATE_CHECK_INTERVAL_S", _DEFAULT_INTERVAL_S
    )
    return time.time() - checked_at < interval


def _fetch_latest_version() -> str:
    url = os.environ.get("AGENTCAD_UPDATE_URL") or _DEFAULT_URL
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": f"agentcad/{__version__} update-check",
        },
    )
    timeout = _float_env("AGENTCAD_UPDATE_TIMEOUT_S", _DEFAULT_TIMEOUT_S)
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    latest = payload.get("info", {}).get("version")
    if not isinstance(latest, str) or not latest:
        raise ValueError("PyPI response did not include info.version")
    Version(latest)
    return latest


def _latest_version() -> str | None:
    cached = _read_cache()
    if _cache_is_fresh(cached):
        latest = cached.get("latest_version")
        return latest if isinstance(latest, str) else None

    refreshed = {"checked_at": time.time()}
    for key in ("last_notification_key", "last_notified_at"):
        if key in cached:
            refreshed[key] = cached[key]
    try:
        refreshed["latest_version"] = _fetch_latest_version()
    except Exception:
        # Preserve a previously known version while rate-limiting failures.
        latest = cached.get("latest_version")
        if isinstance(latest, str):
            refreshed["latest_version"] = latest
    try:
        atomic_write_json(_cache_path(), refreshed)
    except OSError:
        pass
    latest = refreshed.get("latest_version")
    return latest if isinstance(latest, str) else None


def _notice_is_due(notification_key: str) -> bool:
    cached = _read_cache()
    if cached.get("last_notification_key") != notification_key:
        return True
    notified_at = cached.get("last_notified_at")
    if not isinstance(notified_at, (int, float)):
        return True
    interval = _float_env(
        "AGENTCAD_UPDATE_NOTICE_INTERVAL_S", _DEFAULT_NOTICE_INTERVAL_S
    )
    return time.time() - notified_at >= interval


def _mark_notified(notification_key: str) -> None:
    cached = _read_cache()
    cached["last_notification_key"] = notification_key
    cached["last_notified_at"] = time.time()
    try:
        atomic_write_json(_cache_path(), cached)
    except OSError:
        pass


def _daemon_supported() -> bool:
    from agentcad.daemon import daemon_supported

    return daemon_supported()


def _installed_distribution_version() -> str | None:
    try:
        return distribution("agentcad").version
    except (PackageNotFoundError, AttributeError):
        return None


def _install_details() -> tuple[str | None, dict | None]:
    try:
        dist = distribution("agentcad")
    except PackageNotFoundError:
        return None, None

    direct_url = dist.read_text("direct_url.json")
    if direct_url:
        try:
            direct = json.loads(direct_url)
        except ValueError:
            direct = {}
        if (
            isinstance(direct, dict)
            and direct.get("dir_info", {}).get("editable")
        ):
            return "editable", None

    installer = (dist.read_text("INSTALLER") or "").strip().lower() or None
    prefix = str(Path(sys.prefix)).lower()
    if "pipx" in prefix:
        return "pipx", {"argv": ["pipx", "upgrade", "agentcad"]}
    if installer == "uv" and ("/uv/" in prefix or "\\uv\\" in prefix):
        return "uv-tool", {"argv": ["uv", "tool", "upgrade", "agentcad"]}
    if installer == "pip":
        return "pip", {
            "argv": [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "agentcad",
            ]
        }
    return installer, None


def available_update_notice() -> dict | None:
    """Return an additive agent notice, or None when current/offline/source."""
    if os.environ.get("AGENTCAD_NO_UPDATE_CHECK"):
        return None
    # A daemon-routed response is replayed through the foreground client,
    # which owns installed-version guidance and notification throttling.
    if os.environ.get("AGENTCAD_DAEMON_CHILD"):
        return None
    if __version__.endswith("+source"):
        return None

    # Long-lived MCP processes can survive an environment upgrade just like
    # the warm daemon can. Detect newer metadata already present on disk before
    # asking PyPI or suggesting another redundant installation.
    installed = _installed_distribution_version()
    try:
        restart_needed = bool(
            installed and Version(installed) > Version(__version__)
        )
    except InvalidVersion:
        restart_needed = False
    if restart_needed:
        notification_key = f"restart:{installed}"
        if not _notice_is_due(notification_key):
            return None
        notice = {
            "id": _RESTART_NOTICE_ID,
            "level": "info",
            "message": (
                f"agentcad {installed} is installed, but this long-lived "
                f"process is still running {__version__}."
            ),
            "running_version": __version__,
            "installed_version": installed,
            "action": {
                "kind": "restart_agent_integration",
                "requires_user_approval": True,
                "instructions": (
                    "Restart the agent's MCP server or host application to "
                    "load the installed agentcad version."
                ),
            },
        }
        _mark_notified(notification_key)
        return notice

    latest = _latest_version()
    if not latest:
        return None
    try:
        if Version(latest) <= Version(__version__):
            return None
    except InvalidVersion:
        return None
    notification_key = f"update:{latest}"
    if not _notice_is_due(notification_key):
        return None

    installer, upgrade = _install_details()
    notice = {
        "id": _NOTICE_ID,
        "level": "info",
        "message": (
            f"agentcad {latest} is available; this process is running "
            f"{__version__}."
        ),
        "installed_version": __version__,
        "available_version": latest,
        "action": {
            "kind": "upgrade",
            "requires_user_approval": True,
        },
    }
    if _daemon_supported():
        notice["after_upgrade"] = {
            "argv": ["agentcad", "daemon", "restart"],
            "reason": "Load the newly installed code into the warm daemon.",
        }
    if installer:
        notice["installer"] = installer
    if upgrade is not None:
        notice["action"].update(upgrade)
    else:
        notice["action"]["instructions"] = (
            "Use the same environment manager that installed agentcad."
        )
    _mark_notified(notification_key)
    return notice
