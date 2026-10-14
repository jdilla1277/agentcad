# agentcad

**CAD tool for AI agents.** Give your coding agent the ability to design 3D models.

Your agent writes build123d Python scripts by default. agentcad handles execution, STEP export, PNG rendering, mesh export (STL/GLB/OBJ), geometric metrics, validation, diffing, and browser preview. CadQuery remains available as an explicit compatibility mode. Each command's final response is structured JSON on stdout.

> **Reading the output:** the JSON response is written to **stdout**; human-readable progress and diagnostics go to **stderr**. Parse stdout as JSON and treat stderr as plain text — don't merge the streams with `2>&1` before a JSON parser, or the progress lines will break parsing. If you need both, capture them separately.

agentcad is open source under the Apache License 2.0. It runs locally and requires no signup.

[![Featured on Product Hunt](https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1165633&theme=light)](https://www.producthunt.com/products/agentcad?utm_source=badge-featured&utm_medium=badge&utm_campaign=badge-agentcad)

## Demo

[![Watch a coding agent design in agentcad](https://img.youtube.com/vi/Zsn31-IilWM/maxresdefault.jpg)](https://www.youtube.com/watch?v=Zsn31-IilWM)

A coding agent designing in agentcad, live. See more at [agentcad.dev](https://agentcad.dev).

### On CADGenBench: 48% higher CAD Score and 21.8% lower model cost

[![Watch measuring agentcad's effectiveness](https://img.youtube.com/vi/IacqGZH8Gu4/maxresdefault.jpg)](https://www.youtube.com/watch?v=IacqGZH8Gu4)

In a GPT-5 Nano comparison, the agent using agentcad also produced 2.5 times as many valid outputs. Watch the [video](https://www.youtube.com/watch?v=IacqGZH8Gu4), read the [benchmark results](https://agentcad.dev/measuring-agentcad-effectiveness), or [view the change log](https://agentcad.dev/changelog) for more recent updates.

The v0.7.0 release brings the five-stage workflow below to the installed guide
and built-in help. In its [Candidate L CADGenBench run](https://huggingai4engineering-cadgenbench.hf.space/reports/james-dillard_gpt-5-nano-candidate-l-pi-harness-five-s_20261001-001100.html),
GPT-5 Nano scored .245 overall (.175 generation, .353 editing) with 80 of 81
valid outputs. That is a single run, not a new matched direct-build123d
comparison; see the [experiment closeout](docs/product-plans/agentcad-performance-next-steps.md)
for the baseline, caveats, and next evaluations.

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

To install the reusable agent skill separately, use
[`npx skills add jdilla1277/agentcad-skill`](https://skills.sh/jdilla1277/agentcad-skill/agentcad)
or [`clawhub install agentcad`](https://clawhub.ai/jdilla1277/skills/agentcad).
Both install guidance, not the CAD executable; install `agentcad` with pip as
shown above. For a project-local guide, run `agentcad skill install` instead.

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

## Required agent workflow

`agentcad init` installs a guide that requires the same workflow for new models
and edits:

> **Inspect → Specify → Build → Validate → Deliver**

- **Inspect:** understand drawings, requirements, orientation, dimensions, and
  any source geometry before writing a model.
- **Specify:** record measurable requirements and assumptions. For edits, also
  identify the target, expected change, and geometry that must remain stable.
- **Build:** create the simplest geometry that can satisfy the specification;
  use the smallest bounded operation for an edit.
- **Validate:** inspect visual, dimensional, difference, and structural evidence.
  If a check fails, return to the earliest incorrect stage and iterate.
- **Deliver:** hand off only the exact STEP and requested exports that passed.

## Capabilities by workflow stage

### Inspect

- **`agentcad inspect output.step`** — bounded topology, orientation, addressable
  IDs, edit risk, and structural evidence
- **`agentcad measure output.step`** — dimensions, edge lengths, face areas, and
  circular/cylindrical feature measurements
- **`agentcad render output.step --view top,front,right,iso`** — visual evidence
  for orientation and feature location
- **`agentcad parts list REF` / `parts show REF ID`** — inspect named parts and
  isolate one versioned part

### Specify

- **`spec.json`** — records supported cylindrical-feature and structure
  requirements before modeling
- **`agentcad check-spec output.step spec.json`** — evaluates that contract
  during validation

### Build

- **`agentcad run script.py --label label`** — execute a build123d script,
  producing a versioned STEP file and geometric metrics
- **Pre-injected helpers** — build primitives, import existing STEP geometry,
  select features, and perform bounded or validated editing operations

### Validate

- **`agentcad run ... --preview`** — four-view PNG for visual verification; the
  browser viewer can export an on-demand turntable GIF
- **`agentcad diff 1 2`** — compare versions, including actual
  shared/reference-only/candidate-only source-frame volume for valid closed solids
- **`agentcad view old.step new.step`** — open a synchronized A/B comparison
  with separate centered projection and source-frame 3D volume artifacts
- **Live project viewer** — successful runs open one stable local project URL
  and refresh it after subsequent completed builds. Leave the tab open while
  iterating; camera and compatible review settings survive updates. Failed
  builds leave the last successful model visible. Versioned `viewer.html`
  snapshots remain available; from v2,
  previous and current are preloaded for side-by-side, overlay, and
  Parts-tab change review (`--no-view` opts out)
- **Spatial review comments** — pin human feedback to a surface and named part;
  comments persist locally for the agent's next revision
- **`agentcad review list --status open`** — read shared review threads; humans
  and agents can reply, resolve, and reopen with an auditable history
- **`agentcad parts view REF`** — hand off an isolated, focused, or grouped part review viewer

### Deliver

- **`agentcad run ... --export stl,glb`** or **`agentcad export`** — write mesh
  deliverables and report validation for each written format
- **`agentcad viewer [open|status|stop]`** — open the live project or manage its
  lightweight local service; `open` is the default
- **`agentcad docs [section]`** — runtime-aware built-in documentation and worked examples

## Spatial review comments

Live project viewers opened after a run or with `agentcad viewer open` use the existing service bound to
`127.0.0.1`. Press `C`, click the model, confirm the inferred named part and
revision scope, then choose **Send comment**. To hold feedback back, choose
**Save draft** instead; unsent drafts can be edited, deleted, sent individually,
or submitted together with **Send all drafts**.

Comments are plain local JSON under the selected build root's `.agentcad/reviews`—there is no database or
hosted account. On its next turn, an agent can discover and reply to the review:

```bash
agentcad context
agentcad review list --status open
agentcad review comment --message "Could this rib be thinner?" --part support_rib --version current
agentcad review reply C1 --message "Increased the clearance to 4 mm." --version current
agentcad review resolve C1 --message "Implemented in the current revision." --version current
```

Agents can also initiate an immediately-open thread on a named part. Add
`--scope previous|both` to target comparison sides or `--point-mm X,Y,Z` to
place its pin more precisely. Humans and agents can both reply, resolve, or
reopen. Every action records its
actor, timestamp, optional message, and associated revision. The older
`mark-addressed` command remains available for compatibility.

New builds wait while you write or send a comment or reply, then update the
model once you send, save, or cancel. Current/Previous/Both follow the displayed
revision sides; the original source version stays in the thread. If a part
disappears, its comment remains in the list as unplaced. Comments become
available to the agent on its next turn; they do not start a new agent run.
File viewers and temporary part/diff viewers remain read-only.

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

For editor completion or explicit dependencies, the same AgentCAD-owned
callables are available from the stable authoring module:

```python
from agentcad.api import load_step, safe_cut, translate, show_object
```

Import build123d primitives such as `Box` or `Vector` from `build123d` itself.
Do not import STEP writers: `show_object` hands the result back to AgentCAD,
which writes and tracks the canonical STEP file.

In build123d scripts, capture each shape object once, including across
`show_object()`, `show_assembly()`, and `show_compound()` calls and within
assembly iterables. Capturing an assembly also captures all its descendants;
do not capture a child separately or reuse it in another captured assembly.
Repeated references fail with `error_kind: "duplicate_capture"`;
changing the name or ID does not create another instance. For intentional
instances, use `from copy import deepcopy`, then copy the shape before positioning
and capturing it. See `agentcad docs parts` for an example.

`agentcad init` records build123d as the project runtime. That keeps the
script API, built-in docs, and subsequent runs on one clear default.

## CadQuery compatibility

CadQuery remains supported for existing scripts and projects, but it is not
the default authoring path and it is not installed by default. `pip install
agentcad` ships build123d and the OpenCascade binding only. Enable the
compatibility runtime with the optional extra:

```bash
pip install "agentcad[cadquery]"
agentcad daemon restart   # if a daemon was already running
```

The extra pulls in CadQuery and its own dependency chain (CasADi and the
COIN-OR solver libraries it bundles), which carries a larger third-party
license surface than the default install. Selecting the CadQuery runtime
without the extra returns a structured error naming this command. Adopting
agentcad inside a product or hosted service? See
[Incorporating agentcad into your project](https://agentcad.dev/incorporating-agentcad).

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

Keep each script on one CAD API. Conflicting imports, including mixed imports,
are rejected against the project runtime before daemon startup or version
creation, with guidance for fixing the script or intentionally overriding the
runtime. Unpinned projects still detect the runtime and reject ambiguous scripts.
Run responses identify the selection in `runtime_source` (`command`, `project`,
or `detection`, which includes the default fallback). Run
`agentcad docs runtimes` for the complete dispatch contract.

## MCP integration

For native tool integration with Claude Code, Cursor, or Windsurf:

```bash
pip install agentcad[mcp]
```

Extras combine: `pip install "agentcad[mcp,cadquery]"` installs both.

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
