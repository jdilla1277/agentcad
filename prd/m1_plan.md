# Milestone 1: Project Scaffolding & `cadtool init`

## Context

This is the first milestone for cadtool — a greenfield CLI CAD tool for AI agents. M1 establishes the package structure, CLI entry point, and the `cadtool init` command. Every future milestone builds on the patterns set here: structured JSON output, one file per command, isolated test fixtures.

## File Tree (10 files)

```
app/
  pyproject.toml
  src/
    cadtool/
      __init__.py
      cli.py              ← Click group, subcommand registration
      commands/
        __init__.py
        init.py            ← cadtool init logic
  tests/
    __init__.py
    conftest.py            ← runner + isolated_dir fixtures
    test_cli.py            ← 4 tests for CLI entry point
    test_init.py           ← 8 tests for init command
```

## Tech Choices

- **CLI:** Click
- **Tests:** pytest
- **Package:** src layout, pyproject.toml, `pip install -e`

## TDD Implementation Order

### Phase 0: Package skeleton (just enough to import)

1. Create `pyproject.toml` — entry point `cadtool = "cadtool.cli:cli"`, dependency on `click>=8.0`
2. Create empty `src/cadtool/__init__.py`, `src/cadtool/commands/__init__.py`, `tests/__init__.py`
3. Create `cli.py` with bare Click group (no commands yet)
4. Create `conftest.py` with `runner` (CliRunner) and `isolated_dir` (tmp_path + monkeypatch.chdir) fixtures
5. `pip install -e app/` — verify `cadtool` command resolves

### Phase 1: CLI tests — RED then GREEN

6. Write `test_cli.py` with 4 tests (command exists, help flag, init subcommand registered, unknown subcommand fails)
7. Run tests — `test_init_subcommand_registered` fails (RED)
8. Create stub `commands/init.py` with decorated `@click.command` and `pass` body
9. Wire into `cli.py` with `cli.add_command(init)`
10. Run tests — all 4 green (GREEN)

### Phase 2: Init tests — RED then GREEN

11. Write `test_init.py` with 8 tests:
    - `test_init_creates_manifest_file`
    - `test_init_manifest_has_correct_schema`
    - `test_init_default_project_name_is_directory_name`
    - `test_init_name_flag_overrides_project_name`
    - `test_init_created_date_is_iso_format`
    - `test_init_stdout_is_valid_json`
    - `test_init_success_json_schema`
    - `test_init_already_initialized_returns_error`
12. Run tests — all 8 fail (RED)
13. Implement `commands/init.py` with full logic
14. Run full suite — all 12 green (GREEN)

## Key Patterns Established

- **JSON stdout:** Every command outputs `json.dumps(result)` via `click.echo`. Always has `command` and `status` keys.
- **One file per command:** `commands/init.py`, future commands get their own file + `add_command` line in `cli.py`
- **Exit codes:** Success = 0, Error = 1. Both cases print valid JSON.
- **`MANIFEST_FILE` constant:** Defined once in `commands/init.py`, imported by future commands.
- **Test isolation:** `isolated_dir` fixture gives every test a clean temp directory.

## Verification

1. `pytest app/tests/` — all 12 tests pass
2. `cadtool --help` — shows help with `init` listed
3. `cadtool init` in a temp dir — prints success JSON, creates `cadtool.json`
4. `cadtool init` again — prints error JSON, exit code 1
5. `cadtool init --name enclosure` — uses custom name
