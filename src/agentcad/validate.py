"""Pre-execution validation for CadQuery scripts."""

import ast
import importlib

from agentcad.output_contract import missing_output_guidance, step_export_guidance


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

    if not check_imports:
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
            if node.module and not _can_import(node.module):
                errors.append({
                    "check": "import_error",
                    "severity": "error",
                    "message": f"Import error: module '{node.module}' not found",
                    **step_export_guidance(f"module '{node.module}'"),
                })

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
    """Conservatively follow referenced functions, skipping known dead guards.

    A function definition alone does not execute its capture. References count
    as potentially executing it, including aliases and callback arguments.
    Dynamic conditionals still pass; the runners diagnose empty results later.
    """
    output_calls = set(output_calls or ("show_object",))
    functions = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.setdefault(node.name, []).append(node)
    visited = set()
    pending = [tree]
    while pending:
        node = pending.pop()
        if id(node) in visited:
            continue
        if isinstance(node, ast.ClassDef):
            # Construction and Python protocols invoke methods implicitly.
            # Preserve these possible captures without modeling descriptors,
            # metaclasses, or special method dispatch.
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    pending.extend(method.body)
        visited.add(id(node))
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in output_calls:
                return True
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            for function in functions.get(node.id, []):
                pending.extend(function.body)
        if isinstance(node, ast.Attribute):
            # Methods and attributes may reference locally defined callables.
            for function in functions.get(node.attr, []):
                pending.extend(function.body)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            pending.extend(node.decorator_list)
            pending.append(node.args)
            if node.decorator_list:
                pending.extend(node.body)  # A decorator can execute the function.
            continue
        if skip_main_guard and _is_main_guard(node):
            pending.extend(node.orelse)
            continue
        if isinstance(node, ast.If) and isinstance(node.test, ast.Constant):
            pending.extend(node.body if node.test.value else node.orelse)
            continue
        pending.extend(ast.iter_child_nodes(node))
    return False


def _can_import(module_name):
    """Check if a module can be imported."""
    try:
        importlib.import_module(module_name)
        return True
    except (ImportError, ModuleNotFoundError):
        return False
