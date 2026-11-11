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


@pytest.mark.parametrize("already_loaded", [False, True])
def test_python_helper_named_like_stdlib_is_deferred_and_scoped(tmp_path, monkeypatch, already_loaded):
    from agentcad.project_imports import script_imports
    from agentcad.validate import validate_script
    original = importlib.import_module("calendar")
    if not already_loaded:
        monkeypatch.delitem(sys.modules, "calendar")
    marker = tmp_path / "executed.txt"
    (tmp_path / "calendar.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('once')\ndef make_part(): return 12\n"
    )
    with script_imports(tmp_path / "model.py", tmp_path):
        assert validate_script("from calendar import make_part\nshow_object(make_part())") == []
        assert not marker.exists()
        assert importlib.import_module("calendar").make_part() == 12
    assert marker.read_text() == "once"
    if already_loaded:
        assert sys.modules["calendar"] is original
    else:
        assert "calendar" not in sys.modules


@pytest.mark.parametrize("venv_in_project", [False, True])
def test_namespace_folder_does_not_reload_installed_regular_package(tmp_path, monkeypatch, venv_in_project):
    from agentcad.project_imports import script_imports
    project = tmp_path / "project"
    installed = project / ".venv" / "site-packages" if venv_in_project else tmp_path / "installed"
    package = installed / "installed_fixture_pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 12\n")
    monkeypatch.syspath_prepend(str(installed))
    original = importlib.import_module("installed_fixture_pkg")
    (project / "installed_fixture_pkg").mkdir(parents=True)  # No __init__.py.
    try:
        with script_imports(project / "model.py", project):
            assert importlib.import_module("installed_fixture_pkg") is original
    finally:
        sys.modules.pop("installed_fixture_pkg", None)


def test_project_file_cannot_replace_builtin_module(tmp_path):
    from agentcad.project_imports import script_imports
    (tmp_path / "sys.py").write_text("raise AssertionError('must not replace built-in sys')\n")
    with script_imports(tmp_path / "model.py", tmp_path):
        assert importlib.import_module("sys") is sys


def test_new_helper_invalidates_directory_cache_before_validation(tmp_path):
    from agentcad.project_imports import script_imports
    from agentcad.validate import validate_script
    from importlib.machinery import PathFinder
    import os
    stamp = tmp_path.stat().st_mtime_ns
    assert PathFinder.find_spec("new_helper_module", [str(tmp_path)]) is None
    marker = tmp_path / "executed.txt"
    (tmp_path / "new_helper_module.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('once')\ndef make_part(): return 12\n"
    )
    os.utime(tmp_path, ns=(stamp, stamp))
    try:
        with script_imports(tmp_path / "model.py", tmp_path):
            assert validate_script("from new_helper_module import make_part\nshow_object(make_part())") == []
            assert not marker.exists()
            assert importlib.import_module("new_helper_module").make_part() == 12
        assert "new_helper_module" not in sys.modules
    finally:
        sys.modules.pop("new_helper_module", None)
