# Remote MCP: connect without a local CAD install

Complete [cloud access](cloud-access.md) and privately load `AGENTCAD_API_KEY`.
The production hosted endpoint is **`https://api.agentcad.dev/mcp`**. This route
runs CAD remotely; it does not need Python, AgentCAD or a CAD engine locally.

For a Codex client supporting Streamable HTTP and custom headers, configure:

```toml
[mcp_servers.agentcad_cloud]
url = "https://api.agentcad.dev/mcp"
bearer_token_env_var = "AGENTCAD_API_KEY"
tool_timeout_sec = 120
```

See the [official MCP client guide](https://developers.openai.com/codex/mcp).
Other clients use their own settings: send `Authorization: Bearer <API key>`
through their secure header configuration, never in a prompt. An OAuth-only
client cannot use this particular manual header configuration; use its supported
connection flow instead of pasting keys into chat.

Launch the client from the environment containing the key, reload it, and confirm
tool discovery. Call `cloud_help`, then `cloud_projects`. Select an existing
project ID or call `cloud_create_project(name="Bracket", runtime="build123d")`
for a new design. A configured entry alone does not prove authentication worked.

## Build, retrieve, and edit

Give the agent the [example script and spec](example.md), and ask it to:

1. Submit `cloud_run(project=PROJECT, script_text=SOURCE, label="first", idempotency_key="bracket-first")`.
   `SOURCE` is the complete Python source string, not `bracket.py` or a local path.
2. Keep the returned job ID. Call `cloud_job(project=PROJECT, job=JOB, wait_seconds=30)`
   until terminal; poll again while queued/running. Read its actual version.
3. Use `cloud_measure`, `cloud_inspect`, and
   `cloud_check_spec(project=PROJECT, version=VERSION, spec=SPEC_OBJECT, idempotency_key="bracket-first-check")`.
   `SPEC_OBJECT` is the JSON object, not a filename. Save a distinct key for
   each inspection/check submission and poll its returned job.
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
dependencies and hosted review comments are not part
of this walkthrough. Saved-version mesh export is available through
`cloud_export`; poll its job and check `mesh_validation` before retrieving the
requested format. Consult `cloud_help` for currently supported operations.
Local parameter names and
local filesystem controls cannot be added to hosted tool calls.

## Retry and disconnect

Save your key **before** submitting. After losing a response, retry the same
tool/input/key; do not create a new submission because polling is slow. A
failed job needs diagnosis and a new key for a deliberate repair. Require
`delivery_readiness.ready` and passing task checks before delivery. An unsupported
specification is not a pass, and an unavailable comparison is not unchanged
geometry. Consult `cloud_help` rather than guessing parameter aliases.

If a verification job times out, preserve the model and recheck the same spec
with a new verification key. Replaying its old key returns the terminal failure;
reading measurements alone does not clear an incomplete check.

For retryable service responses, retain IDs and follow the returned retry advice.
A genuine missing-job or cross-account 404 remains an error. Do not infer that
no work started or submit another build just because a poll failed.

Disable/remove this server and reload the client to disconnect locally. To revoke
access, revoke the key on the website as described in [cloud access](cloud-access.md).
