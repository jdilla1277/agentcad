"""`agentcad render` against STEPs produced by the build123d runtime.

The render pipeline is OCP-based and runtime-agnostic — it reads
a STEP file and rasterizes via VTK. This file verifies that a STEP
written by build123d's export path is readable by the render code.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentcad.cli import cli


B3D_BOX = "from build123d import Box\nshow_object(Box(20, 30, 10))\n"


def _setup_step(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
    (isolated_dir / "s.py").write_text(B3D_BOX)
    runner.invoke(cli, ["run", "s.py", "--output", "box", "--no-preview"])
    return isolated_dir / "v1_box" / "output.step"


def test_render_iso(runner, isolated_dir):
    step = _setup_step(runner, isolated_dir)
    r = runner.invoke(cli, ["render", str(step), "--view", "iso"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    assert parsed["status"] == "success"
    out = Path(parsed["renders"]["iso"])
    assert out.exists() and out.stat().st_size > 0


def test_render_all_named_views(runner, isolated_dir):
    step = _setup_step(runner, isolated_dir)
    r = runner.invoke(cli, ["render", str(step), "--view", "all"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    # 'all' expands to front/right/top/iso per the docs
    for view in ("front", "right", "top", "iso"):
        assert view in parsed["renders"]


def test_render_custom_angle(runner, isolated_dir):
    step = _setup_step(runner, isolated_dir)
    r = runner.invoke(cli, ["render", str(step), "--view", "45,30"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    # Custom angles are keyed by the integer pair
    assert any("45" in k and "30" in k for k in parsed["renders"])


def test_render_zoom(runner, isolated_dir):
    step = _setup_step(runner, isolated_dir)
    r = runner.invoke(cli, ["render", str(step), "--view", "iso", "--zoom", "1.5"])
    assert r.exit_code == 0, r.output


def test_render_missing_step(runner, isolated_dir):
    r = runner.invoke(cli, ["render", "absent.step", "--view", "iso"])
    parsed = json.loads(r.stdout)
    assert r.exit_code == 1
    assert parsed["status"] == "error"


# --- --highlight-faces ---

B3D_FILLETED = (
    "from build123d import *\n"
    "part = Box(40, 30, 10) - Cylinder(5, 10)\n"
    "part = fillet(part.edges().filter_by(Axis.Z).group_by(Axis.X)[0], 0.4)\n"
    "show_object(part)\n"
)


def _setup_filleted(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
    (isolated_dir / "f.py").write_text(B3D_FILLETED)
    r = runner.invoke(cli, ["run", "f.py", "--output", "fil", "--no-preview"])
    assert r.exit_code == 0, r.output
    return isolated_dir / "v1_fil" / "output.step"


def _face_ids(runner, step):
    """(+z face id, -z face id, [small fillet face ids]) from inspect."""
    r = runner.invoke(cli, ["inspect", str(step), "--summary"])
    clusters = json.loads(r.stdout)["summary"]["face_clusters"]

    def ids(kind, axis, **extra):
        return [
            face_id for c in clusters
            if c["kind"] == kind and c.get("axis") == axis
            and all(c.get(k) == v for k, v in extra.items())
            for face_id in c["ids"]
        ]

    [top] = ids("planar", "+z")
    [bottom] = ids("planar", "-z")
    fillets = ids("cylindrical", "+z", radius=0.4)
    assert len(fillets) == 2
    return top, bottom, fillets


def test_highlight_faces_labels_colors_and_reports(runner, isolated_dir):
    from PIL import Image

    step = _setup_filleted(runner, isolated_dir)
    top, bottom, fillets = _face_ids(runner, step)
    meta_path = step.parent / "meta.json"
    meta_before = meta_path.read_bytes()

    r = runner.invoke(cli, [
        "render", str(step), "--view", "top,iso",
        "--highlight-faces", f"{top},{bottom},{fillets[0]}",
    ])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    assert parsed["status"] == "success"
    assert parsed["highlight"] == "faces"
    for path in parsed["renders"].values():
        assert Path(path).parent == step.parent / "renders" / "faces"
        assert Path(path).is_file()
    assert meta_path.read_bytes() == meta_before

    report = {entry["id"]: entry for entry in parsed["highlighted_faces"]}
    assert [entry["id"] for entry in parsed["highlighted_faces"]] == [
        top, bottom, fillets[0],
    ]
    assert report[top]["labels"] == {"top": "labeled", "iso": "labeled"}
    assert report[bottom]["labels"] == {"top": "hidden", "iso": "hidden"}
    assert report[fillets[0]]["labels"]["top"] in {"hidden", "too_small"}
    assert "labeled" not in report[fillets[0]]["labels"].values()
    assert report[top]["color"] != report[bottom]["color"]
    assert str(bottom) in parsed["hint"] and "--view" in parsed["hint"]

    # The highlighted top face keeps a saturated color (not washed out to
    # white by specular light), and a white label box was drawn on it.
    with Image.open(parsed["renders"]["top"]) as image:
        image = image.convert("RGB")
        expected = tuple(int(report[top]["color"][i:i + 2], 16) for i in (1, 3, 5))
        r_, g_, b_ = image.getpixel((400, 120))
        assert r_ > 150 and g_ < 100 and b_ < 120, (r_, g_, b_, expected)
        assert (255, 255, 255) in {color for _count, color in image.getcolors(1 << 20)}


def test_highlight_faces_too_small_when_label_cannot_fit(runner, isolated_dir):
    step = _setup_filleted(runner, isolated_dir)
    top, _bottom, _fillets = _face_ids(runner, step)
    r = runner.invoke(cli, [
        "render", str(step), "--view", "front", "--size", "40x30",
        "--highlight-faces", str(top),
    ])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    # Seen edge-on from the front at a tiny size: no room for a label.
    assert parsed["highlighted_faces"][0]["labels"]["front"] in {"too_small", "hidden"}
    r = runner.invoke(cli, [
        "render", str(step), "--view", "iso", "--size", "40x30",
        "--highlight-faces", str(top),
    ])
    parsed = json.loads(r.stdout)
    assert parsed["highlighted_faces"][0]["labels"] == {"iso": "too_small"}
    assert "too small" in parsed["hint"]


def test_highlight_faces_out_of_range(runner, isolated_dir):
    step = _setup_filleted(runner, isolated_dir)
    r = runner.invoke(cli, [
        "render", str(step), "--view", "iso", "--highlight-faces", "1,99",
    ])
    assert r.exit_code == 1
    parsed = json.loads(r.stdout)
    assert parsed["status"] == "error"
    assert "99" in parsed["message"] and "faces (IDs 1-" in parsed["message"]
    assert parsed["next_actions"] == [f"agentcad inspect {step} --ids"]
    assert not (step.parent / "renders" / "faces").exists()


def test_highlight_faces_rejects_bad_syntax_and_validation_combo(runner, isolated_dir):
    step = _setup_filleted(runner, isolated_dir)
    for value in ("0", "1,,2", "a", "-3", ""):
        r = runner.invoke(cli, ["render", str(step), "--view", "iso", "--highlight-faces", value])
        assert r.exit_code == 1
        parsed = json.loads(r.stdout)
        assert "--highlight-faces" in parsed["message"]
        assert parsed["next_actions"] == ["agentcad render --help"]
    r = runner.invoke(cli, [
        "render", str(step), "--view", "iso", "--highlight-faces", "1",
        "--highlight", "validation",
    ])
    assert r.exit_code == 1
    parsed = json.loads(r.stdout)
    assert "cannot be combined" in parsed["message"]
    assert parsed["next_actions"] == ["agentcad render --help"]


def test_highlight_faces_forwarded_to_daemon(runner, isolated_dir, monkeypatch):
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    requests = []

    def _capture(request, **_kwargs):
        requests.append(request)
        return {"type": "result", "exit_code": 0, "output": json.dumps({
            "command": "render", "status": "success", "renders": {},
        })}

    monkeypatch.setattr("agentcad.daemon.send_request", _capture)
    (isolated_dir / "any.step").write_text("daemon reads this file")
    r = runner.invoke(cli, [
        "render", "any.step", "--view", "iso", "--highlight-faces", " 7, 3,7",
    ])
    assert r.exit_code == 0, r.output
    assert requests[0]["argv"] == [
        "render", "any.step", "--view", "iso", "--highlight-faces", "7,3",
    ]


def test_highlight_faces_kept_in_missing_step_recovery(runner, isolated_dir):
    import shlex

    step = _setup_filleted(runner, isolated_dir)
    r = runner.invoke(cli, [
        "render", "missing.step", "--view", "iso", "--highlight-faces", "2,3",
        "--no-daemon",
    ])
    assert r.exit_code == 1
    argv = shlex.split(json.loads(r.stdout)["next_actions"][0])
    assert argv[2] == str(step.resolve()) or Path(argv[2]).resolve() == step.resolve()
    assert "--highlight-faces=2,3" in argv


def test_highlight_faces_tells_out_of_frame_from_hidden(runner, isolated_dir):
    step = _setup_filleted(runner, isolated_dir)
    top, bottom, fillets = _face_ids(runner, step)
    # A close-up on the middle of the plate: the corner fillets fall outside
    # the image, while the bottom face is in frame but behind the top face.
    r = runner.invoke(cli, [
        "render", str(step), "--view", "top", "--zoom", "3",
        "--highlight-faces", f"{top},{bottom},{fillets[0]}",
    ])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    report = {entry["id"]: entry["labels"]["top"] for entry in parsed["highlighted_faces"]}
    assert report == {top: "labeled", bottom: "hidden", fillets[0]: "out_of_frame"}
    hint = parsed["hint"]
    assert f"Face(s) {fillets[0]} out of frame" in hint
    assert "--no-fit" in hint and "--focus -19.855,-14.855,0" in hint
    assert f"Face(s) {bottom} hidden" in hint and "--view" in hint
    colors = [entry["color"] for entry in parsed["highlighted_faces"]]
    assert len(set(colors)) == len(colors)

    # Following the hint frames the fillet: it is no longer out of frame.
    r = runner.invoke(cli, [
        "render", str(step), "--view", "iso", "--focus", "-19.855,-14.855,0",
        "--no-fit", "--zoom", "20", "--highlight-faces", str(fillets[0]),
    ])
    assert r.exit_code == 0, r.output
    labels = json.loads(r.stdout)["highlighted_faces"][0]["labels"]
    assert labels["iso"] in {"labeled", "too_small"}


def test_face_label_hint_groups_statuses():
    from agentcad.commands.render import _face_label_hint

    hint = _face_label_hint([
        {"id": 3, "labels": {"iso": "out_of_frame", "top": "out_of_frame"}},
        {"id": 4, "labels": {"iso": "hidden", "top": "out_of_frame"}},
        {"id": 5, "labels": {"iso": "too_small", "top": "hidden"}},
        {"id": 6, "labels": {"iso": "labeled", "top": "hidden"}},
    ], {3: (1.0, -2.5, 0.0)})
    assert "Face(s) 3 out of frame" in hint and "--focus 1,-2.5,0 --no-fit" in hint
    assert "Face(s) 4 hidden" in hint
    assert "Face(s) 5 visible but too small" in hint
    assert "Face(s) 6" not in hint and ", 6" not in hint
