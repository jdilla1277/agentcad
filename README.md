# agentcad

**CAD tool for AI agents.** Give your coding agent the ability to design 3D models.

Your agent writes build123d Python scripts by default. agentcad handles execution, STEP export, PNG rendering, mesh export (STL/GLB/OBJ), geometric metrics, validation, diffing, and browser preview. CadQuery remains available as an explicit compatibility mode. Each command's final response is structured JSON on stdout.

> **Reading the output:** the JSON response is written to **stdout**; human-readable progress and diagnostics go to **stderr**. Parse stdout as JSON and treat stderr as plain text — don't merge the streams with `2>&1` before a JSON parser, or the progress lines will break parsing. If you need both, capture them separately.

agentcad is open source under the Apache License 2.0. It runs locally and requires no signup.

[![Featured on Product Hunt](https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1165633&theme=light)](https://www.producthunt.com/products/agentcad?utm_source=badge-featured&utm_medium=badge&utm_campaign=badge-agentcad)

## Demo

[![Watch a coding agent design in agentcad](https://img.youtube.com/vi/Zsn31-IilWM/maxresdefault.jpg)](https://www.youtube.com/watch?v=Zsn31-IilWM)

A coding agent designing in agentcad, live. See more at [agentcad.dev](https://agentcad.dev).

### Introducing parts

[![Watch agentcad parts rebuild a toy assembly](https://img.youtube.com/vi/VdMhRUiCaNU/maxresdefault.jpg)](https://youtu.be/VdMhRUiCaNU)

Parts let an agent build CAD as named, color-coded pieces and groups, then hand back a viewer a human can inspect. Watch the demo on [YouTube](https://youtu.be/VdMhRUiCaNU) or read the story at [agentcad.dev/parts](https://agentcad.dev/parts).

## Quick start

Install agentcad, then paste this into Claude Code, Cursor, or any coding agent:

```
Create a Python 3.12 virtual environment, then:

pip install agentcad
agentcad init --name phone-stand

Design me a phone stand: a simple angled cradle that holds a phone
at 60 degrees. About 80mm wide, 50mm deep, with a 5mm lip at the bottom
to keep the phone from sliding. Show me a preview when you're done.
```

### Keep generated files in a build directory

Create `agentcad.toml` in your source project before initialization:

```toml
build_dir = "./build"
```

Then run `agentcad init`. Version directories, the generated manifest, viewers,
exports, and logs go under `build/`; authored scripts and installed guidance
stay in the source project. Add the build directory to your own `.gitignore`.

For CI, initialize and select an independent root with
`agentcad init --build-dir /tmp/cad-build` and
`agentcad run model.py --label first --build-dir /tmp/cad-build`.
Overrides do not edit project configuration. Relative build paths resolve from
the project root, not the caller's working directory. Existing projects keep
their current layout unless configured. `--output` remains a deprecated version
label, never a destination. See `agentcad docs artifacts` for the full contract.

## What it does

- **`agentcad run script.py --label label`** — execute a build123d script, producing a versioned STEP file + geometric metrics (volume, dimensions, validity, face/edge counts)
- **Live project viewer** — successful runs open one stable local project URL
  and refresh it after subsequent completed builds. Leave the tab open while
  iterating; camera and compatible review settings survive updates. Failed
  builds leave the last successful model visible. Versioned `viewer.html`
  snapshots remain available; from v2,
  A=previous and B=current are preloaded for A/B, side-by-side, overlay, and
  Parts-tab change review (`--no-view` opts out)
- **`agentcad run ... --preview`** — four-view PNG for visual verification; the browser viewer can export an on-demand turntable GIF
- **`agentcad run ... --render iso,front`** — high-quality PNG views
- **`agentcad run ... --export stl,glb`** — mesh export for 3D printing or web viewers
- **`agentcad measure output.step`** — dimensional report (overall metrics, edge lengths, face areas, circular/cylindrical diameters)
- **`agentcad check-spec output.step spec.json`** — compare measured cylindrical features against an explicit checklist
- **`agentcad inspect output.step`** — bounded topology deep-dive with observable loading, validity, and extraction phases
- **`agentcad parts list REF`** — list named/captured parts for a version
- **`agentcad parts show REF ID`** — show one versioned part by stable id
- **`agentcad parts view REF`** — hand off an isolated, focused, or grouped part review viewer
- **`agentcad diff 1 2`** — compare versions, including actual shared/reference-only/candidate-only source-frame volume for valid closed solids
- **`agentcad view old.step new.step`** — open a synchronized A/B comparison with separate centered projection and source-frame 3D volume artifacts
- **`agentcad viewer [open|status|stop]`** — open the live project or manage its
  lightweight local service; `open` is the default
- **`agentcad docs [section]`** — runtime-aware built-in documentation and worked examples

`--label` names the version; the JSON response returns the actual file under
`outputs.step`. The older `--output LABEL` spelling remains a deprecated
compatibility alias and never denotes a destination path.

### Keep one preview open

After a normal `run` or `import`, give the human `project_viewer.url` from the
JSON response. That URL tracks the latest completed viewer; `viewer` still
points to this version's snapshot. An active page prevents another browser tab
opening. Updates are checked about once a second (less often in background
tabs). A new model replaces the previous one only after it loads successfully.

`--no-view` never launches a browser or starts the viewer service. If it still
generates viewer artifacts, an already-open project page can receive them.
The core-only `--no-preview --no-diff --no-view` combination generates no viewer;
the page retains the older model and reports that the new preview is unavailable.

The service binds to `127.0.0.1` and serves only registered viewer snapshots at
private project URLs. These are local bookmarks, not public share links. URLs
survive service restarts and builds, but moving the project folder changes its
identity. Viewer state lives in `~/.cache/agentcad/viewer` (override with
`AGENTCAD_VIEWER_HOME` for isolated automation). Keep that directory to preserve
the saved port and private URLs. If the port is occupied, AgentCAD reports the
problem without silently changing the URL. Use `agentcad viewer status` and
`agentcad viewer stop` for diagnostics; `agentcad viewer open` restarts it.
An incompatible service protocol requires stopping the service with the
installation that started it before reopening with the new installation.

## No boilerplate

Scripts need zero imports. By default, build123d primitives, `show_object`, and agentcad edit helpers are pre-injected:

```python
box = Box(10, 20, 5)
show_object(box)
```

`agentcad init` records build123d as the project runtime. That keeps the
script API, built-in docs, and subsequent runs on one clear default.

## CadQuery compatibility

CadQuery remains supported for existing scripts and projects, but it is not
the default authoring path.

For a CadQuery project:

```bash
agentcad init --name legacy-model --runtime cadquery
agentcad docs quickstart --runtime cadquery
agentcad run script.py --label first
```

For a one-off CadQuery script inside a build123d project:

```bash
agentcad docs preamble --runtime cadquery
agentcad run legacy.py --label legacy --runtime cadquery
```

Keep each script on one CAD API. If a script clearly targets the other engine,
agentcad reports the mismatch and the exact one-off override. Run
`agentcad docs runtimes` for the complete dispatch contract.

## MCP integration

For native tool integration with Claude Code, Cursor, or Windsurf:

```bash
pip install agentcad[mcp]
```

Add to `.mcp.json`:

```json
{"agentcad": {"command": "python", "args": ["-m", "agentcad.mcp"]}}
```

## Requirements

- Python 3.10–3.12 (OpenCascade bindings do not support 3.13+)

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Feedback

If your agent struggles, run `agentcad feedback "what happened"` to capture a friction log.
