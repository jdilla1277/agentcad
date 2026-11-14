# Remote MCP: connect without a local CAD install

Complete [cloud access](cloud-access.md). Ask your host for its actual hosted
MCP URL, approval confirmation, and issued key pair. An API origin alone does
not imply that `/mcp` is enabled. Your agent client must support **Streamable
HTTP and custom HTTP headers**; an OAuth-only client cannot use this pilot.

For Codex, put the following in its MCP configuration, replacing the example
URL with the issued URL. See the [official client guide](https://developers.openai.com/codex/mcp).

```toml
[mcp_servers.agentcad_cloud]
url = "https://api.example.test/mcp"
env_http_headers = { "X-AgentCAD-Key-ID" = "AGENTCAD_KEY_ID", "X-AgentCAD-Key-Secret" = "AGENTCAD_KEY_SECRET" }
tool_timeout_sec = 120
```

Launch the client from the environment containing those credential values,
then reload/restart it. No Python, AgentCAD executable, or CAD engine is needed
locally. Keep secret values out of the configuration and prompts.

Confirm tool discovery, call `cloud_help`, then `cloud_projects`. Select an
existing project ID or call `cloud_create_project(name="Bracket", runtime="build123d")`
for a new design. A configured entry is not proof that discovery/authentication
worked. Do not use an AgentCAD CLI login or MCP OAuth login for this pilot.

## Build, retrieve, and edit

Give the agent the [example script and spec](example.md), and ask it to:

1. Submit `cloud_run(project=PROJECT, script_text=SOURCE, label="first", idempotency_key="bracket-first")`.
   `SOURCE` is the complete Python source string, not `bracket.py` or a local path.
2. Keep the returned job ID. Call `cloud_job(project=PROJECT, job=JOB, wait_seconds=30)`
   until terminal; poll again while queued/running. Read its actual version.
3. Use `cloud_measure`, `cloud_inspect`, and
   `cloud_check_spec(project=PROJECT, version=VERSION, spec=SPEC_OBJECT)`.
   `SPEC_OBJECT` is the JSON object, not a filename. Poll inspection/check jobs.
4. Call `cloud_render`, poll that job, and retrieve the PNG with
   `cloud_artifact(project=PROJECT, job=RENDER_JOB, kind="render", include_content=True)`.
5. Retrieve the build STEP with `kind="step"` and the check report with
   `kind="report"`, each using its own job ID and `include_content=True`.
6. Submit the edited source with a new key/label. Check the returned version
   using the 12 mm spec, call `cloud_diff` with actual original/edited versions,
   and poll. Review the edited PNG and downloaded STEP before delivery.

PNG content is an MCP image, STEP content is an embedded resource, and JSON
reports are text resources. Your client must support these content types to
display or save them. Hosted tools never write to a client-supplied destination
path. Keep returned byte counts and SHA-256 values when saving evidence.
Use authenticated HTTPS retrieval for files over the 8 MiB inline limit.
Private artifact references do not authorize public sharing.

Only explicit source/content or owned cloud references are uploaded. Scripts
and specs are limited to 256 KiB; STEP uploads to 20 MiB. Folder upload, custom
dependencies, hosted mesh exports, retained-check/promotion tools, and cloud
review comments are not provided by this pilot. Local parameter names and
local filesystem controls cannot be added to hosted tool calls.

## Retry and disconnect

Save your key **before** submitting. After losing a response, retry the same
tool/input/key; do not create a new submission because polling is slow. A
failed job needs diagnosis and a new key for a deliberate repair. An unsupported
specification is not a pass, and an unavailable comparison is not unchanged
geometry. Consult `cloud_help` rather than guessing parameter aliases.

If a previously accepted job unexpectedly returns `Job not found`, keep its
IDs and retry the same `cloud_job` read. Temporary 404s have been observed during
polling. Contact the host if the problem persists; do not infer that no work
started and send a new build.

Disable/remove this server and reload the client to disconnect locally.
Restart it after credential replacement. To revoke the shared pilot key pair,
contact the operator as described in [cloud access](cloud-access.md).
