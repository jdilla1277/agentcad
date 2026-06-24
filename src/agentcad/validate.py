"""Pre-execution validation for CadQuery scripts."""

import ast
import importlib


def validate_script(source, output_calls=None):
    """Validate a script source before execution.

    Returns a list of error dicts. Empty list means the script is valid.
    Each error dict has: check, severity, message.
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
        if output_calls == {"show_object"}:
            call_hint = "show_object()"
            add_hint = "Add show_object(result) to surface your geometry."
        else:
            call_hint = " or ".join(f"{name}()" for name in sorted(output_calls))
            add_hint = (
                "Add show_object(result) for one shape, or show_assembly(result) "
                "for intentional multi-body build123d output."
            )
        errors.append({
            "check": "show_object_missing",
            "severity": "error",
            "message": f"Script does not call {call_hint}. {add_hint}",
        })

    # 3. Check imports resolve
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _can_import(alias.name):
                    errors.append({
                        "check": "import_error",
                        "severity": "error",
                        "message": f"Import error: module '{alias.name}' not found",
                    })
        elif isinstance(node, ast.ImportFrom):
            if node.module and not _can_import(node.module):
                errors.append({
                    "check": "import_error",
                    "severity": "error",
                    "message": f"Import error: module '{node.module}' not found",
                })

    return errors


def _has_output_call(tree, output_calls=None):
    """Check if AST contains a geometry output call."""
    output_calls = set(output_calls or ("show_object",))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in output_calls:
                return True
    return False


def _can_import(module_name):
    """Check if a module can be imported."""
    try:
        importlib.import_module(module_name)
        return True
    except (ImportError, ModuleNotFoundError):
        return False
