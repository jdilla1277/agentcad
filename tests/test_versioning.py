import json
from concurrent.futures import ThreadPoolExecutor

import pytest


def test_concurrent_reservations_get_unique_numbers_and_directories(tmp_path):
    from agentcad.versioning import reserve_version

    (tmp_path / "agentcad.json").write_text(json.dumps({
        "name": "concurrent",
        "versions": [],
    }))

    def reserve(index):
        return reserve_version(tmp_path, f"part-{index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(reserve, range(8)))

    assert sorted(r.number for r in reservations) == list(range(1, 9))
    assert len({r.path for r in reservations}) == 8
    assert all(r.path.is_dir() for r in reservations)


def test_atomic_json_failure_preserves_previous_document(tmp_path):
    from agentcad.versioning import atomic_write_json

    target = tmp_path / "meta.json"
    atomic_write_json(target, {"status": "success", "version": 1})

    with pytest.raises(TypeError):
        atomic_write_json(target, {"not_json": object()})

    assert json.loads(target.read_text()) == {
        "status": "success",
        "version": 1,
    }
    assert list(tmp_path.glob(".meta.json.*.tmp")) == []


def test_concurrent_core_commits_merge_manifest_entries(tmp_path):
    from agentcad.versioning import commit_version, reserve_version

    manifest_path = tmp_path / "agentcad.json"
    manifest_path.write_text(json.dumps({
        "name": "concurrent",
        "versions": [],
    }))
    reservations = [
        reserve_version(tmp_path, f"part-{index}")
        for index in range(8)
    ]

    def commit(reservation):
        meta = {
            "status": "success",
            "version": reservation.number,
            "label": reservation.label,
        }
        entry = {
            "version": reservation.number,
            "label": reservation.label,
            "status": "success",
            "path": f"{reservation.dir_name}/",
        }
        commit_version(
            reservation,
            meta,
            entry,
            advance_current=True,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(commit, reservations))

    manifest = json.loads(manifest_path.read_text())
    assert [entry["version"] for entry in manifest["versions"]] == list(range(1, 9))
    assert len({entry["path"] for entry in manifest["versions"]}) == 8
    assert all(
        json.loads((reservation.path / "meta.json").read_text())["status"]
        == "success"
        for reservation in reservations
    )
