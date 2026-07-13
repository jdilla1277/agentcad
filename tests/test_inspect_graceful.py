"""Graceful file intake regression — `inspect` must never throw a stack trace.

Every input — valid CAD, mesh, display format, garbage, empty, missing, wrong
magic bytes, malformed — produces structured JSON with `format_detected`,
`status`, `message`, `suggestion` keys. Per M60 Phase 1.
"""
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from agentcad.cli import cli


# --- input fixtures ---------------------------------------------------------

def _write(path, data):
    if isinstance(data, str):
        path.write_text(data)
    else:
        path.write_bytes(data)
    return path


def _stl_ascii(path):
    return _write(path, "solid cube\nfacet normal 0 0 1\nouter loop\n"
                        "vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
                        "endloop\nendfacet\nendsolid cube\n")


def _stl_binary(path):
    header = b"\x00" * 80
    triangle_count = struct.pack("<I", 0)
    return _write(path, header + triangle_count)


def _glb(path):
    return _write(path, b"glTF\x02\x00\x00\x00" + b"\x00" * 16)


def _html_renamed_step(path):
    return _write(path, "<!DOCTYPE html>\n<html><body>404 Not Found</body></html>\n")


def _zip_3mf(path):
    return _write(path, b"PK\x03\x04" + b"\x00" * 30)


def _ply(path):
    return _write(path, "ply\nformat ascii 1.0\nelement vertex 0\nend_header\n")


def _real_step(path):
    """Minimal valid STEP file (exported via cadquery)."""
    import cadquery as cq
    from cadquery import exporters
    box = cq.Workplane("XY").box(10, 10, 10)
    exporters.export(box, str(path))
    return path


