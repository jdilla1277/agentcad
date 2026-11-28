# Local CLI: install, build, and review

Use Python **3.10–3.12**; the example pins 3.12. Install Python first if
`python3.12` is unavailable. No AgentCAD account or cloud credentials are needed.

```sh
mkdir cad-example
cd cad-example
python3.12 -m venv .venv
.venv/bin/python -m pip install 'agentcad==0.8.2'
.venv/bin/python -m pip check
.venv/bin/agentcad init --name Bracket
.venv/bin/agentcad context
```

Check `context.tool_version`. `init` installs the operating guide and creates
project history. For an existing project, enter its source folder and run
`context`; skip `init` and preserve its runtime and history. CadQuery is optional:
install `agentcad[cadquery]==0.8.2` and use an explicitly CadQuery project for
existing CadQuery scripts.

Save the [example script and spec](example.md). The commands below assume a new example project.

```sh
.venv/bin/agentcad run bracket.py --label first \
  --export stl,obj --no-daemon > first.json
```

The final JSON goes to stdout and progress goes to stderr. Read the literal
`outputs.step` path in `first.json`. Use that returned path in these commands
in place of `STEP_PATH`:

```sh
.venv/bin/agentcad inspect STEP_PATH --summary
.venv/bin/agentcad check-spec STEP_PATH spec.json
```

Review `delivery_readiness.ready`, `metrics.is_valid`, the spec verdict,
`preview`, and the written export
validation results. The run opens a local viewer; share `project_viewer.url`
with the person on this computer. Use its Comments control to pin feedback,
send it, and then ask the agent to read `agentcad review list --status open`.
Comments do not automatically start an agent turn.

Save the edited script/spec from the example and run:

```sh
.venv/bin/agentcad run edited.py --label edited \
  --export stl,obj --no-daemon > edited.json
.venv/bin/agentcad diff 1 2
```

`1 2` assumes the two builds above were the first versions in this new project.
In an existing project, use the actual returned version numbers. Check the
edited STEP against `edited-spec.json` and verify both the change and preserved
geometry before delivering the returned STEP and requested meshes.

STL and OBJ are optional exports for this example. GLB is also supported;
read `mesh_validation` for each requested mesh. A written file or successful
core build does not establish a valid mesh. Before delivery, require
`delivery_readiness.ready` and passing task checks; inspect blockers rather than
treating `metrics.is_valid` alone as a complete acceptance verdict.

## Recover and disconnect

If a response is lost, check `agentcad context` and the saved version's
`meta.json` before running the script again. Ordinary local Python runs do not
have the cloud job/retry contract. Local `--idempotency-key` and `--status`
require the separate WIP `--experimental-changes` workflow; this guide does not
opt into it. A deliberate repair creates a new local version.

There is no local account to sign out of. `agentcad viewer stop` stops the local
viewer service; `agentcad daemon stop` stops the CAD daemon. Neither deletes
project files. To suppress browser startup, add `--no-view`. For an environment
without graphics, use `--no-preview --no-diff --no-view`, then render separately
where graphics support exists. A missing PNG does not mean CAD failed; inspect
the core and optional-artifact statuses. See `agentcad docs artifacts`.
