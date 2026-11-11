"""Project-local modules are request scoped, including Python's import cache."""
import importlib
import sys

import pytest


@pytest.mark.parametrize("namespace", [False, True])
def test_local_import_validation_and_execution_do_not_need_pythonpath(tmp_path, namespace):
    from agentcad.project_imports import script_imports
    from agentcad.validate import validate_script
    script = tmp_path / "scripts" / "model.py"
    script.parent.mkdir()
    (script.parent / "local_part.py").write_text("from parts.dimensions import WIDTH\ndef make_part(): return WIDTH\n")
    package = tmp_path / "parts"
    package.mkdir()
    if not namespace:
        (package / "__init__.py").write_text("")
    (package / "dimensions.py").write_text("WIDTH = 12\n")
    source = "from local_part import make_part\nshow_object(make_part())"
    path_before = sys.path[:]
    with script_imports(script, tmp_path):
        assert validate_script(source) == []
        outputs = []
        exec(source, {"show_object": outputs.append})
        assert outputs == [12]
    assert sys.path == path_before
    assert not {"local_part", "parts", "parts.dimensions"} & sys.modules.keys()


def test_validation_does_not_execute_local_helpers(tmp_path):
    from agentcad.project_imports import script_imports
    from agentcad.validate import validate_script
    marker = tmp_path / "executed.txt"
    (tmp_path / "local_part.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('once')\ndef make_part(): return 1\n"
    )
    with script_imports(tmp_path / "model.py", tmp_path):
        assert validate_script("from local_part import make_part\nshow_object(make_part())") == []
        assert not marker.exists()
        importlib.import_module("local_part")
        assert marker.read_text() == "once"


def test_missing_submodule_does_not_execute_package_during_validation(tmp_path):
    from agentcad.project_imports import script_imports
    from agentcad.validate import validate_script
    package = tmp_path / "parts"
    package.mkdir()
    (package / "__init__.py").write_text("raise RuntimeError('must only execute in runner')\n")
    with script_imports(tmp_path / "model.py", tmp_path):
        assert validate_script("import parts.missing\nshow_object(1)") == []


def test_preexisting_same_named_module_is_restored(tmp_path, monkeypatch):
    from agentcad.project_imports import script_imports
    from types import ModuleType
    previous = ModuleType("local_part")
    previous.WIDTH = 99
    monkeypatch.setitem(sys.modules, "local_part", previous)
    (tmp_path / "local_part.py").write_text("WIDTH = 12\n")
    with script_imports(tmp_path / "model.py", tmp_path):
        assert importlib.import_module("local_part").WIDTH == 12
    assert sys.modules["local_part"] is previous


def test_edited_helper_and_same_named_project_are_fresh_even_with_pyc(tmp_path):
    from agentcad.project_imports import script_imports
    import os
    import py_compile
    projects = [tmp_path / name for name in ("one", "two")]
    for project, value in zip(projects, (10, 30)):
        project.mkdir()
        (project / "local_part.py").write_text(f"WIDTH = {value}\n")
    helper = projects[0] / "local_part.py"
    py_compile.compile(str(helper), doraise=True)
    timestamp = helper.stat().st_mtime_ns
    for project, value in [(projects[0], 10), (projects[1], 30), (projects[0], 20)]:
        if value == 20:
            helper.write_text("WIDTH = 20\n")  # Same size and timestamp as the cached source.
            os.utime(helper, ns=(timestamp, timestamp))
        with script_imports(project / "model.py", project):
            assert importlib.import_module("local_part").WIDTH == value
    assert "local_part" not in sys.modules


def test_import_failure_restores_process_state(tmp_path):
    from agentcad.project_imports import script_imports
    (tmp_path / "local_part.py").write_text("raise RuntimeError('bad helper')\n")
    path, finders = sys.path[:], sys.meta_path[:]
    with pytest.raises(RuntimeError, match="bad helper"):
        with script_imports(tmp_path / "model.py", tmp_path):
            importlib.import_module("local_part")
    assert sys.path == path and sys.meta_path == finders
    assert "local_part" not in sys.modules
