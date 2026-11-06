# Local CLI: install, build, and review

Use Python **3.10–3.12**; the example pins 3.12. Install Python first if
`python3.12` is unavailable. No AgentCAD account or cloud credentials are needed.

```sh
mkdir cad-example
cd cad-example
python3.12 -m venv .venv
.venv/bin/python -m pip install 'agentcad==0.7.2'
.venv/bin/python -m pip check
.venv/bin/agentcad init --name Bracket
.venv/bin/agentcad context
```

Check `context.tool_version`. `init` installs the operating guide and creates
project history. For an existing project, enter its source folder and run
`context`; skip `init` and preserve its runtime and history. CadQuery is optional:
install `agentcad[cadquery]==0.7.2` and use an explicitly CadQuery project for
existing CadQuery scripts.

Save the [example script and spec](example.md). Use one saved retry key per
intended build; the keys below assume a new example project.

```sh
.venv/bin/agentcad run bracket.py --label first --idempotency-key first-build \
  --export stl,obj --no-daemon > first.json
```

The final JSON goes to stdout and progress goes to stderr. Read the literal
`outputs.step` path in `first.json`. Use that returned path in these commands
in place of `STEP_PATH`:

```sh
.venv/bin/agentcad inspect STEP_PATH --summary
.venv/bin/agentcad check-spec STEP_PATH spec.json
```

Review `metrics.is_valid`, the spec verdict, `preview`, and the written export
validation results. The run opens a local viewer; share `project_viewer.url`
with the person on this computer. Use its Comments control to pin feedback,
send it, and then ask the agent to read `agentcad review list --status open`.
Comments do not automatically start an agent turn.

Save the edited script/spec from the example and run:

```sh
.venv/bin/agentcad run edited.py --label edited --idempotency-key edit-build \
  --export stl,obj --no-daemon > edited.json
.venv/bin/agentcad diff 1 2
```

`1 2` assumes the two builds above were the first versions in this new project.
In an existing project, use the actual returned version numbers. Check the
edited STEP against `edited-spec.json` and verify both the change and preserved
geometry before delivering the returned STEP and requested meshes.

This example uses STL and OBJ because a clean 0.7.2 macOS arm64 run reported
14 open edges in its exported GLB. See [issue #272](https://github.com/jdilla1277/agentcad/issues/272).
If you request GLB, read `mesh_validation.glb`; a written file or a successful
core build does not establish a valid mesh. Do not deliver a failed mesh as
validated output.

## Recover and disconnect

If a run response is lost, read its saved status without executing the script:

```sh
.venv/bin/agentcad run --idempotency-key edit-build --status
```

Follow returned recovery actions for interrupted work. Do not create a new
submission simply because work is slow. Read `agentcad docs run-retries` and
`agentcad context` for the saved revision and any recovery candidates.

There is no local account to sign out of. `agentcad viewer stop` stops the local
viewer service; `agentcad daemon stop` stops the CAD daemon. Neither deletes
project files. To suppress browser startup, add `--no-view`. For an environment
without graphics, use `--no-preview --no-diff --no-view`, then render separately
where graphics support exists. A missing PNG does not mean CAD failed; inspect
the core and optional-artifact statuses. See `agentcad docs artifacts`.
