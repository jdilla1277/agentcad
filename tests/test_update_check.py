import json

import pytest

from agentcad import response_metadata
from agentcad import update_check
from agentcad.cli import cli


@pytest.fixture(autouse=True)
def _isolated_update_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCAD_UPDATE_CACHE_DIR", str(tmp_path))


class _Distribution:
    def __init__(self, installer="pip", direct_url=None, version="1.2.3"):
        self.installer = installer
        self.direct_url = direct_url
        self.version = version

    def read_text(self, name):
        if name == "INSTALLER":
            return self.installer
        if name == "direct_url.json":
            return self.direct_url
        return None


def test_newer_release_becomes_advisory_notice(monkeypatch):
    monkeypatch.delenv("AGENTCAD_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update_check, "__version__", "1.2.3")
    monkeypatch.setattr(update_check, "_latest_version", lambda: "1.3.0")
    monkeypatch.setattr(
        update_check, "distribution", lambda _name: _Distribution("pip")
    )
    monkeypatch.setattr(update_check, "_daemon_supported", lambda: True)

    notice = update_check.available_update_notice()

    assert notice["installed_version"] == "1.2.3"
    assert notice["available_version"] == "1.3.0"
    assert notice["action"]["requires_user_approval"] is True
    assert notice["action"]["argv"][1:] == [
        "-m", "pip", "install", "--upgrade", "agentcad",
    ]
    assert notice["after_upgrade"]["argv"] == [
        "agentcad", "daemon", "restart",
    ]


def test_long_lived_process_reports_restart_after_environment_upgrade(monkeypatch):
    monkeypatch.delenv("AGENTCAD_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update_check, "__version__", "1.2.3")
    monkeypatch.setattr(
        update_check,
        "distribution",
        lambda _name: _Distribution("pip", version="1.3.0"),
    )
    monkeypatch.setattr(
        update_check,
        "_latest_version",
        lambda: (_ for _ in ()).throw(AssertionError("PyPI is not needed")),
    )

    notice = update_check.available_update_notice()

    assert notice["id"] == "agentcad.process_restart_required"
    assert notice["running_version"] == "1.2.3"
    assert notice["installed_version"] == "1.3.0"
    assert notice["action"]["kind"] == "restart_agent_integration"
    assert "upgrade" not in notice["action"]


def test_daemon_child_leaves_notice_ownership_to_foreground_client(monkeypatch):
    monkeypatch.delenv("AGENTCAD_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AGENTCAD_DAEMON_CHILD", "1")
    monkeypatch.setattr(
        update_check,
        "_latest_version",
        lambda: (_ for _ in ()).throw(AssertionError("child must not check")),
    )

    assert update_check.available_update_notice() is None


def test_current_and_source_versions_do_not_emit_notices(monkeypatch):
    monkeypatch.delenv("AGENTCAD_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update_check, "__version__", "1.2.3")
    monkeypatch.setattr(update_check, "_latest_version", lambda: "1.2.3")
    assert update_check.available_update_notice() is None

    monkeypatch.setattr(update_check, "__version__", "0.0.0+source")
    monkeypatch.setattr(
        update_check,
        "_latest_version",
        lambda: (_ for _ in ()).throw(AssertionError("must not check")),
    )
    assert update_check.available_update_notice() is None


def test_editable_install_does_not_suggest_overwriting_with_pip(monkeypatch):
    monkeypatch.delenv("AGENTCAD_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update_check, "__version__", "1.0.0")
    monkeypatch.setattr(update_check, "_latest_version", lambda: "2.0.0")
    monkeypatch.setattr(
        update_check,
        "distribution",
        lambda _name: _Distribution(
            "pip", json.dumps({"dir_info": {"editable": True}}), version="1.0.0"
        ),
    )

    notice = update_check.available_update_notice()

    assert notice["installer"] == "editable"
    assert "argv" not in notice["action"]
    assert "same environment manager" in notice["action"]["instructions"]


def test_same_release_notice_is_throttled_for_three_days(monkeypatch):
    monkeypatch.delenv("AGENTCAD_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update_check, "__version__", "1.0.0")
    monkeypatch.setattr(update_check, "_latest_version", lambda: "2.0.0")
    monkeypatch.setattr(
        update_check,
        "distribution",
        lambda _name: _Distribution("pip", version="1.0.0"),
    )
    monkeypatch.setattr(update_check, "_daemon_supported", lambda: True)

    assert update_check.available_update_notice() is not None
    assert update_check.available_update_notice() is None


def test_update_notice_omits_daemon_restart_on_unsupported_platform(monkeypatch):
    monkeypatch.delenv("AGENTCAD_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update_check, "__version__", "1.0.0")
    monkeypatch.setattr(update_check, "_latest_version", lambda: "2.0.0")
    monkeypatch.setattr(
        update_check,
        "distribution",
        lambda _name: _Distribution("pip", version="1.0.0"),
    )
    monkeypatch.setattr(update_check, "_daemon_supported", lambda: False)

    notice = update_check.available_update_notice()

    assert "after_upgrade" not in notice


def test_response_augmentation_adds_and_deduplicates_notice(monkeypatch):
    notice = {
        "id": "agentcad.update_available",
        "level": "info",
        "message": "new version",
    }
    monkeypatch.setattr(
        response_metadata, "available_update_notice", lambda: notice
    )
    original = json.dumps({
        "command": "context",
        "status": "success",
        "_meta": {"notices": [{"id": notice["id"], "message": "old"}]},
    })

    augmented = json.loads(response_metadata.augment_json_response(original))

    assert augmented["status"] == "success"
    assert augmented["_meta"]["notices"] == [notice]


def test_response_augmentation_ignores_checker_failures(monkeypatch):
    monkeypatch.setattr(
        response_metadata,
        "available_update_notice",
        lambda: (_ for _ in ()).throw(RuntimeError("unexpected checker bug")),
    )
    original = json.dumps({"command": "context", "status": "success"})

    assert response_metadata.augment_json_response(original) == original


def test_foreground_response_removes_stale_daemon_update_notice(monkeypatch):
    monkeypatch.setattr(
        response_metadata, "available_update_notice", lambda: None
    )
    original = json.dumps({
        "command": "run",
        "status": "success",
        "_meta": {
            "notices": [{
                "id": "agentcad.update_available",
                "installed_version": "1.0.0",
                "available_version": "2.0.0",
            }],
            "other": "preserved",
        },
    })

    augmented = json.loads(response_metadata.augment_json_response(original))

    assert augmented["_meta"] == {"other": "preserved"}


def test_cli_responses_surface_update_notice(
    runner, isolated_dir, monkeypatch
):
    notice = {
        "id": "agentcad.update_available",
        "level": "info",
        "message": "new version",
        "action": {"requires_user_approval": True},
    }
    monkeypatch.setattr(
        response_metadata, "available_update_notice", lambda: notice
    )

    result = runner.invoke(cli, ["init", "--name", "updates-test"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["_meta"]["notices"] == [notice]


def test_failed_refresh_is_cached_without_breaking_commands(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("AGENTCAD_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("AGENTCAD_UPDATE_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        update_check,
        "_fetch_latest_version",
        lambda: (_ for _ in ()).throw(OSError("offline")),
    )

    assert update_check._latest_version() is None

    cached = json.loads((tmp_path / "update-check.json").read_text())
    assert isinstance(cached["checked_at"], float)
    assert "latest_version" not in cached


def test_refresh_preserves_notice_throttle_state(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTCAD_NO_UPDATE_CHECK", raising=False)
    cache_path = tmp_path / "update-check.json"
    cache_path.write_text(json.dumps({
        "checked_at": 0,
        "latest_version": "2.0.0",
        "last_notification_key": "update:2.0.0",
        "last_notified_at": 123.0,
    }))
    monkeypatch.setattr(
        update_check, "_fetch_latest_version", lambda: "2.0.0"
    )

    assert update_check._latest_version() == "2.0.0"

    refreshed = json.loads(cache_path.read_text())
    assert refreshed["last_notification_key"] == "update:2.0.0"
    assert refreshed["last_notified_at"] == 123.0
