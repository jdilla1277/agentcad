# cadtool

A CLI-based CAD tool designed for AI agents. Write CadQuery scripts, get back structured JSON output, versioned STEP files, PNG renders, and mesh exports.

## Requirements

- **Python 3.10-3.12** (the OpenCascade bindings do not support 3.13+)

## Install

```bash
pip install git+https://github.com/jdilla1277/mountain-climber.git#subdirectory=app
```

This installs `cadtool` and all dependencies (CadQuery, OpenCascade bindings, Click) automatically.

## Quick Start

```bash
# Initialize a project
cadtool init --name myproject

# Write a CadQuery script
cat > box.py << 'EOF'
import cadquery as cq
result = cq.Workplane("XY").box(10, 20, 5)
show_object(result)
EOF

# Run it — produces a versioned STEP file
cadtool run box.py --output box

# Render PNG views
cadtool render v1_box/output.step --view iso

# Export to mesh formats
cadtool export v1_box/output.step --format stl,glb,obj

# Check project state
cadtool context

# Compare versions
cadtool diff 1 2
```

## Commands

| Command | Description |
|---------|-------------|
| `cadtool init` | Initialize a new project (creates `cadtool.json`) |
| `cadtool run` | Execute a CadQuery script, produce versioned STEP output |
| `cadtool render` | Render PNG views of an existing STEP file |
| `cadtool export` | Export a STEP file to mesh formats (STL, GLB, OBJ) |
| `cadtool context` | Show project state (versions, current label) |
| `cadtool docs` | Show built-in documentation |
| `cadtool diff` | Compare two versions of a model |

All commands return structured JSON with `command` and `status` fields.

## Scripts

Scripts use the [CadQuery](https://cadquery.readthedocs.io/) Python API. Every script must call `show_object()` to register its output:

```python
import cadquery as cq
result = cq.Workplane("XY").box(10, 20, 5)
show_object(result)
```

cadtool also provides geometry helpers for organic shapes:

```python
from cadtool.helpers import loft_sections, tapered_sweep, naca_wire, mirror_fuse
```

Run `cadtool docs helpers` for details.

## Built-in Documentation

After installing, run `cadtool docs` for full documentation, or `cadtool docs <section>` for a specific topic:

```
cadtool docs install      # installation instructions
cadtool docs commands     # command reference
cadtool docs workflow     # typical workflow
cadtool docs export       # export formats
cadtool docs render       # render options
cadtool docs helpers      # geometry helpers
cadtool docs schema       # JSON response schema
```

## Development

```bash
/opt/homebrew/bin/python3.12 -m venv app/.venv
source app/.venv/bin/activate
pip install -e app/ pytest
pytest app/tests/ -v
```
