"""Pre-execution validation for CadQuery scripts."""

import ast
import importlib

from agentcad.output_contract import (
    STEP_OUTPUT_GUIDANCE,
    manual_step_export_checks,
    missing_output_guidance,
    step_export_guidance,
)


# Kept lightweight and local: validation runs before the CAD stack is imported
# or daemon routing begins. ``agentcad.api.__all__`` is covered against this
# list in tests so the targeted guidance cannot silently drift.
_AGENTCAD_API_NAMES = frozenset({
    "show_object", "show_assembly", "show_compound",
    "load_step", "load_step_shape", "pick_face", "pick_edge",
    "fillet_edges", "chamfer_edges", "shell_faces", "split_by_plane",
    "cut_pocket", "boss", "loft_sections", "tapered_sweep", "naca_wire",
    "mirror_fuse", "copy_shape", "safe_cut", "safe_intersection", "safe_fuse",
    "translate", "rotate", "bbox_point", "bbox_size", "place_at", "assemble",
    "annular_boss", "raise_annulus", "ellipse_wire", "spline_wire",
    "polygon_wire", "rounded_rect_wire", "elliptical_sweep",
    "involute_gear_profile",
})


def validate_script(source, output_calls=None, *, check_imports=True):
    """Validate a script source before execution.

    Returns a list of error dicts. Empty list means the script is valid.
    Each error dict has: check, severity, message. Capture errors also carry
    candidate expressions and repair snippets. Disable check_imports for a
    purely static check before daemon routing or runtime initialization.
    """
    output_calls = set(output_calls or ("show_object",))
    errors = []

    # 1. Syntax check
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        errors.append({
            "check": "syntax_error",
            "severity": "error",
            "message": f"Syntax error at line {e.lineno}: {e.msg}",
        })
        return errors  # Can't do further AST checks with bad syntax

    # 2. Check for an output capture call
    if not _has_output_call(tree, output_calls):
        call_hint = " or ".join(f"{name}()" for name in sorted(output_calls))
        guidance = missing_output_guidance(source)
        guarded = [node for node in tree.body if _is_main_guard(node)]
        if guarded:
            entrypoint = "\n".join(ast.unparse(stmt) for node in guarded for stmt in node.body)
            guard_hint = (
                "agentcad executes scripts with a module name other than '__main__', "
                "so the __name__ == '__main__' block does not run. "
                "Replace that guard with its unindented body:\n" + entrypoint
            )
            if _has_output_call(tree, output_calls, skip_main_guard=False):
                guidance = {"message": guard_hint, "candidates": [], "repair_snippets": [entrypoint]}
            else:
                guidance["message"] = guard_hint + "\nThen: " + guidance["message"]
                guidance["entrypoint_snippet"] = entrypoint
        errors.append({
            "check": "show_object_missing",
            "severity": "error",
            **guidance,
            "message": f"Script has no reachable call to {call_hint}. {guidance['message']}",
        })
        return errors  # Missing capture must fail before importing the CAD stack.

    errors.extend(manual_step_export_checks(tree))
    if errors or not check_imports:
        return errors

    # 3. Check imports resolve
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _can_import(alias.name):
                    errors.append({
                        "check": "import_error",
                        "severity": "error",
                        "message": f"Import error: module '{alias.name}' not found",
                        **step_export_guidance(f"module '{alias.name}'"),
                    })
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            module_exists = _can_import(node.module)
            for alias in node.names:
                if alias.name == "*" and module_exists:
                    continue
                error = _from_import_error(node.module, alias.name, module_exists)
                if error:
                    errors.append(error)

    return errors


def _is_main_guard(node):
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]
    return (
        isinstance(left, ast.Name) and left.id == "__name__"
        and isinstance(right, ast.Constant) and right.value == "__main__"
    ) or (
        isinstance(right, ast.Name) and right.id == "__name__"
        and isinstance(left, ast.Constant) and left.value == "__main__"
    )


