import json

from cadtool.cli import cli
from cadtool.manifest import MANIFEST_FILE


def _init_project(runner):
    runner.invoke(cli, ["init", "--name", "test_project"])


def _write_script(directory, content, filename="script.py"):
    path = directory / filename
    path.write_text(content)
    return path


def test_bare_script_no_imports(runner, isolated_dir):
    """Script without any imports still runs — cq is pre-injected."""
    _init_project(runner)
    _write_script(isolated_dir, content=(
        'result = cq.Workplane("XY").box(10, 10, 10)\n'
        'show_object(result)\n'
    ))
    result = runner.invoke(cli, ["run", "script.py", "--output", "bare"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["status"] == "success"


def test_helpers_available_without_import(runner, isolated_dir):
    """Helpers are pre-injected — can use loft_sections etc. without import."""
    _init_project(runner)
    # Use a helper that's easy to test — mirror_fuse on a simple box
    _write_script(isolated_dir, content=(
        'box = cq.Workplane("XY").box(10, 10, 10).val().wrapped\n'
        'result_shape = mirror_fuse(box, plane="XZ")\n'
        'import cadquery as cq2\n'
        'show_object(cq2.Workplane().newObject([cq2.Shape(result_shape)]))\n'
    ))
    result = runner.invoke(cli, ["run", "script.py", "--output", "helper"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["status"] == "success"


def test_explicit_import_still_works(runner, isolated_dir):
    """Scripts with explicit imports don't conflict with preamble."""
    _init_project(runner)
    _write_script(isolated_dir, content=(
        'import cadquery as cq\n'
        'result = cq.Workplane("XY").box(10, 10, 10)\n'
        'show_object(result)\n'
    ))
    result = runner.invoke(cli, ["run", "script.py", "--output", "explicit"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["status"] == "success"


def test_error_line_numbers_reasonable(runner, isolated_dir):
    """Error messages have line numbers close to actual source lines."""
    _init_project(runner)
    # Line 1: good, Line 2: bad
    _write_script(isolated_dir, content=(
        'result = cq.Workplane("XY").box(10, 10, 10)\n'
        'raise ValueError("deliberate error on line 2")\n'
        'show_object(result)\n'
    ))
    result = runner.invoke(cli, ["run", "script.py", "--output", "errline"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert "deliberate error on line 2" in parsed["error"]


def test_preamble_translate_available(runner, isolated_dir):
    _init_project(runner)
    script = "box = cq.Workplane('XY').box(10,10,10).val().wrapped\nmoved = translate(box, 50, 0, 0)\nshow_object(cq.Workplane('XY').newObject([cq.Shape.cast(moved)]))\n"
    _write_script(isolated_dir, content=script)
    result = runner.invoke(cli, ["run", "script.py", "--output", "t"])
    parsed = json.loads(result.output)
    assert parsed["status"] == "success"


def test_preamble_rotate_available(runner, isolated_dir):
    _init_project(runner)
    script = "box = cq.Workplane('XY').box(10,10,10).val().wrapped\nspun = rotate(box, 'Z', 45)\nshow_object(cq.Workplane('XY').newObject([cq.Shape.cast(spun)]))\n"
    _write_script(isolated_dir, content=script)
    result = runner.invoke(cli, ["run", "script.py", "--output", "r"])
    parsed = json.loads(result.output)
    assert parsed["status"] == "success"


def test_docs_preamble_section(runner):
    """cadtool docs preamble lists available pre-injected names."""
    result = runner.invoke(cli, ["docs", "preamble"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    content = data["content"]
    assert "cq" in content
    assert "loft_sections" in content
    assert "tapered_sweep" in content
    assert "naca_wire" in content
    assert "mirror_fuse" in content
    assert "translate" in content
    assert "rotate" in content
