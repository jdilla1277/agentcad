"""Import-free diagnostics for scripts that do not capture geometry.

Candidate expressions are repair hints only: never execute them or select an
implicit result. Static analysis cannot prove the type of a Python expression.
"""

import ast


def output_candidates(source):
    """Find possible output expressions in module scope, in source order."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    candidates = {}

    def visit(statements):
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                # Literal parameters, strings, and numeric arithmetic are not
                # useful repairs. Calls/attributes may return geometry; leave
                # their types to runtime rather than importing CAD here.
                possible = value is not None and any(
                    isinstance(child, (ast.Call, ast.Attribute))
                    or (isinstance(child, ast.Name) and child.id in candidates)
                    for child in ast.walk(value)
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        candidates.pop(target.id, None)
                        if possible:
                            candidates[target.id] = target.id
            elif isinstance(node, ast.Delete):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        candidates.pop(target.id, None)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    call = item.context_expr
                    if isinstance(call, ast.Call) and isinstance(item.optional_vars, ast.Name):
                        name = getattr(call.func, "id", getattr(call.func, "attr", ""))
                        attr = {"BuildPart": "part", "BuildSketch": "sketch", "BuildLine": "line"}.get(name)
                        if attr:
                            var = item.optional_vars.id
                            candidates[var] = f"{var}.{attr}"
                visit(node.body)
            else:
                # Include module-level branches/loops, but never function
                # locals: a suggested repair must be in the script's scope.
                for field in ("body", "orelse", "finalbody"):
                    body = getattr(node, field, None)
                    if isinstance(body, list):
                        visit(body)
                for handler in getattr(node, "handlers", []):
                    visit(handler.body)

    visit(tree.body)
    return list(candidates.values())


def missing_output_guidance(source, candidates=None):
    """Return copyable, explicit repairs without choosing among candidates."""
    if candidates is None:
        candidates = output_candidates(source)
    repairs = [f"show_object({name})" for name in candidates]
    if len(repairs) == 1:
        message = f"Add an output capture after constructing the intended geometry:\n{repairs[0]}"
    elif repairs:
        message = (
            "Multiple possible output variables found; choose the intended geometry "
            "and add its capture (or capture each intended part). No output was selected.\n"
            + "\n".join(f"- {repair}" for repair in repairs)
        )
    else:
        message = (
            "No likely result variable was found. Assign the intended shape to a "
            "variable, then capture it; for example:\n"
            "result = ...  # replace ... with your shape construction\n"
            "show_object(result)"
        )
    return {"message": message, "candidates": candidates, "repair_snippets": repairs}


def missing_output_message(source, candidates=None):
    return "Script produced no results. " + missing_output_guidance(source, candidates)["message"]