def _has_output_call(tree, output_calls=None, *, skip_main_guard=True):
    """Follow calls reachable from module execution, skipping known dead guards.

    Only resolve simple local functions, aliases, constructors, and methods.
    Unknown dynamic calls cannot prove that a capture executes.
    """
    output_calls = set(output_calls or ("show_object",))
    direct_output_names, api_module_names = _output_import_bindings(
        tree, output_calls
    )
    active_calls = set()
    found = False

    def method_on(cls, name):
        return next((item for item in cls.body
                     if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and item.name == name), None)

    def run_function(node, env, cls=None, args=()):
        key = id(node)
        if key in active_calls:
            return
        active_calls.add(key)
        try:
            local = env.copy()
            params = node.args.posonlyargs + node.args.args
            if cls is not None and params:
                local[params[0].arg] = ("instance", cls)
                params = params[1:]
            for param, value in zip(params, args):
                local[param.arg] = value
            if isinstance(node, ast.Lambda):
                expression(node.body, local)
            else:
                statements(node.body, local)
        finally:
            active_calls.remove(key)

    def expression(node, env):
        nonlocal found
        if node is None or found:
            return None
        if isinstance(node, ast.Name):
            return env.get(node.id)
        if isinstance(node, ast.Lambda):
            expression(node.args, env)  # Defaults execute; the body does not.
            return ("function", node)
        if isinstance(node, ast.GeneratorExp):
            # Creating a generator evaluates its outer iterable, not its body.
            expression(node.generators[0].iter, env)
            return ("generator", node)
        if isinstance(node, ast.Attribute):
            owner = expression(node.value, env)
            if owner and owner[0] in {"instance", "class"}:
                method = method_on(owner[1], node.attr)
                if method:
                    return ("method", method, owner[1])
            return None
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Name) and func.id in direct_output_names
            ) or (
                isinstance(func, ast.Attribute)
                and func.attr in output_calls
                and _dotted_name(func.value) in api_module_names
            ):
                found = True
                return None
            target = expression(func, env)
            args = [expression(arg, env) for arg in node.args]
            for keyword in node.keywords:
                expression(keyword.value, env)
            if target:
                if target[0] == "function":
                    run_function(target[1], env, args=args)
                elif target[0] == "method":
                    run_function(target[1], env, cls=target[2], args=args)
                elif target[0] == "class":
                    new = method_on(target[1], "__new__")
                    if new:
                        run_function(new, env, cls=target[1], args=args)
                    constructor = method_on(target[1], "__init__")
                    if constructor:
                        run_function(constructor, env, cls=target[1], args=args)
                    return ("instance", target[1])
                elif target[0] == "instance":
                    caller = method_on(target[1], "__call__")
                    if caller:
                        run_function(caller, env, cls=target[1], args=args)
            # These built-ins consume a lazy iterable immediately.
            if (isinstance(func, ast.Name) and func.id in {"list", "tuple", "set"}
                    and node.args):
                consume_iterable(node.args[0], env)
            return None
        for child in ast.iter_child_nodes(node):
            expression(child, env)
        return None

    def consume_iterable(node, env):
        if isinstance(node, ast.Name):
            target = env.get(node.id)
            if target and target[0] == "generator":
                node = target[1]
        if isinstance(node, ast.GeneratorExp):
            expression(node.elt, env)
            for generator in node.generators:
                for condition in generator.ifs:
                    expression(condition, env)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "map" and node.args):
            callback = expression(node.args[0], env)
            if callback and callback[0] == "function":
                run_function(callback[1], env)

    def bind(target, value, env):
        if isinstance(target, ast.Name):
            env[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                bind(item, None, env)

    def statements(body, env):
        for node in body:
            if found:
                return
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    expression(decorator, env)
                expression(node.args, env)
                env[node.name] = ("function", node)
            elif isinstance(node, ast.ClassDef):
                for base in node.bases:
                    expression(base, env)
                for decorator in node.decorator_list:
                    expression(decorator, env)
                statements(node.body, env.copy())
                env[node.name] = ("class", node)
            elif isinstance(node, ast.Assign):
                value = expression(node.value, env)
                for target in node.targets:
                    bind(target, value, env)
            elif isinstance(node, ast.AnnAssign):
                bind(node.target, expression(node.value, env), env)
            elif isinstance(node, ast.If):
                expression(node.test, env)
                if skip_main_guard and _is_main_guard(node):
                    statements(node.orelse, env)
                elif isinstance(node.test, ast.Constant):
                    statements(node.body if node.test.value else node.orelse, env)
                else:
                    statements(node.body, env.copy())
                    statements(node.orelse, env.copy())
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                expression(node.iter if isinstance(node, (ast.For, ast.AsyncFor)) else node.test, env)
                if isinstance(node, (ast.For, ast.AsyncFor)):
                    consume_iterable(node.iter, env)
                statements(node.body, env.copy())
                statements(node.orelse, env.copy())
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    expression(item.context_expr, env)
                statements(node.body, env.copy())
            elif isinstance(node, ast.Try):
                statements(node.body, env.copy())
                for handler in node.handlers:
                    statements(handler.body, env.copy())
                statements(node.orelse, env.copy())
                statements(node.finalbody, env.copy())
            else:
                expression(node, env)

    statements(tree.body, {})
    return found


def _output_import_bindings(tree, output_calls):
    """Return names that are provably bound to AgentCAD output capture.

    Bare pre-injected names always count. Attribute calls count only when the
    receiver is an imported ``agentcad.api`` module alias; an unrelated
    ``reporter.show_object(...)`` must not bypass the early capture check.
    """
    direct_names = set(output_calls)
    module_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "agentcad.api":
                    module_names.add(alias.asname or "agentcad.api")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "agentcad":
                for alias in node.names:
                    if alias.name == "api":
                        module_names.add(alias.asname or alias.name)
            elif node.module == "agentcad.api":
                for alias in node.names:
                    if alias.name in output_calls:
                        direct_names.add(alias.asname or alias.name)
    return direct_names, module_names


def _dotted_name(node):
    """Return a dotted name for a simple Name/Attribute chain."""
    parts = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _can_import(module_name):
    """Check if a module can be imported."""
    try:
        importlib.import_module(module_name)
        return True
    except (ImportError, ModuleNotFoundError):
        return False


def _from_import_error(module_name, name, module_exists):
    """Return targeted guidance for an invalid ``from ... import ...``."""
    if module_exists:
        try:
            module = importlib.import_module(module_name)
            getattr(module, name)
            return None
        except (AttributeError, ImportError, ModuleNotFoundError):
            try:
                importlib.import_module(f"{module_name}.{name}")
                return None
            except (ImportError, ModuleNotFoundError):
                pass

    if module_name != "agentcad.api" and name in _AGENTCAD_API_NAMES:
        if (
            not module_exists
            or module_name == "agentcad"
            or module_name == "agentcad.helpers"
            or module_name == "build123d"
            or module_name.startswith("build123d.")
        ):
            return {
                "check": "import_error",
                "severity": "error",
                "message": (
                    f"Import error: '{name}' is an AgentCAD authoring helper, "
                    f"not exported by '{module_name}'. Use "
                    f"`from agentcad.api import {name}`, or remove the import "
                    "because AgentCAD pre-injects it into run scripts."
                ),
                "suggestion": f"from agentcad.api import {name}",
                "more_at": "agentcad docs preamble",
            }

    if module_name == "build123d" and name == "Vec":
        return {
            "check": "import_error",
            "severity": "error",
            "message": (
                "Import error: build123d has no 'Vec' type. Use "
                "`from build123d import Vector`."
            ),
            "suggestion": "from build123d import Vector",
            "more_at": "agentcad docs preamble",
        }

    step_writers = {
        "save_step", "write_step", "write_to_step", "export_step",
        "exportStep", "write_step_shape", "STEPControl_Writer",
        "STEPCAFControl_Writer",
    }
    if (
        name in step_writers
        or module_name in {"build123d.io", "build123d.export"}
        or (module_name == "build123d" and name in {"io", "export"})
    ):
        return {
            "check": "import_error",
            "severity": "error",
            "message": (
                f"Import error: STEP writer '{name}' from '{module_name}' is "
                "not part of the AgentCAD authoring workflow."
            ),
            "suggestion": STEP_OUTPUT_GUIDANCE,
            "more_at": "agentcad docs preamble",
        }

    if not module_exists:
        return {
            "check": "import_error",
            "severity": "error",
            "message": f"Import error: module '{module_name}' not found",
            **step_export_guidance(f"module '{module_name}'"),
        }

    return {
        "check": "import_error",
        "severity": "error",
        "message": f"Import error: '{name}' is not exported by '{module_name}'",
    }
