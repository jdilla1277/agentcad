"""Missing capture repairs must be cheap, explicit, and usable by an agent."""

# collect-on-default-profile

import importlib.util
import json

import pytest

from agentcad.cli import cli
from agentcad.output_contract import output_candidates
from agentcad.validate import validate_script


@pytest.mark.parametrize(("source", "candidates"), [
    ("length = 10\nwidth = length * 2\n", []),
    ("plate = Box(10, 20, 3)\n", ["plate"]),
    ("plate = Box(10, 20, 3)\npin = Cylinder(2, 5)\n", ["plate", "pin"]),
    ("result = Box(1, 2, 3)\nresult = None\n", []),
    ("plate = Box(1, 2, 3)\ndel plate\n", []),
    ("plate: Part = Box(1, 2, 3)\n", ["plate"]),
    ("plate = cq.Workplane('XY').box(1, 2, 3)\n", ["plate"]),
    ("with BuildPart() as bracket:\n    Box(1, 2, 3)\n", ["bracket.part"]),
    ("def build():\n    local = Box(1, 2, 3)\n", []),
    ("plate = Box(1, 2, 3)\nexport_step(plate, 'manual.step')\n", ["plate"]),
])
def test_static_candidates(source, candidates):
    check, = validate_script(source, check_imports=False)
    assert check["check"] == "show_object_missing"
    assert check["candidates"] == candidates
    assert check["repair_snippets"] == [f"show_object({name})" for name in candidates]
    if len(candidates) > 1:
        assert "No output was selected" in check["message"]
    elif not candidates:
        assert "No likely result variable" in check["message"]


