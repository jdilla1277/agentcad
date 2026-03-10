# M21 — Parametric Scripts

**Epic:** Fast Loop (Phase 2)
**Status:** Planned
**Goal:** `--params` flag on `cadtool run` passes parameter overrides to CQGI, letting agents iterate by changing numbers without rewriting scripts.

## Context

CQGI already supports parametric scripts natively. Any top-level constant assignment (`int`, `float`, `str`, `bool`) is automatically detected as an overridable parameter by its AST walker. `CQModel.build(build_parameters={...})` accepts a dict that overrides these values at the AST level before execution.

Currently `cadtool run` calls `cqgi.parse(source).build()` with no parameters. This milestone wires up a `--params` CLI flag to CQGI's existing parameter injection.

### How CQGI parameters work

1. `CQModel.__init__()` calls `_find_vars()` which walks top-level `ast.Assign` nodes.
2. Any `name = <constant>` at the top level becomes an `InputParameter` in `model.metadata.parameters`.
3. Supported types: `int`, `float`, `str`, `bool`, `tuple`.
4. `model.build(build_parameters={"name": value})` modifies the AST node in-place before `compile()` + `exec()`.
5. If a parameter name doesn't exist, `InvalidParameterError` is raised.

### Preamble interaction

The preamble (`import cadquery as cq; from cadtool.helpers import ...`) contains no top-level constant assignments, so it doesn't create spurious parameters.

## Design

### CLI

```bash
cadtool run script.py --output v1 --params length=60,sweep=0.4
cadtool run script.py --output v2 --params "length=60, sweep=0.2, label=test"
```

### Parsing `--params`

`_parse_params(raw)` splits on `,`, then for each `key=value` pair, coerces types in order: `bool` → `int` → `float` → `str`.

```python
def _parse_params(raw):
    params = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" not in pair:
            raise ValueError(f"Invalid param format: '{pair}'. Expected key=value.")
        key, val = pair.split("=", 1)
        key, val = key.strip(), val.strip()
        # Bool
        if val.lower() in ("true", "false"):
            params[key] = val.lower() == "true"
        else:
            # Int → Float → String
            try:
                params[key] = int(val)
            except ValueError:
                try:
                    params[key] = float(val)
                except ValueError:
                    params[key] = val
    return params
```

### Error handling

- **Bad `--params` format** (no `=`): Return `status: "error"` immediately. No version consumed, no disk artifacts.
- **Unknown parameter name**: CQGI raises `InvalidParameterError`. Catch before version allocation. Return `status: "error"` with the available parameter names + types so the agent can self-correct.
- **Type mismatch**: CQGI raises `InvalidParameterError`. Same treatment.

### Where params are recorded

- Output JSON: `"params": {"length": 60.0, "sweep": 0.4}` (only if `--params` used)
- meta.json: `"params": {"length": 60.0, "sweep": 0.4}` (only if `--params` used)
- `cadtool diff`: params diff shown alongside metrics changes

### Docs update

New `"parametric"` section in docs.py:
- How to declare parameters (just use top-level assignments)
- How to override with `--params`
- Type coercion rules
- Error messages and how to fix them

## Files Modified

| File | Changes |
|------|---------|
| `app/src/cadtool/commands/run.py` | Add `--params` Click option; `_parse_params()` helper; split `cqgi.parse()` from `.build()` to inspect params before building; pass `build_parameters=` to `.build()`; catch `InvalidParameterError` before version allocation; record `params` in JSON + meta.json |
| `app/src/cadtool/commands/diff.py` | Add `params` to `changes` dict (same `_scalar_diff` pattern as metrics) |
| `app/src/cadtool/commands/docs.py` | Add `"parametric"` section to SECTIONS |
| `app/tests/test_run.py` | ~10 new tests |
| `app/tests/test_docs.py` | 1 new test |
| `app/tests/test_diff.py` | 1 new test |

## Tests (12)

1. `test_run_params_override_changes_output` — Script with `length = 50.0`, run with `--params length=100` produces different bbox than default.
2. `test_run_params_in_json_response` — Output JSON includes `"params": {"length": 100.0}`.
3. `test_run_params_in_meta_json` — meta.json includes `"params"` key.
4. `test_run_params_multiple_values` — `--params length=60,width=20` overrides two parameters.
5. `test_run_params_int_preserved` — `--params count=5` passes integer, not float.
6. `test_run_params_float_coercion` — `--params ratio=0.5` passes float 0.5.
7. `test_run_params_string_value` — `--params label=test` passes string `"test"`.
8. `test_run_params_bool_true` — `--params smooth=true` passes `True`.
9. `test_run_params_unknown_parameter_error` — `--params nonexistent=5` returns `status: "error"` listing available parameter names.
10. `test_run_params_bad_format_error` — `--params badformat` returns `status: "error"`.
11. `test_run_without_params_no_params_key` — Default run (no `--params`) has no `"params"` in output JSON.
12. `test_docs_parametric_section` — `cadtool docs parametric` returns content mentioning `--params`.

## Implementation Order (Red/Green TDD)

### Step 1 — Red: `_parse_params()` and basic `--params`

Write tests 1-3, 10-11 (red). These cover: params change geometry, params in JSON, params in meta, bad format error, no params key when omitted.

### Step 2 — Green: Wire up `--params`

1. Add `_parse_params(raw)` helper to `run.py`.
2. Add `--params` Click option (default `None`).
3. Split the current `cqgi.parse(script_source).build()` into two steps:
   ```python
   model = cqgi.parse(script_source)
   # ... param handling ...
   build_result = model.build(build_parameters=parsed_params)
   ```
4. When `--params` is provided, parse and pass to `.build()`.
5. Record `params` in output JSON and meta.json (only when used).

### Step 3 — Red/Green: Type coercion tests (4-8)

Write tests for multiple values, int, float, string, bool. These should all pass with the existing `_parse_params()` implementation.

### Step 4 — Red/Green: Error handling (9)

Write test for unknown parameter. Catch `InvalidParameterError` from `cqgi` module. Return error with available parameter names before version allocation.

### Step 5 — Docs + diff

1. Add `"parametric"` section to docs.py.
2. Add params comparison to diff.py — use `_scalar_diff` on `meta.get("params", {})`.
3. Write test 12.

### Step 6 — Verify

Run full suite. Expect ~246 tests (234 + 12).

## Test Count

234 → ~246 (+12 tests)
