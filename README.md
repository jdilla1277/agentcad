# Mountain Climber

A CLI-based CAD tool designed for AI agents. Issue commands, get back structured JSON output to design CAD drawings.

## Requirements

- **Python 3.12** — CadQuery/OCP bindings do not support 3.13+

## Setup

```bash
/opt/homebrew/bin/python3.12 -m venv app/.venv
source app/.venv/bin/activate
pip install -e app/ pytest
```

## Usage

```bash
source app/.venv/bin/activate

# Initialize a project
cadtool init --name my_project

# Run a CadQuery script
cadtool run script.py --output my_label
```

All commands return JSON with `command` and `status` keys.

## Key Patterns

- One file per command in `app/src/cadtool/commands/`
- `manifest.py` provides shared `load_manifest(command=)` / `save_manifest()`
- Manifest schema: `name`, `version`, `created`, `versions`
- CadQuery scripts are executed via CQGI; scripts must call `show_object()` to produce output
- Successful runs create versioned directories (e.g. `v1_my_label/`) with `script.py`, `output.step`, and `meta.json`
- Failed runs create `_failed` directories (e.g. `v1_my_label_failed/`) with `script.py` and `meta.json` (no STEP output)
- Failed runs consume a version number; `current` does not advance on failure

## Testing

```bash
source app/.venv/bin/activate
pytest app/tests/ -v
```

Tests use `runner` (CliRunner) and `isolated_dir` (tmp_path + chdir) fixtures from `conftest.py`.
