import json

import pytest

from agentcad.cli import cli
from agentcad.manifest import MANIFEST_FILE
from agentcad.validate import validate_script


def _init_project(runner):
    runner.invoke(cli, ["init", "--name", "test_project"])


def _write_script(directory, content, filename="script.py"):
    path = directory / filename
    path.write_text(content)
    return path


# --- Unit tests for validate_script ---


class TestValidateScript:
    def test_valid_script_no_errors(self):
        source = (
            'import cadquery as cq\n'
            'result = cq.Workplane("XY").box(10, 10, 10)\n'
            'show_object(result)\n'
        )
        errors = validate_script(source)
        assert errors == []

    def test_syntax_error_caught(self):
        source = 'this is not valid python('
        errors = validate_script(source)
        assert len(errors) >= 1
        assert errors[0]["check"] == "syntax_error"
        assert errors[0]["severity"] == "error"

    def test_missing_show_object_caught(self):
        source = (
            'import cadquery as cq\n'
            'result = cq.Workplane("XY").box(10, 10, 10)\n'
        )
        errors = validate_script(source)
        assert len(errors) >= 1
        assert any(e["check"] == "show_object_missing" for e in errors)

    def test_show_object_in_conditional_passes(self):
        source = (
            'import cadquery as cq\n'
            'result = cq.Workplane("XY").box(10, 10, 10)\n'
            'if True:\n'
            '    show_object(result)\n'
        )
        errors = validate_script(source)
        assert not any(e["check"] == "show_object_missing" for e in errors)

    def test_bad_import_caught(self):
        source = (
            'import nonexistent_module_xyz\n'
            'show_object(None)\n'
        )
        errors = validate_script(source)
        assert len(errors) >= 1
        assert any(e["check"] == "import_error" for e in errors)

    def test_known_imports_pass(self):
        source = (
            'import math\n'
            'import json\n'
            'show_object(None)\n'
        )
        errors = validate_script(source)
        assert not any(e["check"] == "import_error" for e in errors)

    def test_error_structure(self):
        source = 'bad syntax('
        errors = validate_script(source)
        e = errors[0]
        assert "check" in e
        assert "severity" in e
        assert "message" in e


# --- Integration tests ---


def test_validation_error_missing_show_object(runner, isolated_dir):
    """Missing show_object caught without consuming version."""
    _init_project(runner)
    _write_script(isolated_dir, content='import cadquery as cq\n')
    result = runner.invoke(cli, ["run", "script.py", "--output", "noshow"])
    parsed = json.loads(result.output)
    assert parsed["status"] == "validation_error"
    assert parsed["command"] == "run"
    assert "checks" in parsed
    assert any(c["check"] == "show_object_missing" for c in parsed["checks"])


def test_validation_error_syntax(runner, isolated_dir):
    """Syntax error caught without consuming version."""
    _init_project(runner)
    _write_script(isolated_dir, content='def foo(\n')
    result = runner.invoke(cli, ["run", "script.py", "--output", "badsyntax"])
    parsed = json.loads(result.output)
    assert parsed["status"] == "validation_error"
    assert any(c["check"] == "syntax_error" for c in parsed["checks"])


def test_validation_error_bad_import(runner, isolated_dir):
    """Bad import caught without consuming version."""
    _init_project(runner)
    _write_script(isolated_dir, content=(
        'import nonexistent_module_xyz\n'
        'show_object(None)\n'
    ))
    result = runner.invoke(cli, ["run", "script.py", "--output", "badimport"])
    parsed = json.loads(result.output)
    assert parsed["status"] == "validation_error"
    assert any(c["check"] == "import_error" for c in parsed["checks"])


def test_validation_error_no_version_consumed(runner, isolated_dir):
    """Version count unchanged after validation error."""
    _init_project(runner)
    _write_script(isolated_dir, content='import cadquery as cq\n')
    runner.invoke(cli, ["run", "script.py", "--output", "noshow"])
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert len(manifest.get("versions", [])) == 0


def test_validation_error_no_disk_artifacts(runner, isolated_dir):
    """No version directory created on validation error."""
    _init_project(runner)
    _write_script(isolated_dir, content='import cadquery as cq\n')
    runner.invoke(cli, ["run", "script.py", "--output", "noshow"])
    # No v1_noshow or v1_noshow_failed directory
    dirs = [d for d in isolated_dir.iterdir() if d.is_dir()]
    version_dirs = [d for d in dirs if d.name.startswith("v1")]
    assert version_dirs == []


def test_valid_script_passes_and_runs(runner, isolated_dir):
    """Valid script passes validation and runs normally."""
    _init_project(runner)
    _write_script(isolated_dir, content=(
        'import cadquery as cq\n'
        'result = cq.Workplane("XY").box(10, 10, 10)\n'
        'show_object(result)\n'
    ))
    result = runner.invoke(cli, ["run", "script.py", "--output", "good"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["status"] == "success"


def test_docs_validation_section(runner):
    """agentcad docs validation describes available checks."""
    result = runner.invoke(cli, ["docs", "validation"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    content = data["content"]
    assert "syntax" in content.lower()
    assert "show_object" in content
    assert "import" in content.lower()
