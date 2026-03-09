# M16 — Pre-Execution Validation

**Epic:** Fast Loop
**Status:** Planned
**Goal:** Catch preventable mistakes in <100ms before the expensive build, without consuming a version number.

## New Files

- `app/src/cadtool/validate.py` — `validate_script(source)` returning list of check results
- `app/tests/test_validate.py` — unit tests for each check

## Modified Files

- `app/src/cadtool/commands/run.py` — run validation before version allocation; `status: "validation_error"` exit
- `app/src/cadtool/commands/docs.py` — add "validation" section

## Control Flow Change in run.py

Current: `load manifest -> allocate version -> execute -> handle result`
New: `load manifest -> validate script -> allocate version -> execute -> handle result`

Validation errors return immediately with `status: "validation_error"`, NO version consumed.

## Static Checks

| Check | Implementation | Error message |
|-------|---------------|---------------|
| Syntax | `ast.parse(source)` | Python's SyntaxError with line number |
| `show_object()` present | Walk AST for Call where func is `show_object` | `"Script does not call show_object()."` |
| Import resolution | `importlib.import_module()` on each Import/ImportFrom | `"Import error: module 'X' not found"` |

## Tests (~8)

- Missing `show_object()` caught without consuming version
- Syntax error caught without consuming version
- Bad import caught without consuming version
- Valid script passes validation and runs normally
- Validation error JSON has correct structure
- Version count unchanged after validation error
- No disk artifacts from validation error
- Script with `show_object` in a conditional still passes