def test_missing_capture_does_not_import_modules(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Missing capture must fail before import resolution")

    monkeypatch.setattr("agentcad.validate._can_import", forbidden)
    check, = validate_script("import build123d\nplate = Box(1, 2, 3)\n")
    assert check["repair_snippets"] == ["show_object(plate)"]


@pytest.mark.parametrize("runtime", ["build123d", "cadquery"])
@pytest.mark.parametrize("count", [0, 1, 2])
def test_runtime_missing_capture_candidates(runtime, count):
    if importlib.util.find_spec(runtime) is None:
        pytest.skip(f"{runtime} is not installed")
    from agentcad.runners.dispatch import get_runner

    runner = get_runner(runtime)
    shape = "Box(1, 2, 3)" if runtime == "build123d" else "cq.Workplane('XY').box(1, 2, 3)"
    names = ["plate", "pin"][:count]
    source = "".join(f"{name} = {shape}\n" for name in names)
    # A dynamically skipped call passes static validation but captures nothing.
    source += "emit = False\nif emit:\n    show_object(None)\n"
    assert runner.validate(source) == []
    result = runner.execute(source)
    assert result.status == "execution_error"
    assert result.topo_shape is None
    if not names:
        assert "No likely result variable" in result.exception
    for name in names:
        assert f"show_object({name})" in result.exception
    if count == 2:
        assert "No output was selected" in result.exception
    if count == 1:
        repaired = runner.execute(source + "show_object(plate)\n")
        assert repaired.success, repaired.exception


def test_builder_runtime_repair():
    from agentcad.runners import build123d

    source = "with BuildPart() as bracket:\n    Box(1, 2, 3)\n"
    result = build123d.execute(source)
    assert "show_object(bracket.part)" in result.exception
    assert build123d.execute(source + "show_object(bracket.part)\n").success


@pytest.mark.parametrize("no_daemon", [False, True])
def test_cli_missing_capture_precedes_runtime_and_daemon(runner, isolated_dir, monkeypatch, no_daemon):
    from agentcad.commands import run as run_mod
    from agentcad.runners import build123d

    initialized = runner.invoke(cli, ["init", "--name", "capture"])
    assert initialized.exit_code == 0, initialized.output
    script = isolated_dir / "plate.py"
    script.write_text("from build123d import Box\nplate = Box(10, 20, 3)\n")

    def forbidden(*args, **kwargs):
        pytest.fail("Missing capture must fail before daemon routing or runtime work")

    with monkeypatch.context() as patch:
        patch.setattr(run_mod, "maybe_route_through_daemon", forbidden)
        patch.setattr(build123d, "validate", forbidden)
        patch.setattr(build123d, "execute", forbidden)
        result = runner.invoke(cli, ["run", str(script), "--label", "plate"] + (["--no-daemon"] if no_daemon else []))
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "validation_error"
    assert payload["artifact_created"] is False
    assert payload["outputs"]["step"] is None
    check, = payload["checks"]
    assert check["repair_snippets"] == ["show_object(plate)"]
    assert not list(isolated_dir.rglob("*.step"))

    script.write_text(script.read_text() + check["repair_snippets"][0] + "\n")
    repaired = runner.invoke(cli, ["run", str(script), "--label", "fixed", "--no-daemon", "--no-preview", "--no-view"])
    assert repaired.exit_code == 0, repaired.output
    assert json.loads(repaired.stdout)["status"] == "success"


@pytest.mark.parametrize("source", [
    "def make():\n    show_object(Box(1, 2, 3))\nalias = make\n",
    "class Model:\n    def emit(self):\n        show_object(Box(1, 2, 3))\nModel()\n",
    "class Model:\n    def __init__(self):\n        show_object(Box(1, 2, 3))\n",
])
def test_uncalled_capture_fails_before_routing_and_execution(
    runner, isolated_dir, monkeypatch, source,
):
    from agentcad.commands import run as run_mod
    from agentcad.runners import build123d

    assert runner.invoke(cli, ["init", "--name", "capture"]).exit_code == 0
    script = isolated_dir / "plate.py"
    script.write_text("from build123d import Box\n" + source)

    def forbidden(*args, **kwargs):
        pytest.fail("Uncalled capture must fail before daemon or runner work")

    with monkeypatch.context() as patch:
        patch.setattr(run_mod, "maybe_route_through_daemon", forbidden)
        patch.setattr(build123d, "validate", forbidden)
        patch.setattr(build123d, "execute", forbidden)
        result = runner.invoke(cli, ["run", str(script), "--label", "uncalled"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "validation_error"
    assert payload["checks"][0]["check"] == "show_object_missing"
    assert not list(isolated_dir.rglob("*.step"))


@pytest.mark.parametrize("source", [
    "def emit():\n    show_object(Box(1, 2, 3))\ndef consume(cb):\n    cb()\nconsume(cb=emit)\n",
    "def emit():\n    show_object(Box(1, 2, 3))\ndef consume(cb=emit):\n    cb()\nconsume()\n",
    "class Model:\n    def emit(self):\n        show_object(Box(1, 2, 3))\ndef factory():\n    return Model()\nfactory().emit()\n",
    "class Base:\n    def emit(self):\n        show_object(Box(1, 2, 3))\nclass Model(Base):\n    pass\nModel().emit()\n",
    "class Model:\n    def emit(self, _):\n        show_object(Box(1, 2, 3))\nlist(map(Model().emit, [1]))\n",
    "capture = show_object\ncapture(Box(1, 2, 3))\n",
    "class Model:\n    def emit(self):\n        show_object(Box(1, 2, 3))\ngetattr(Model(), 'emit')()\n",
    "class Model:\n    def emit(self):\n        show_object(Box(1, 2, 3))\nmethod = 'emit'\ngetattr(Model(), method)()\n",
    "def emit():\n    show_object(Box(1, 2, 3))\n    yield 1\nlist(emit())\n",
    "import asyncio\nasync def emit():\n    show_object(Box(1, 2, 3))\nasyncio.run(emit())\n",
    "import asyncio\nasync def emit():\n    show_object(Box(1, 2, 3))\nasync def main():\n    await emit()\nasyncio.run(main())\n",
    "def emit():\n    show_object(Box(1, 2, 3))\ndef noop():\n    pass\ncallback = emit if True else noop\ncallback()\n",
    "def emit():\n    show_object(Box(1, 2, 3))\ncallbacks = [emit]\ncallbacks[0]()\n",
    "def emit():\n    show_object(Box(1, 2, 3))\ncallbacks = [emit]\nindex = 0\ncallbacks[index]()\n",
    "def emit():\n    show_object(Box(1, 2, 3))\ncallbacks = {'emit': emit}\ncallbacks['emit']()\n",
    "def emit():\n    show_object(Box(1, 2, 3))\ndef consume(cb):\n    cb()\nconsume(**{'cb': emit})\n",
    "def emit():\n    show_object(Box(1, 2, 3))\ndef consume(cb):\n    cb()\nconsume(*[emit])\n",
])
def test_resolvable_capture_reaches_execution(runner, isolated_dir, source):
    assert runner.invoke(cli, ["init", "--name", "capture"]).exit_code == 0
    script = isolated_dir / "capture.py"
    script.write_text("from build123d import Box\n" + source)
    result = runner.invoke(cli, ["run", str(script), "--label", "capture", "--no-daemon", "--no-preview", "--no-view"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["outputs"]["step"]


@pytest.mark.parametrize("source", [
    "def emit():\n    show_object(Box(1, 2, 3))\n    yield 1\nemit()\n",
    "async def emit():\n    show_object(Box(1, 2, 3))\nemit()\n",
    "def emit():\n    return\n    show_object(Box(1, 2, 3))\nemit()\n",
    "def emit():\n    try:\n        return\n    finally:\n        pass\n    show_object(Box(1, 2, 3))\nemit()\n",
    "while False:\n    show_object(Box(1, 2, 3))\n",
    "for _ in []:\n    show_object(Box(1, 2, 3))\n",
    "items = (show_object(Box(1, 2, 3)) for _ in [])\nlist(items)\n",
])
def test_deferred_or_dead_capture_fails_before_routing(
    runner, isolated_dir, monkeypatch, source,
):
    from agentcad.commands import run as run_mod
    from agentcad.runners import build123d

    assert runner.invoke(cli, ["init", "--name", "capture"]).exit_code == 0
    script = isolated_dir / "capture.py"
    script.write_text("from build123d import Box\n" + source)

    def forbidden(*args, **kwargs):
        pytest.fail("Missing capture must fail before daemon or runner work")

    with monkeypatch.context() as patch:
        patch.setattr(run_mod, "maybe_route_through_daemon", forbidden)
        patch.setattr(build123d, "validate", forbidden)
        patch.setattr(build123d, "execute", forbidden)
        result = runner.invoke(cli, ["run", str(script), "--label", "dead"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "validation_error"
    assert payload["checks"][0]["check"] == "show_object_missing"


def test_dynamic_lookup_without_possible_capture_fails():
    source = "class Model:\n    pass\nname = 'emit'\ngetattr(Model(), name)()\n"
    check, = validate_script(source, check_imports=False)
    assert check["check"] == "show_object_missing"


def test_syntax_error_has_no_candidates():
    assert output_candidates("def broken(") == []


@pytest.mark.parametrize("call", ["main()", "alias = main\nalias()", "list(map(main, [1]))"])
def test_called_function_capture_passes(call):
    source = "def main(*args):\n    show_object(Box(1, 2, 3))\n" + call
    assert validate_script(source, check_imports=False) == []


def test_called_lambda_capture_passes():
    source = "callback = lambda: show_object(Box(1, 2, 3))\ncallback()\n"
    assert validate_script(source, check_imports=False) == []


@pytest.mark.parametrize("consume", [
    "list(items)",
    "for _ in items:\n    pass",
])
def test_consumed_generator_capture_passes(consume):
    source = "items = (show_object(Box(1, 2, 3)) for _ in [1])\n" + consume
    assert validate_script(source, check_imports=False) == []


def test_uncalled_function_capture_fails():
    source = "def main():\n    show_object(Box(1, 2, 3))\n"
    assert validate_script(source, check_imports=False)[0]["check"] == "show_object_missing"


@pytest.mark.parametrize("source", [
    "def make():\n    show_object(Box(1, 2, 3))\nalias = make\n",
    "def make():\n    show_object(Box(1, 2, 3))\ncallbacks = [make]\n",
    "def make():\n    show_object(Box(1, 2, 3))\ncallback = make if True else None\n",
    "value = show_object(Box(1, 2, 3)) if False else None\n",
    "def make():\n    show_object(Box(1, 2, 3))\ncallbacks = [make]\nentry = callbacks[0]\n",
    "def make():\n    show_object(Box(1, 2, 3))\ndef consume(cb):\n    pass\nconsume(**{'cb': make})\n",
    "def make():\n    show_object(Box(1, 2, 3))\nconsume(make)\n",
    "def make():\n    show_object(Box(1, 2, 3))\niterator = map(make, [1])\n",
    "callback = lambda: show_object(Box(1, 2, 3))\n",
    "items = (show_object(Box(1, 2, 3)) for _ in [1])\n",
    "class Model:\n    def __init__(self):\n        show_object(Box(1, 2, 3))\n",
    "class Model:\n    def emit(self):\n        show_object(Box(1, 2, 3))\n",
    "class Model:\n    def emit(self):\n        show_object(Box(1, 2, 3))\nModel()\n",
    "class Model:\n    def emit(self):\n        show_object(Box(1, 2, 3))\nmodel = Model()\nmethod = model.emit\n",
])
def test_uncalled_references_do_not_count_as_capture(source):
    check, = validate_script(source, check_imports=False)
    assert check["check"] == "show_object_missing"


@pytest.mark.parametrize("source", [
    "class Model:\n    def __init__(self):\n        show_object(Box(1, 2, 3))\nModel()\n",
    "class Model:\n    def emit(self):\n        show_object(Box(1, 2, 3))\nModel().emit()\n",
    "class Model:\n    def emit(self):\n        show_object(Box(1, 2, 3))\nmodel = Model()\nmodel.emit()\n",
    "class Model:\n    def emit(self):\n        show_object(Box(1, 2, 3))\nmodel = Model()\nmethod = model.emit\nmethod()\n",
    "class Model:\n    @staticmethod\n    def emit():\n        show_object(Box(1, 2, 3))\nModel.emit()\n",
    "class Model:\n    def __init__(self):\n        self.emit()\n    def emit(self):\n        show_object(Box(1, 2, 3))\nModel()\n",
])
def test_invoked_methods_and_constructors_count_as_capture(source):
    assert validate_script(source, check_imports=False) == []


@pytest.mark.parametrize("guard", ["__name__ == '__main__'", "'__main__' == __name__"])
def test_main_guard_repair(guard):
    from agentcad.runners import build123d

    source = "def main():\n    bracket = Box(1, 2, 3)\n    show_object(bracket)\n"
    guarded_source = source + f"if {guard}:\n    main()\n"
    check, = validate_script(guarded_source, check_imports=False)
    assert "block does not run" in check["message"]
    assert check["repair_snippets"] == ["main()"]
    repaired_source = source + check["repair_snippets"][0] + "\n"
    assert validate_script(repaired_source, check_imports=False) == []
    assert build123d.execute(repaired_source).success


def test_capture_in_main_guard_else_passes():
    source = "if __name__ == '__main__':\n    pass\nelse:\n    show_object(Box(1, 2, 3))\n"
    assert validate_script(source, check_imports=False) == []


def test_statically_false_capture_fails():
    source = "plate = Box(1, 2, 3)\nif False:\n    show_object(plate)\n"
    check, = validate_script(source, check_imports=False)
    assert check["repair_snippets"] == ["show_object(plate)"]


def test_capture_in_constructor_passes():
    source = "class Model:\n    def __init__(self):\n        show_object(Box(1, 2, 3))\nModel()\n"
    assert validate_script(source, check_imports=False) == []