def _truncated_step(path, isolated_dir):
    """A STEP that has the right magic but cuts off mid-content."""
    real = _real_step(isolated_dir / "_full.step")
    content = real.read_text()
    return _write(path, content[: len(content) // 2])


# --- assertions -------------------------------------------------------------

def _assert_clean_json(result):
    """Output is parseable JSON, has the expected keys, no traceback leaked."""
    assert "Traceback" not in (result.output or ""), \
        f"stack trace leaked into output:\n{result.output}"
    if result.stderr_bytes:
        assert b"Traceback" not in result.stderr_bytes, \
            f"stack trace leaked into stderr:\n{result.stderr}"
    parsed = json.loads(result.stdout)
    assert parsed["command"] == "inspect"
    assert "status" in parsed
    assert "format_detected" in parsed
    return parsed


# --- tier 0 (full edit) -----------------------------------------------------

class TestTier0FullEdit:
    def test_step_returns_success_with_format(self, runner, isolated_dir):
        step = _real_step(isolated_dir / "part.step")
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = _assert_clean_json(result)
        assert parsed["status"] == "success"
        assert parsed["format_detected"] == "step"

    def test_step_success_includes_next_actions(self, runner, isolated_dir):
        """Success path includes 1-2 contextual next_actions per design_conventions.md."""
        step = _real_step(isolated_dir / "part.step")
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = _assert_clean_json(result)
        assert "next_actions" in parsed
        assert 1 <= len(parsed["next_actions"]) <= 2
        assert all(isinstance(a, str) for a in parsed["next_actions"])
        # Each entry follows the "<command> — <why>" format with an en-dash.
        assert all(" — " in a for a in parsed["next_actions"])
        assert "more_at" in parsed

    def test_tier0_more_at_points_to_inspect_docs(self, monkeypatch, capsys):
        import agentcad.commands.inspect_cmd as inspect_module

        def fake_topology_report(*args, **kwargs):
            return ({
                "command": "inspect",
                "status": "success",
                "file": "/tmp/part.step",
                "solid_count": 1,
                "shell_count": 1,
                "shells": [{"closed": True, "face_count": 6}],
                "face_count": 6,
                "face_orientations": {"forward": 6, "reversed": 0},
                "edge_count": 12,
                "free_edge_count": 0,
                "is_valid": True,
            }, object())

        monkeypatch.setattr(
            inspect_module, "_topology_report", fake_topology_report
        )

        inspect_module._inspect_tier0(
            "/tmp/part.step",
            {"format": "step", "extension": ".step", "size_bytes": 123},
        )

        parsed = json.loads(capsys.readouterr().out)
        assert parsed["more_at"] == "agentcad docs inspect"

    def test_asymmetric_face_orientations_on_valid_solid_gets_note(
        self, runner, isolated_dir
    ):
        """Pump Manifold e2e friction test: agents saw face_orientations
        forward=11 reversed=107 on a valid closed solid and read it as
        alarming. The note explains it's normal OCCT-import behavior."""
        # Use a non-symmetric STEP — a filleted bracket commonly produces
        # asymmetric orientation counts after OCCT round-trip.
        import cadquery as cq
        from cadquery import exporters
        plate = cq.Workplane("XY").box(40, 30, 5)
        hole = cq.Workplane("XY").circle(5).extrude(10).translate((10, 0, 0))
        part = plate.cut(hole).edges(">Z").fillet(1)
        step = isolated_dir / "asymmetric.step"
        exporters.export(part, str(step))

        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = _assert_clean_json(result)
        fwd = parsed["face_orientations"]["forward"]
        rev = parsed["face_orientations"]["reversed"]
        # Test only meaningful when the condition we're noting actually holds.
        if (
            parsed.get("is_valid")
            and parsed.get("free_edge_count", 0) == 0
            and fwd != rev
        ):
            notes = parsed.get("notes", [])
            assert any(
                "orientation" in n.lower() or "forward" in n.lower()
                for n in notes
            ), f"expected orientation note for asymmetric {fwd}/{rev}; got {notes}"
        else:
            pytest.skip(
                f"shape didn't trigger the asymmetric/valid condition "
                f"(is_valid={parsed.get('is_valid')}, "
                f"free_edge_count={parsed.get('free_edge_count')}, "
                f"forward={fwd}, reversed={rev})"
            )

    def test_symmetric_orientation_omits_note(self, runner, isolated_dir):
        """When forward == reversed, the note must NOT fire — it would be
        decorative, not contextual."""
        step = _real_step(isolated_dir / "box.step")  # plain box: 3/3 typical
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = _assert_clean_json(result)
        fwd = parsed["face_orientations"]["forward"]
        rev = parsed["face_orientations"]["reversed"]
        if fwd == rev:
            notes = parsed.get("notes", [])
            assert not any(
                "asymmetry" in n.lower() for n in notes
            ), f"unexpected orientation note for symmetric {fwd}/{rev}: {notes}"
        else:
            pytest.skip(
                f"box produced asymmetric orientations {fwd}/{rev} — can't "
                "exercise the symmetric-omit path with this fixture"
            )

    def test_closed_box_omits_open_shell_note(self, runner, isolated_dir):
        """A closed box (free_edge_count == 0) should NOT trigger the
        open-shell clarification note. Notes are conditional, not decorative."""
        step = _real_step(isolated_dir / "closed.step")
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = _assert_clean_json(result)
        assert parsed.get("free_edge_count") == 0
        # Either no notes field at all, or no open-shell note in it.
        notes = parsed.get("notes", [])
        assert not any("free_edge_count" in n for n in notes), \
            f"unexpected open-shell note on a closed box: {notes}"

    def test_open_shell_triggers_clarification_note(self, runner, isolated_dir):
        """When is_valid is true AND free_edge_count > 0 (the friction-test
        scenario that read as contradictory), a `notes` entry must explain
        what the agent is looking at."""
        # Build a single open face — a 10x10 plate as a Face/Shell, not a Solid.
        # This produces is_valid: true with free edges along the perimeter.
        import cadquery as cq
        from cadquery import exporters
        face = cq.Workplane("XY").rect(10, 10).extrude(0.001)  # near-zero-thickness plate
        # Better approach: use an actual non-closed shell. Use a half-box.
        # Simplest reliable trigger: a single rectangular face wrapped in a Shell.
        from cadquery.occ_impl.shapes import Face, Shell
        f = Face.makePlane(length=10, width=10)
        shell = Shell.makeShell([f])
        path = isolated_dir / "open_shell.step"
        exporters.export(cq.Workplane("XY").newObject([shell]), str(path))

        result = runner.invoke(cli, ["inspect", str(path)])
        parsed = _assert_clean_json(result)
        # The condition must hold for the test to be meaningful.
        if parsed.get("is_valid") and parsed.get("free_edge_count", 0) > 0:
            assert "notes" in parsed, \
                f"open shell with is_valid={parsed['is_valid']} and " \
                f"free_edge_count={parsed['free_edge_count']} should have notes"
            assert any("free_edge_count" in n for n in parsed["notes"])
        else:
            pytest.skip(
                f"open-shell fixture didn't trigger condition "
                f"(is_valid={parsed.get('is_valid')}, "
                f"free_edge_count={parsed.get('free_edge_count')})"
            )

    @pytest.mark.parametrize("flags", [[], ["--ids"], ["--summary"]])
    def test_surface_only_input_explains_zero_solids(
        self, runner, isolated_dir, flags
    ):
        """A valid surface can have no solid body; explain the resulting zero
        volume and do not direct the agent into a normal solid edit flow."""
        import cadquery as cq
        from cadquery import exporters
        from cadquery.occ_impl.shapes import Face, Shell

        face = Face.makePlane(length=10, width=10)
        shell = Shell.makeShell([face])
        path = isolated_dir / "surface_only.step"
        exporters.export(cq.Workplane("XY").newObject([shell]), str(path))

        result = runner.invoke(
            cli, ["inspect", str(path), "--no-daemon", *flags]
        )
        parsed = _assert_clean_json(result)

        assert parsed["solid_count"] == 0
        assert parsed["shells"] == [{"closed": False, "face_count": 1}]
        notes = " ".join(parsed.get("notes", [])).lower()
        assert "no solid body" in notes
        assert "zero volume" in notes
        assert "loft" in notes
        next_actions = " ".join(parsed["next_actions"]).lower()
        assert "loft" in next_actions
        assert "pick_face" not in next_actions

    def test_brep_extension_is_recognized_as_tier0(self, runner, isolated_dir):
        # Even a stub BREP file (we don't need a parseable one for the format
        # detection layer; the kernel-level error is a separate concern).
        # We only assert the classification; OCCT may still reject the content.
        brep = _write(isolated_dir / "stub.brep", "DBRep_DrawableShape\n")
        result = runner.invoke(cli, ["inspect", str(brep)])
        parsed = _assert_clean_json(result)
        # Classification recognizes BREP; status may be success or malformed
        # depending on whether the stub parses. Either way, no stack trace.
        assert parsed["format_detected"] in {"brep", "step", "malformed"}


# --- tier 1 (inspect-only mesh) --------------------------------------------

class TestTier1Mesh:
    def test_stl_ascii_returns_limited_status(self, runner, isolated_dir):
        stl = _stl_ascii(isolated_dir / "part.stl")
        result = runner.invoke(cli, ["inspect", str(stl)])
        parsed = _assert_clean_json(result)
        assert parsed["status"] == "limited"
        assert parsed["format_detected"] == "stl"
        assert "editable" in parsed and parsed["editable"] is False
        assert "message" in parsed
        assert "suggestion" in parsed

    def test_stl_binary_returns_limited_status(self, runner, isolated_dir):
        stl = _stl_binary(isolated_dir / "part.stl")
        result = runner.invoke(cli, ["inspect", str(stl)])
        parsed = _assert_clean_json(result)
        assert parsed["status"] == "limited"
        assert parsed["format_detected"] == "stl"


# --- tier 2 (recognized but deferred) --------------------------------------

@pytest.mark.parametrize("ext,content", [
    ("iges", "                                                                        S      1\n"),
    ("3mf", b"PK\x03\x04" + b"\x00" * 30),
    ("obj", "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"),
    ("dxf", "0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n"),
    ("svg", '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'),
])
class TestTier2Recognized:
    def test_returns_recognized_deferred(self, runner, isolated_dir, ext, content):
        path = _write(isolated_dir / f"part.{ext}", content)
        result = runner.invoke(cli, ["inspect", str(path)])
        parsed = _assert_clean_json(result)
        assert parsed["status"] == "recognized_deferred"
        assert parsed["format_detected"] == ext
        assert "message" in parsed


# --- display formats --------------------------------------------------------

class TestDisplayFormats:
    def test_glb_returns_display_format(self, runner, isolated_dir):
        path = _glb(isolated_dir / "model.glb")
        result = runner.invoke(cli, ["inspect", str(path)])
        parsed = _assert_clean_json(result)
        assert parsed["status"] == "display_format"
        assert parsed["format_detected"] == "glb"
        assert "suggestion" in parsed
        assert "step" in parsed["suggestion"].lower()

    def test_gltf_returns_display_format(self, runner, isolated_dir):
        path = _write(isolated_dir / "model.gltf", '{"asset": {"version": "2.0"}}')
        result = runner.invoke(cli, ["inspect", str(path)])
        parsed = _assert_clean_json(result)
        assert parsed["status"] == "display_format"
        assert parsed["format_detected"] == "gltf"

    def test_ply_returns_display_format(self, runner, isolated_dir):
        path = _ply(isolated_dir / "cloud.ply")
        result = runner.invoke(cli, ["inspect", str(path)])
        parsed = _assert_clean_json(result)
        assert parsed["status"] == "display_format"
        assert parsed["format_detected"] == "ply"


# --- unknown extensions -----------------------------------------------------

class TestUnknownFormat:
    def test_blend_returns_unknown_format(self, runner, isolated_dir):
        path = _write(isolated_dir / "model.blend", b"BLENDER-v303RENDH\x00\x00\x00\x00")
        result = runner.invoke(cli, ["inspect", str(path)])
        parsed = _assert_clean_json(result)
        assert parsed["status"] == "unknown_format"
        assert "suggestion" in parsed

    def test_random_extension_returns_unknown_format(self, runner, isolated_dir):
        path = _write(isolated_dir / "weird.xyz", b"\x00\x01\x02\x03random bytes")
        result = runner.invoke(cli, ["inspect", str(path)])
        parsed = _assert_clean_json(result)
        assert parsed["status"] == "unknown_format"


# --- malformed (extension claims one thing, content says another) -----------

class TestMalformed:
    def test_html_renamed_step_returns_malformed(self, runner, isolated_dir):
        """The classic 'wget got an error page' failure mode."""
        path = _html_renamed_step(isolated_dir / "widget.step")
        result = runner.invoke(cli, ["inspect", str(path)])
        parsed = _assert_clean_json(result)
        assert parsed["status"] == "malformed"
        assert "html" in parsed.get("message", "").lower() or \
               "download" in parsed.get("message", "").lower()


# --- broken inputs (file-system level) --------------------------------------

class TestBrokenInputs:
    def test_empty_file_returns_empty(self, runner, isolated_dir):
        path = _write(isolated_dir / "empty.step", b"")
        result = runner.invoke(cli, ["inspect", str(path)])
        parsed = _assert_clean_json(result)
        assert parsed["status"] == "empty"

    def test_missing_file_returns_error(self, runner, isolated_dir):
        result = runner.invoke(cli, ["inspect", "nope.step"])
        # Existing test asserts exit_code == 1 — preserve that.
        assert result.exit_code == 1
        parsed = _assert_clean_json(result)
        assert parsed["status"] == "error"

    def test_directory_returns_error(self, runner, isolated_dir):
        d = isolated_dir / "subdir"
        d.mkdir()
        result = runner.invoke(cli, ["inspect", str(d)])
        parsed = _assert_clean_json(result)
        assert parsed["status"] == "error"

    def test_broken_symlink_returns_error(self, runner, isolated_dir):
        link = isolated_dir / "dangling.step"
        os.symlink(str(isolated_dir / "does_not_exist.step"), str(link))
        result = runner.invoke(cli, ["inspect", str(link)])
        parsed = _assert_clean_json(result)
        assert parsed["status"] == "error"


# --- the no-stack-trace contract (parametrized over all weird inputs) -------

@pytest.fixture
def weird_input_factory(isolated_dir):
    """Yields callables that produce weird inputs in isolated_dir."""
    def _make(name, kind):
        path = isolated_dir / name
        if kind == "empty":
            _write(path, b"")
        elif kind == "html_as_step":
            _html_renamed_step(path)
        elif kind == "stl_ascii":
            _stl_ascii(path)
        elif kind == "stl_binary":
            _stl_binary(path)
        elif kind == "glb":
            _glb(path)
        elif kind == "ply":
            _ply(path)
        elif kind == "zip":
            _zip_3mf(path)
        elif kind == "iges":
            _write(path, "S      1\n")
        elif kind == "obj":
            _write(path, "v 0 0 0\n")
        elif kind == "dxf":
            _write(path, "0\nSECTION\n")
        elif kind == "svg":
            _write(path, "<?xml version=\"1.0\"?><svg></svg>")
        elif kind == "blend":
            _write(path, b"BLENDER-v303")
        elif kind == "random":
            _write(path, b"\x00\x01\x02\x03\x04")
        elif kind == "directory":
            path.mkdir()
        elif kind == "broken_symlink":
            os.symlink(str(isolated_dir / "_no_target_"), str(path))
        elif kind == "truncated_step":
            _truncated_step(path, isolated_dir)
        else:
            raise ValueError(kind)
        return path
    return _make


WEIRD_INPUTS = [
    ("empty.step", "empty"),
    ("widget.step", "html_as_step"),
    ("part.stl", "stl_ascii"),
    ("part.stl", "stl_binary"),
    ("model.glb", "glb"),
    ("cloud.ply", "ply"),
    ("model.3mf", "zip"),
    ("legacy.iges", "iges"),
    ("mesh.obj", "obj"),
    ("plan.dxf", "dxf"),
    ("icon.svg", "svg"),
    ("file.blend", "blend"),
    ("garbage.xyz", "random"),
    ("subdir", "directory"),
    ("dangling.step", "broken_symlink"),
    ("truncated.step", "truncated_step"),
]


class TestNoStackTrace:
    """The load-bearing guarantee: no input produces a stack trace."""

    @pytest.mark.parametrize("name,kind", WEIRD_INPUTS)
    def test_input_returns_clean_json(self, runner, weird_input_factory, name, kind):
        path = weird_input_factory(name, kind)
        result = runner.invoke(cli, ["inspect", str(path)])
        # No stack trace, parseable JSON, expected shape.
        _assert_clean_json(result)


# --- subprocess contract: fd-level stdout cleanliness -----------------------

class TestSubprocessContract:
    """End-to-end verification that 'stdout is parseable JSON' holds at the
    fd level, not just the sys.stdout level. Catches what CliRunner can't:
    OCP's Message_DefaultMessenger writes diagnostics directly to fd 1,
    bypassing Python's stdout buffer. On a malformed STEP, that leaks raw
    text onto stdout *before* our JSON unless we silence it explicitly."""

    @staticmethod
    def _agentcad_bin() -> Path:
        candidate = Path(sys.executable).parent / "agentcad"
        assert candidate.exists(), f"agentcad CLI not found at {candidate}"
        return candidate

    def _run(self, isolated_dir, *args):
        return subprocess.run(
            [str(self._agentcad_bin()), *args],
            capture_output=True, text=True, cwd=str(isolated_dir),
        )

    def test_truncated_step_stdout_is_pure_json(self, isolated_dir):
        """OCCT's parser writes a red ANSI error line directly to fd 1
        when STEP parsing fails. That must not leak to stdout."""
        import cadquery as cq
        from cadquery import exporters
        full = isolated_dir / "_full.step"
        exporters.export(cq.Workplane("XY").box(10, 10, 10), str(full))
        truncated_text = full.read_text()[: len(full.read_text()) // 2]
        truncated = isolated_dir / "truncated.step"
        truncated.write_text(truncated_text)

        result = self._run(isolated_dir, "inspect", str(truncated))
        # Load-bearing assertion: stdout is clean JSON, full stop.
        parsed = json.loads(result.stdout)
        assert parsed["command"] == "inspect"
        assert parsed["status"] == "malformed"
        assert parsed["format_detected"] == "step"
        assert "Traceback" not in result.stderr

    def test_real_step_stdout_is_pure_json(self, isolated_dir):
        """Sanity check: the success path also produces clean JSON via
        a real subprocess (not just CliRunner)."""
        import cadquery as cq
        from cadquery import exporters
        real = isolated_dir / "real.step"
        exporters.export(cq.Workplane("XY").box(10, 10, 10), str(real))

        result = self._run(isolated_dir, "inspect", str(real))
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        assert parsed["format_detected"] == "step"
