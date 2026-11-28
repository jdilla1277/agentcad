# Local MCP: connect your agent to CAD

Install Python 3.10–3.12 and an MCP client that can launch a local stdio server.
Use Python 3.12 for this example. No cloud account or credentials are needed.

```sh
mkdir cad-example
cd cad-example
python3.12 -m venv .venv
.venv/bin/python -m pip install 'agentcad[mcp]==0.8.2'
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
2. Call `run(script="bracket.py", output="first", cwd=..., export="stl,obj")`.
3. Read its `outputs.step`; call `inspect(file=STEP_PATH, summary=True, cwd=...)`
   and `check_spec(file=STEP_PATH, spec_file="spec.json", cwd=...)`.
4. Review the returned preview and `project_viewer.url`. Check
   `delivery_readiness.ready`, geometry validity,
   the spec verdict, and validation for every requested mesh.
5. Run `edited.py` with `output="edited"`, check against
   `edited-spec.json`, and call `diff(ref1="1", ref2="2", cwd=...)`
   using the actual returned version references.

`output` is the local MCP run's **label**, not a destination filename. In tool
calls, replace `...` with the same absolute working directory. Ask `docs` for
the installed tool contract when needed. Deliver only the returned files that
passed the checks.

STL/OBJ are optional exports; GLB is also supported. Read the requested mesh's
`mesh_validation` verdict. Require `delivery_readiness.ready` and passing task
checks before delivery. Missing or incomplete checks are not approval.

Parts/review CLI commands do not all have native MCP tools in 0.8.2. If your
agent has shell access, it can read/reply to local review comments with the
CLI; do not assume those commands are discoverable MCP tools.

## Recover and disconnect

For a lost build response, call `context(cwd=...)` and inspect saved versions
before creating another. Local Python `run_status`/retry keys require the separate
WIP `experimental_changes=True` workflow; this ordinary local guide does not
enable it. A graphics failure can leave valid STEP output; inspect artifact statuses and
use `preview=False, diff=False, view=False` for a core-only headless build.

Remove or disable this server in your client to disconnect it, then reload the
client. There is no local login token to revoke. Stop the separate viewer or
daemon with the CLI if they are running; project history remains on disk.
