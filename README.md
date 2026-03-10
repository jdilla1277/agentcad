# cadtool

cadtool is a command-line CAD tool built for AI agents. You describe what you want, your agent builds it.

## Get started

Create a fresh directory, set up a Python 3.12 venv, and install:

```bash
mkdir myproject && cd myproject
python3.12 -m venv .venv && source .venv/bin/activate
pip install git+https://github.com/jdilla1277/mountain-climber.git#subdirectory=app
```

> **Don't have Python 3.12?** `brew install python@3.12` (macOS) or `sudo apt install python3.12 python3.12-venv` (Ubuntu). The OpenCascade bindings require 3.10–3.12 — 3.13+ won't work.

Then start [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (or any AI coding agent) in that directory and tell it what to build. The agent will run `cadtool --help`, read the operational briefing, and take it from there.

```
"Build me a helical gear with 32 teeth, module 16, 25° helix angle"
```

That's it. The agent handles `cadtool init`, writes CadQuery scripts, runs them, checks metrics, renders views, and iterates — all through structured JSON.

## How it works under the hood

cadtool executes [CadQuery](https://cadquery.readthedocs.io/) Python scripts and returns structured JSON. Every run produces a versioned STEP file, geometric metrics, and optional PNG renders and mesh exports.

```bash
cadtool init --name myproject
cadtool run gear.py --output v1 --render iso --preview
# → {"status": "success", "version": 1, "metrics": {"volume": 4218.3, ...}, ...}
```

Scripts don't need imports — `cq`, `show_object`, and geometry helpers are pre-injected. Run `cadtool --help` for the full operational briefing, or `cadtool docs` for deep-dive documentation.

| Command | What it does |
|---------|-------------|
| `cadtool run` | Execute a script → versioned STEP, metrics, renders, exports |
| `cadtool render` | Render PNG views of a STEP file |
| `cadtool export` | Export STEP to STL, GLB, OBJ |
| `cadtool inspect` | Topology report — shells, faces, edges, validity |
| `cadtool diff` | Compare metrics between versions |
| `cadtool docs` | Built-in documentation (15 sections) |
| `cadtool daemon` | Background worker to skip cold-start on repeat runs |

## Development

```bash
/opt/homebrew/bin/python3.12 -m venv app/.venv
source app/.venv/bin/activate
pip install -e app/ pytest
pytest app/tests/ -v
```
