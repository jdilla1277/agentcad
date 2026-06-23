import json

from agentcad.cli import cli


def _write_project(root, *, current="assembly", parts=None, version=1, label="assembly"):
    version_dir = root / f"v{version}_{label}"
    version_dir.mkdir()
    (version_dir / "output.glb").write_bytes(b"glb-test")
    (version_dir / "meta.json").write_text(json.dumps({
        "version": version,
        "label": label,
        "status": "success",
        "script": f"v{version}_{label}/script.py",
        "outputs": {"step": f"v{version}_{label}/output.step"},
        "viewer": f"v{version}_{label}/viewer.html",
        "viewer_glb": f"v{version}_{label}/output.glb",
        "parts": parts or [],
    }))
    (root / "agentcad.json").write_text(json.dumps({
        "name": "parts-test",
        "current": current,
        "versions": [{
            "version": version,
            "label": label,
            "status": "success",
            "path": f"v{version}_{label}/",
        }],
    }))
    return version_dir


def test_parts_list_reads_version_snapshot(runner, isolated_dir):
    _write_project(isolated_dir, parts=[
        {
            "id": "base_plate",
            "id_source": "explicit",
            "name": "Base Plate",
            "color": "gray",
            "metrics": {"volume": 100.0},
            "preview": "v1_assembly/parts/base_plate.png",
        },
        {
            "id": "axle_shaft",
            "id_source": "name",
            "name": "Axle Shaft",
            "metrics": {"volume": 20.0},
        },
    ])

    result = runner.invoke(cli, ["parts", "list", "v1"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)

    assert data["command"] == "parts list"
    assert data["status"] == "success"
    assert data["version"] == 1
    assert data["label"] == "assembly"
    assert data["viewer"] == "v1_assembly/viewer.html"
    assert data["viewer_glb"] == "v1_assembly/output.glb"
    assert data["script"] == "v1_assembly/script.py"
    assert data["part_count"] == 2
    assert [p["id"] for p in data["parts"]] == ["base_plate", "axle_shaft"]


def test_parts_show_returns_one_part_by_id(runner, isolated_dir):
    _write_project(isolated_dir, parts=[
        {"id": "base_plate", "id_source": "explicit", "name": "Base Plate"},
        {"id": "part_1", "id_source": "generated"},
    ])

    result = runner.invoke(cli, ["parts", "show", "assembly", "base_plate"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)

    assert data["command"] == "parts show"
    assert data["viewer_glb"] == "v1_assembly/output.glb"
    assert data["part"]["id"] == "base_plate"
    assert data["part"]["name"] == "Base Plate"


def test_parts_current_ref_uses_manifest_current(runner, isolated_dir):
    _write_project(isolated_dir, parts=[
        {"id": "base_plate", "id_source": "explicit", "name": "Base Plate"},
    ])

    result = runner.invoke(cli, ["parts", "list", "current"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)

    assert data["label"] == "assembly"
    assert data["parts"][0]["id"] == "base_plate"


def test_parts_list_legacy_numeric_ids_are_version_local(runner, isolated_dir):
    _write_project(isolated_dir, parts=[
        {"id": 0, "name": "legacy_deck", "metrics": {"volume": 10}},
        {"id": 1, "metrics": {"volume": 5}},
    ])

    result = runner.invoke(cli, ["parts", "list", "1"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)

    assert data["parts"][0]["id"] == "part_0"
    assert data["parts"][0]["legacy_id"] == 0
    assert data["parts"][0]["id_source"] == "legacy"
    assert data["parts"][0]["version_local"] is True
    assert data["parts"][1]["id"] == "part_1"


def test_parts_show_accepts_legacy_numeric_alias(runner, isolated_dir):
    _write_project(isolated_dir, parts=[
        {"id": 0, "name": "legacy_deck"},
    ])

    result = runner.invoke(cli, ["parts", "show", "1", "0"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)

    assert data["part"]["id"] == "part_0"
    assert data["part"]["legacy_id"] == 0


def test_parts_nested_help_documents_refs_and_legacy_ids(runner):
    list_result = runner.invoke(cli, ["parts", "list", "--help"])
    show_result = runner.invoke(cli, ["parts", "show", "--help"])
    view_result = runner.invoke(cli, ["parts", "view", "--help"])

    assert list_result.exit_code == 0
    assert show_result.exit_code == 0
    assert view_result.exit_code == 0
    assert "current" in list_result.output
    assert "latest" in list_result.output
    assert "part_count" in list_result.output
    assert "legacy_id" in list_result.output
    assert "PART_ID" in show_result.output
    assert "numeric alias" in show_result.output
    assert "--isolate" in view_result.output
    assert "--ghost-rest" in view_result.output
    assert "--focus" in view_result.output


def test_parts_list_missing_version_errors(runner, isolated_dir):
    _write_project(isolated_dir)

    result = runner.invoke(cli, ["parts", "list", "missing"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)

    assert data["status"] == "error"
    assert "not found" in data["message"]


def test_parts_show_missing_part_errors_with_available_ids(runner, isolated_dir):
    _write_project(isolated_dir, parts=[
        {"id": "base_plate", "id_source": "explicit", "name": "Base Plate"},
    ])

    result = runner.invoke(cli, ["parts", "show", "1", "wheel"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)

    assert data["status"] == "error"
    assert data["available_ids"] == ["base_plate"]


def test_parts_view_writes_reproducible_review_viewer(runner, isolated_dir):
    _write_project(isolated_dir, parts=[
        {
            "id": "base_plate",
            "id_source": "explicit",
            "name": "Base Plate",
            "color": "gray",
        },
        {
            "id": "axle_shaft",
            "id_source": "name",
            "name": "Axle Shaft",
            "color": "steelblue",
        },
    ])

    result = runner.invoke(cli, [
        "parts", "view", "assembly",
        "--isolate", "axle_shaft",
        "--hide", "base_plate",
        "--ghost-rest",
        "--focus", "axle_shaft",
        "--no-open",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)

    assert data["command"] == "parts view"
    assert data["status"] == "success"
    assert data["viewer_glb"] == "v1_assembly/output.glb"
    assert data["review_viewer"].startswith("v1_assembly/parts_review_")
    assert data["part_review"] == {
        "mode": "part-review",
        "source": "agentcad parts view",
        "version": 1,
        "label": "assembly",
        "selected": "axle_shaft",
        "focus": "axle_shaft",
        "isolated": ["axle_shaft"],
        "hidden": ["base_plate"],
        "ghost_rest": True,
    }

    html = (isolated_dir / data["review_viewer"]).read_text()
    assert "const PART_REVIEW = " in html
    assert '"mode": "part-review"' in html
    assert '"isolated": ["axle_shaft"]' in html
    assert "Part controls" in html
    assert "Ghost rest" in html


def test_parts_view_rejects_unknown_part_ids(runner, isolated_dir):
    _write_project(isolated_dir, parts=[
        {"id": "base_plate", "id_source": "explicit", "name": "Base Plate"},
    ])

    result = runner.invoke(cli, [
        "parts", "view", "1", "--isolate", "wheel", "--no-open",
    ])
    assert result.exit_code == 1
    data = json.loads(result.stdout)

    assert data["status"] == "error"
    assert data["missing_ids"] == ["wheel"]
    assert data["available_ids"] == ["base_plate"]


def test_parts_view_requires_viewer_glb_file(runner, isolated_dir):
    version_dir = _write_project(isolated_dir, parts=[
        {"id": "base_plate", "id_source": "explicit", "name": "Base Plate"},
    ])
    (version_dir / "output.glb").unlink()

    result = runner.invoke(cli, [
        "parts", "view", "1", "--isolate", "base_plate", "--no-open",
    ])
    assert result.exit_code == 1
    data = json.loads(result.stdout)

    assert data["status"] == "error"
    assert "viewer_glb not found" in data["message"]


def test_parts_no_manifest_errors(runner, isolated_dir):
    result = runner.invoke(cli, ["parts", "list", "1"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)

    assert data["command"] == "parts"
    assert data["status"] == "error"
    assert "agentcad.json not found" in data["message"]
