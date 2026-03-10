# cadtool

cadtool is a command-line CAD tool built for AI agents. You write a short Python script describing 3D geometry, and cadtool turns it into versioned STEP files, PNG renders, mesh exports, and structured JSON — all from the terminal.

## Install

```bash
mkdir myproject && cd myproject
python3.12 -m venv .venv && source .venv/bin/activate
pip install git+https://github.com/jdilla1277/mountain-climber.git#subdirectory=app
```

> **Don't have Python 3.12?** `brew install python@3.12` (macOS) or `sudo apt install python3.12 python3.12-venv` (Ubuntu). The OpenCascade bindings require 3.10–3.12 — 3.13+ won't work.

## Your first model in 60 seconds

```bash
cadtool init --name myproject

cat > box.py << 'EOF'
box = cq.Workplane('XY').box(10, 20, 5)
show_object(box)
EOF

cadtool run box.py --output first --render iso --preview
```

That's it — cadtool pre-injects `cq` (CadQuery) and `show_object` automatically, so scripts don't need any imports.

You'll get back JSON like this:

```json
{
  "command": "run",
  "status": "success",
  "version": 1,
  "label": "first",
  "outputs": {"step": "v1_first/output.step", "script": "v1_first/script.py"},
  "metrics": {
    "dimensions": {"x": 10.0, "y": 20.0, "z": 5.0},
    "volume": 1000.0,
    "surface_area": 700.0,
    "face_count": 6,
    "edge_count": 12,
    "is_valid": true
  },
  "preview": "v1_first/preview.png",
  "renders": {"iso": "v1_first/renders/iso.png"}
}
```

Your project directory now looks like this:

```
myproject/
  cadtool.json
  box.py
  v1_first/
    output.step
    script.py
    meta.json
    preview.png
    renders/
      iso.png
```

Every run creates a new versioned directory (`v1_first`, `v2_tweaked`, `v3_final`...). Nothing is overwritten.

## Commands

| Command | What it does |
|---------|-------------|
| `cadtool init` | Create a new project |
| `cadtool run` | Execute a script → versioned STEP, metrics, renders, exports |
| `cadtool render` | Render PNG views of an existing STEP file |
| `cadtool export` | Export STEP to mesh formats (STL, GLB, OBJ) |
| `cadtool inspect` | Topology report — shells, faces, edges, validity |
| `cadtool diff` | Compare metrics between two versions |
| `cadtool context` | Show project state |
| `cadtool view` | Open a model in the browser (three.js) |
| `cadtool daemon` | Background worker to skip cold-start on repeat runs |
| `cadtool docs` | Built-in documentation (15 sections) |

Every command returns JSON with `command` and `status` keys. Parse the output to drive your workflow.

## Writing scripts

Scripts use the [CadQuery](https://cadquery.readthedocs.io/) API. The only requirement is calling `show_object()` with your result — that tells cadtool what geometry to output.

These names are available automatically (no imports needed):

| Name | What it does |
|------|-------------|
| `cq` | The cadquery module |
| `show_object` | Register geometry for output (required) |
| `translate(shape, x, y, z)` | Move a shape |
| `rotate(shape, axis, angle)` | Rotate around `'X'`, `'Y'`, or `'Z'` |
| `mirror_fuse(shape, plane)` | Mirror and boolean-fuse |
| `loft_sections(wires, smooth)` | Loft wires into a solid |
| `tapered_sweep(spine, radii)` | Sweep circles along a path |
| `naca_wire(y, le_x, te_x, t)` | NACA 4-digit airfoil wire |

Run `cadtool docs helpers` for full details on each.

## Built-in documentation

```bash
cadtool docs              # everything
cadtool docs quickstart   # getting started
cadtool docs helpers      # geometry helper functions
cadtool docs patterns     # common CadQuery patterns and footguns
cadtool docs render       # render options and camera control
cadtool docs parametric   # overriding script parameters
cadtool docs inspect      # topology debugging
```

For AI agents: `cadtool --help` outputs a comprehensive operational briefing with examples, JSON schemas, and patterns — everything needed to start producing geometry immediately.

## Development

```bash
/opt/homebrew/bin/python3.12 -m venv app/.venv
source app/.venv/bin/activate
pip install -e app/ pytest
pytest app/tests/ -v
```
