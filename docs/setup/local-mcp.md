# Local MCP: connect your agent to CAD

Install Python 3.10–3.12 and an MCP client that can launch a local stdio server.
Use Python 3.12 for this example. No cloud account or credentials are needed.

```sh
mkdir cad-example
cd cad-example
python3.12 -m venv .venv
.venv/bin/python -m pip install 'agentcad[mcp]==0.7.2'
.venv/bin/agentcad init --name Bracket
.venv/bin/agentcad context
```

For an existing project, enter its source folder and run `context` instead of
initializing again. Configure your client to launch this executable and arguments:

```text
command: /absolute/path/to/cad-example/.venv/bin/python
arguments: -m agentcad.mcp
working directory: /absolute/path/to/cad-example
```

For example, a Claude Code `.mcp.json` uses a `mcpServers` wrapper. See its
[MCP configuration guide](https://code.claude.com/docs/en/mcp) for project
approval and connection status:

```json
{
  "mcpServers": {
    "agentcad": {
      "command": "/absolute/path/to/cad-example/.venv/bin/python",
      "args": ["-m", "agentcad.mcp"]
    }
  }
}
```

For Codex, add this entry to your MCP configuration, using its
[official configuration guide](https://developers.openai.com/codex/mcp):

```toml
[mcp_servers.agentcad]
command = "/absolute/path/to/cad-example/.venv/bin/python"
args = ["-m", "agentcad.mcp"]
cwd = "/absolute/path/to/cad-example"
tool_timeout_sec = 120
```

Restart/reload the client and check that tool discovery succeeds. A configured
file alone does not prove the server is connected. Local paths must exist on
the machine running the MCP server. Never use an unqualified `python` when it
might select a different environment.

## Build, check, and edit

Save the [example files](example.md), then ask the connected agent to:

1. Call `context(cwd="/absolute/path/to/cad-example")` and confirm the runtime.
2. Call `run(script="bracket.py", output="first", cwd=..., export="stl,obj", idempotency_key="first-build")`.
3. Read its `outputs.step`; call `inspect(file=STEP_PATH, summary=True, cwd=...)`
   and `check_spec(file=STEP_PATH, spec_file="spec.json", cwd=...)`.
4. Review the returned preview and `project_viewer.url`. Check geometry validity,
   the spec verdict, and validation for every requested mesh.
5. Run `edited.py` with `output="edited"` and a new key, check against
   `edited-spec.json`, and call `diff(ref1="1", ref2="2", cwd=...)`
   using the actual returned version references.

`output` is the local MCP run's **label**, not a destination filename. In tool
calls, replace `...` with the same absolute working directory. Ask `docs` for
the installed tool contract when needed. Deliver only the returned files that
passed the checks.

The example uses STL/OBJ. The same fixture's GLB failed closed-mesh validation
on a clean 0.7.2 macOS arm64 install ([issue #272](https://github.com/jdilla1277/agentcad/issues/272)).
If you request it separately, read `mesh_validation.glb`; a generated file
with a failed verdict must not be delivered as a validated mesh.

Parts/review CLI commands do not all have native MCP tools in 0.7.2. If your
agent has shell access, it can read/reply to local review comments with the
CLI; do not assume those commands are discoverable MCP tools.

## Recover and disconnect

For a lost build response, call `run_status(idempotency_key="first-build", cwd=...)`.
Keep the original key; a repair or deliberate new build needs a new one. A
graphics failure can leave valid STEP output; inspect artifact statuses and
use `preview=False, diff=False, view=False` for a core-only headless build.

Remove or disable this server in your client to disconnect it, then reload the
client. There is no local login token to revoke. Stop the separate viewer or
daemon with the CLI if they are running; project history remains on disk.
