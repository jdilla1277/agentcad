"""Repo-level pytest hooks.

Two jobs live here:

1. Keep viewer service state and subprocesses out of the developer's
   account (session-scoped fixture at the bottom).
2. Skip CadQuery-dependent test modules when the optional ``cadquery`` extra
   is not installed. The default ``pip install agentcad`` ships build123d
   only (docs/product-plans/cadquery-optional.md); running ``pytest`` on
   that profile must prove the shared command paths have no hidden CadQuery
   import, and it can only do that if modules whose *fixtures* are built
   with CadQuery step aside instead of erroring at collection time.

The detection is a source scan for the ways this suite reaches CadQuery:
a ``cadquery`` import, the zero-import ``cq.Workplane`` preamble, or a
``--runtime cadquery`` CLI invocation. Modules that only *mention* CadQuery
in assertions about docs text still run. A module that must run on both
profiles even though it spells those strings out (it tests the missing-extra
behavior itself) opts out of the scan with the comment
``# collect-on-default-profile`` anywhere in its source.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

CADQUERY_INSTALLED = importlib.util.find_spec("cadquery") is not None

_CADQUERY_TEST_MARKERS = re.compile(
    r"^\s*(?:import|from) cadquery\b"
    r"|cq\.Workplane"
    r"|--runtime.{0,8}cadquery",
    re.MULTILINE,
)

_ALWAYS_COLLECT_MARKER = "# collect-on-default-profile"

_ignored_modules: list[str] = []


def pytest_ignore_collect(collection_path, config):
    if CADQUERY_INSTALLED:
        return None
    path = Path(collection_path)
    if path.suffix != ".py" or not path.name.startswith("test_"):
        return None
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return None
    if _ALWAYS_COLLECT_MARKER in text:
        return None
    if _CADQUERY_TEST_MARKERS.search(text):
        rel = str(path.relative_to(config.rootpath))
        if rel not in _ignored_modules:  # hook fires more than once per path
            _ignored_modules.append(rel)
        return True
    return None


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if _ignored_modules:
        terminalreporter.write_sep(
            "-",
            f"{len(_ignored_modules)} CadQuery-dependent test modules not "
            "collected: the optional `cadquery` extra is not installed "
            "(pip install \"agentcad[cadquery]\")",
        )
        for name in _ignored_modules:
            terminalreporter.write_line(f"  {name}")


@pytest.fixture(scope="session", autouse=True)
def isolated_viewer_service(tmp_path_factory):
    from agentcad import project_viewer
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("AGENTCAD_VIEWER_HOME", str(tmp_path_factory.mktemp("viewer-runtime")))
        yield
        project_viewer.stop_service()
