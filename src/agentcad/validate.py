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

    Resolve simple local functions, aliases, constructors, and methods. Leave
    genuinely dynamic method dispatch to the runner when capture is possible.
    """
    output_calls = set(output_calls or ("show_object",))
    direct_output_names, api_module_names = _output_import_bindings(
        tree, output_calls
    )
    active_calls = set()
    defaults = {}
    class_bases = {}
    try_nodes = (ast.Try, getattr(ast, "TryStar", ast.Try))
    found = False

    def known_empty(node):
        return (
            isinstance(node, (ast.List, ast.Tuple, ast.Set)) and not node.elts
        ) or (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "range" and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == 0
        )

    def method_on(cls, name):
        method = next((item for item in reversed(cls.body)
                       if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and item.name == name), None)
        if method:
            return method
        for base in class_bases.get(id(cls), ()):
            method = method_on(base, name)
            if method:
                return method
        return None

    def class_may_capture(cls, seen=None):
        seen = set() if seen is None else seen
        if id(cls) in seen:
            return False
        seen.add(id(cls))
        for method in cls.body:
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for call in ast.walk(method):
                    if isinstance(call, ast.Call) and (
                        isinstance(call.func, ast.Name)
                        and call.func.id in direct_output_names
                        or isinstance(call.func, ast.Attribute)
                        and call.func.attr in output_calls
                        and _dotted_name(call.func.value) in api_module_names
                    ):
                        return True
        return any(class_may_capture(base, seen)
                   for base in class_bases.get(id(cls), ()))

    def is_generator_function(node):
        def has_yield(body):
            for child in ast.iter_child_nodes(body):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.Lambda, ast.ClassDef)):
                    continue
                if isinstance(child, (ast.Yield, ast.YieldFrom)) or has_yield(child):
                    return True
            return False
        return has_yield(node)

    def run_function(node, env, receiver=None, args=(), kwargs=None):
        key = id(node)
        if key in active_calls:
            return None
        local = env.copy()
        params = node.args.posonlyargs + node.args.args
        local.update(defaults.get(key, {}))
        if receiver is not None and params:
            local[params[0].arg] = receiver
            params = params[1:]
        for param, value in zip(params, args):
            local[param.arg] = value
        for name, value in (kwargs or {}).items():
            local[name] = value
        if isinstance(node, ast.AsyncFunctionDef):
            return ("coroutine", node, local)
        if is_generator_function(node):
            return ("generator_call", node, local)
        active_calls.add(key)
        try:
            if isinstance(node, ast.Lambda):
                return expression(node.body, local)
            return statements(node.body, local)[1]
        finally:
            active_calls.remove(key)

    def expression(node, env):
        nonlocal found
        if node is None or found:
            return None
        if isinstance(node, ast.Name):
            if node.id in direct_output_names and node.id not in env:
                return ("capture",)
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
                    return ("method", method, owner)
            if (node.attr in output_calls
                    and _dotted_name(node.value) in api_module_names):
                return ("capture",)
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
            kwargs = {keyword.arg: expression(keyword.value, env)
                      for keyword in node.keywords if keyword.arg is not None}
            for keyword in node.keywords:
                if keyword.arg is None:
                    expression(keyword.value, env)
            if isinstance(func, ast.Name) and func.id == "getattr" and args:
                owner = args[0]
                if owner and owner[0] in {"instance", "class"}:
                    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        method = method_on(owner[1], node.args[1].value)
                        if method:
                            return ("method", method, owner)
                        return None
                    if class_may_capture(owner[1]):
                        return ("dynamic_method", owner[1])
                    return None
            if target:
                result = invoke_target(target, env, args, kwargs)
            else:
                result = None
            # These built-ins consume a lazy iterable immediately.
            if (isinstance(func, ast.Name) and func.id in {"list", "tuple", "set"}
                    and node.args):
                consume_iterable(node.args[0], env)
            if (_dotted_name(func) == "asyncio.run" and args
                    and args[0] and args[0][0] == "coroutine"):
                statements(args[0][1].body, args[0][2])
            return result
        if isinstance(node, ast.Await):
            value = expression(node.value, env)
            if value and value[0] == "coroutine":
                statements(value[1].body, value[2])
            return None
        for child in ast.iter_child_nodes(node):
            expression(child, env)
        return None

    def invoke_target(target, env, args=(), kwargs=None):
        nonlocal found
        if target[0] == "capture":
            found = True
        elif target[0] == "function":
            return run_function(target[1], env, args=args, kwargs=kwargs)
        elif target[0] == "method":
            method, owner = target[1:]
            decorators = {name.id for name in method.decorator_list
                          if isinstance(name, ast.Name)}
            receiver = None if "staticmethod" in decorators else (
                ("class", owner[1]) if "classmethod" in decorators else
                owner if owner[0] == "instance" else None
            )
            return run_function(method, env, receiver=receiver,
                                args=args, kwargs=kwargs)
        elif target[0] == "class":
            cls = target[1]
            new = method_on(cls, "__new__")
            if new:
                run_function(new, env, receiver=("class", cls), args=args,
                             kwargs=kwargs)
            constructor = method_on(cls, "__init__")
            if constructor:
                run_function(constructor, env, receiver=("instance", cls),
                             args=args, kwargs=kwargs)
            return ("instance", cls)
        elif target[0] == "instance":
            caller = method_on(target[1], "__call__")
            if caller:
                return run_function(caller, env, receiver=target,
                                    args=args, kwargs=kwargs)
        elif target[0] == "dynamic_method":
            # A dynamic lookup on a known local instance may select a method
            # that captures output. Let the runner decide which one executes.
            found = True
        return None

    def consume_iterable(node, env):
        target = expression(node, env)
        if target and target[0] == "generator_call":
            statements(target[1].body, target[2])
            return
        if isinstance(node, ast.Name):
            if target and target[0] == "generator":
                node = target[1]
        if isinstance(node, ast.GeneratorExp):
            if known_empty(node.generators[0].iter):
                return
            expression(node.elt, env)
            for generator in node.generators:
                for condition in generator.ifs:
                    expression(condition, env)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "map" and node.args):
            if len(node.args) > 1 and known_empty(node.args[1]):
                return
            callback = expression(node.args[0], env)
            if callback:
                invoke_target(callback, env)

    def bind(target, value, env):
        if isinstance(target, ast.Name):
            env[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                bind(item, None, env)

    def statements(body, env):
        for node in body:
            if found:
                return False, None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    expression(decorator, env)
                expression(node.args, env)
                params = node.args.posonlyargs + node.args.args
                bound_defaults = {
                    param.arg: expression(value, env)
                    for param, value in zip(params[-len(node.args.defaults):],
                                            node.args.defaults)
                } if node.args.defaults else {}
                bound_defaults.update({
                    param.arg: expression(value, env)
                    for param, value in zip(node.args.kwonlyargs,
                                            node.args.kw_defaults)
                    if value is not None
                })
                defaults[id(node)] = bound_defaults
                env[node.name] = ("function", node)
            elif isinstance(node, ast.ClassDef):
                for base in node.bases:
                    expression(base, env)
                for decorator in node.decorator_list:
                    expression(decorator, env)
                class_bases[id(node)] = [value[1] for base in node.bases
                                         if (value := expression(base, env))
                                         and value[0] == "class"]
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
                    result = statements(node.orelse, env)
                elif isinstance(node.test, ast.Constant):
                    result = statements(node.body if node.test.value else node.orelse, env)
                else:
                    left = statements(node.body, env.copy())
                    right = statements(node.orelse, env.copy())
                    result = left if left[0] and right[0] else (False, None)
                if result[0]:
                    return result
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                is_for = isinstance(node, (ast.For, ast.AsyncFor))
                expression(node.iter if is_for else node.test, env)
                if is_for:
                    consume_iterable(node.iter, env)
                if (is_for and not known_empty(node.iter)) or (
                    not is_for and not (isinstance(node.test, ast.Constant)
                                        and not node.test.value)
                ):
                    statements(node.body, env.copy())
                statements(node.orelse, env.copy())
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    expression(item.context_expr, env)
                statements(node.body, env.copy())
            elif isinstance(node, try_nodes):
                body_result = statements(node.body, env.copy())
                for handler in node.handlers:
                    statements(handler.body, env.copy())
                statements(node.orelse, env.copy())
                final_result = statements(node.finalbody, env.copy())
                if final_result[0]:
                    return final_result
                if body_result[0] and not node.handlers:
                    return body_result
            elif isinstance(node, ast.Return):
                return True, expression(node.value, env)
            elif isinstance(node, (ast.Raise, ast.Break, ast.Continue)):
                expression(node, env)
                return True, None
            else:
                expression(node, env)
        return False, None

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
