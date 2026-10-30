# Use AgentCAD cloud projects with an agent

Your agent writes a CAD script on your machine. AgentCAD uploads that file,
builds it on the host's computers, and saves a version in a cloud project.
Your agent can then measure it, check requirements, inspect it, download a
picture, and submit an edited script as another version.

The agent still needs an LLM from your agent provider. AgentCAD runs the CAD
tools; it does not supply the LLM.

## Connect directly to a hosted MCP URL

If your host supplies an MCP URL, configure that URL and your issued credentials
in a client supporting Streamable HTTP and custom HTTP headers. This connection
does not need AgentCAD or a CAD engine installed on your machine. Your host
must enable the hosted endpoint; an API address alone does not imply MCP access.

For Codex, use this MCP configuration with your actual issued URL:

```toml
[mcp_servers.agentcad_cloud]
url = "https://api.example.test/mcp"
env_http_headers = { "X-AgentCAD-Key-ID" = "AGENTCAD_KEY_ID", "X-AgentCAD-Key-Secret" = "AGENTCAD_KEY_SECRET" }
tool_timeout_sec = 120
```

Launch your client from the environment containing the two credential values.
Keep values out of prompts and configuration files; this example stores only
their environment variable names. See the [Codex MCP reference](https://developers.openai.com/codex/mcp)
for the client configuration location. This pilot connection requires a client
that can supply those headers; it does not provide an OAuth/login flow.

Ask the connected agent to call `cloud_help`. The build-and-edit loop below
also applies to the hosted tools, with these file-handling differences:

| Operation | Hosted MCP input/output |
| --- | --- |
| Build | `cloud_run` takes `script_text` containing the Python source, not a local filename. |
| Import | `cloud_import` takes `step_base64` file content, or an explicit `source_version` and `source_path` within the same cloud project. |
| Check a spec | `cloud_check_spec` takes a `spec` JSON object, not a spec file path. |
| Retrieve evidence | `cloud_artifact` takes a job/kind or returned version/path reference. `include_content=true` returns a native PNG image, embedded STEP bytes, or a JSON report over authenticated MCP. |

Supply a unique `idempotency_key` before each submission if you need to retry
after losing the MCP response. Use the same key and identical input for that
retry; use a new key for an edit. Poll existing jobs rather than resubmitting.

Hosted scripts/specs are limited to 256 KiB; STEP uploads to 20 MiB. Inline
artifact content is limited to 8 MiB. Larger artifacts return an explicit limit
error and a private download reference requiring the owning account's API
credentials. Artifact URLs are relative to the API origin and are not public
sharing links. Your client can save retrieved content locally; the hosted
server does not accept local destination paths.

The numbered setup below describes the local CLI/MCP adapter, which is another
way to use the same cloud projects and results.

## 1. Install AgentCAD and supply your credentials

Cloud access requires an API address, key ID, and secret issued by your host.
There is no signup or login screen.

The local cloud adapter is available in AgentCAD 0.7.1. Install it with
Python 3.10–3.12; this example uses Python 3.12:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install 'agentcad[mcp]==0.7.1'
.venv/bin/agentcad cloud --help
```

Your host should supply these three environment variables to the process that
starts your CLI or MCP server:

| Variable | Value |
| --- | --- |
| `AGENTCAD_API_URL` | The HTTPS API address supplied by your host. |
| `AGENTCAD_KEY_ID` | Your issued key ID. |
| `AGENTCAD_KEY_SECRET` | Your issued secret. |

Keep the values in your password manager or existing secret store. Do not put
them in this document, your CAD script, a project file, or an agent prompt.
Restart your agent or MCP server after changing its environment.

## 2. Connect your agent

Use the same local AgentCAD MCP server as for local CAD work:

```sh
/absolute/path/to/.venv/bin/python -m agentcad.mcp
```

Configure your agent client to start that command and pass the three environment
variables above. Use absolute paths for the Python executable and working folder.
The server exposes the local tools and the explicit `cloud_*` tools together.
Ask your agent to use the cloud tools for cloud projects.

For Codex, this entry in your existing MCP configuration starts the server.
Replace the two `/absolute/path/` values and launch Codex from the environment
containing your credentials:

```toml
[mcp_servers.agentcad]
command = "/absolute/path/to/.venv/bin/python"
args = ["-m", "agentcad.mcp"]
cwd = "/absolute/path/to/your/cad-folder"
env_vars = ["AGENTCAD_API_URL", "AGENTCAD_KEY_ID", "AGENTCAD_KEY_SECRET"]
tool_timeout_sec = 120
```

The configuration forwards variable names, so it contains no secret values.
See the [Codex MCP reference](https://developers.openai.com/codex/mcp) for your
client's configuration location. Other agent clients can start the same command
using their own MCP configuration format.

This server runs on your machine and calls the HTTPS API. For a host-issued
remote URL, use the hosted connection above. You do not need to initialize a
local AgentCAD project for either connection.

If you prefer the CLI, give your agent the `.venv/bin/agentcad` executable and
run `agentcad docs cloud` for the command reference. The CLI uses the same
credentials, cloud projects, and results as the MCP tools.

## 3. Give the agent a build task

For example:

> Use AgentCAD cloud tools to build an L bracket with a 100 × 60 mm base,
> an 8 mm wall thickness, a 40 mm total height, and two 10 mm holes in the
> base. Add a separate spacer as a second solid. Save the script and a JSON
> spec, build the model, inspect and measure it, and download its STEP file
> and isometric picture. Then enlarge both holes to 12 mm, save a new version,
> and verify the change. Give me the project and version references, the check
> results, and the downloaded files for review.

Provide hole positions and other dimensions when they matter. The agent should
write down its assumptions before building. Use self-contained Python scripts
with the host's build123d or CadQuery runtime; folder uploads and installing
extra packages on the host are not supported.

## 4. Follow the build-and-edit loop

The names below are the actual MCP tools. `project`, `job`, and `version` are
references returned by earlier calls, not filenames you invent.

| Step | What the agent does |
| --- | --- |
| Create a project | Call `cloud_create_project(name="Bracket", runtime="build123d")`. Keep its `id` as `project`. Use `cloud_projects` to find an existing project. |
| Submit a script | Call `cloud_run(project=project, script="model.py", label="first", cwd="/absolute/path/to/your/cad-folder")`. Keep its job `id` and `idempotency_key`. Only that script is uploaded. |
| Wait for the build | Call `cloud_job(project=project, job=job, wait_seconds=30)`. If it says `queued` or `running`, poll the same job again. When it says `succeeded`, keep its returned `version`, `artifact`, and `measurement`. If it says `failed`, read its reason before changing anything. |
| Measure and inspect | Call `cloud_measure(project=project, version=version)`. Submit `cloud_inspect(project=project, version=version, summary=True)` and poll that inspection's job. |
| Check requirements | Write a JSON spec such as the example below. Submit `cloud_check_spec(project=project, version=version, spec_file="spec.json", cwd="/absolute/path/to/your/cad-folder")` and poll its job. Read its nested `result`. |
| Save the report | After that check completes, call `cloud_download(project=project, job=check_job, kind="report", destination="first-report.json", cwd="/absolute/path/to/your/cad-folder")`. |
| Save the STEP | Call `cloud_download(project=project, job=build_job, kind="step", destination="first.step", cwd="/absolute/path/to/your/cad-folder")`. |
| Import a STEP | Call `cloud_import(project=project, file="first.step", cwd="/absolute/path/to/your/cad-folder")` and poll its job. This creates its own version; read that returned number. |
| Save a picture | Submit `cloud_render(project=project, version=version)` and poll its job. Then call `cloud_download(project=project, job=render_job, kind="render", destination="first.png", cwd="/absolute/path/to/your/cad-folder")`. Open the downloaded PNG for visual review. |
| Edit | Change the local script and submit it with a new label and a new retry key. Poll that job and keep its actual version number. Check the edited version against the edited spec. |
| Compare | Submit `cloud_diff(project=project, from_version=original_script_version, to_version=edited_script_version)` and poll its job. Read the nested `result`, including the comparison method and status. |

Each submission has its own job. Use the build job for a STEP download, the
render job for a PNG, and the spec-check job for its JSON report. An import also
creates a version, so the edited script is not necessarily version 2.

For a model containing exactly two solids and two 10 mm cylindrical holes:

```json
{
  "expect_solids": 2,
  "features": [
    {"name": "base_holes", "type": "cylinder", "diameter_mm": 10, "count": 2}
  ]
}
```

After enlarging both holes to 12 mm, change `diameter_mm` to `12` in a new spec
and check the edited version. The original 10 mm spec should fail on that
version. Additional requirements, such as material, are not checked by these
geometry tests; unsupported fields are reported rather than silently accepted.

For a ready-made multi-solid script and matching spec, use
[bracket.py](../examples/cloud/bracket.py) and
[spec.json](../examples/cloud/spec.json). The script contains two Boolean-cut
holes and a separate spacer. Increasing both hole diameters from 10 to 12 mm
removes 552.9203 mm³ while leaving the spacer in place.

## 5. Handle waiting and retries

- A submission response acknowledges a job. It does not mean geometry passed.
- A polling call waits at most 60 seconds. If the job is still running, poll
  again; do not submit another build to make it finish.
- Keep the submission's `idempotency_key`. If the response is lost, retry with
  that same key and identical input. Use a new key when changing the script,
  label, or other submitted values. The client does not retry submissions for you.
- If project creation loses its response, list projects before creating another.
- Downloads require an explicit filename in an existing local directory.
  They preserve existing files unless you explicitly request an overwrite.
  Interrupted downloads leave no partial destination file.
- A missing script/spec/STEP file is a local input error. Correct its path
  before submitting. Use absolute `cwd` values to make the selected folder clear.

## 6. Hand back evidence

Give the person or product reviewing the result:

- The cloud project ID and the original, imported, and edited version numbers.
- Measurements and the geometry validity result for the relevant versions.
- The JSON spec and completed check report for the edited version.
- The comparison result, including whether the comparison was exact,
  approximate, or unavailable, and the direction of the change.
- The downloaded STEP and PNG, plus their cloud project/job/artifact references.
  Local filenames work on your machine; API paths require credentials and are
  not public sharing links.

Keep these decisions separate:

| Result | What it establishes |
| --- | --- |
| Job `status: "succeeded"` | The requested operation completed. |
| `geometry_valid: true` or measurement `metrics.is_valid: true` | The CAD shape passed the engine's geometry validity check. |
| Spec `passed: true` | The submitted spec's supported checks passed and no unsupported requirement prevented a full verdict. |
| Spec `passed: false` | A supported requirement failed, even if the job succeeded. |
| Spec `passed: null` | The full spec has no pass verdict. Read the unsupported requirements and any supported-check results. |
| Comparison unavailable | The tool could not establish the requested comparison; this does not mean the geometry is unchanged. |
| PNG produced | A picture is available to inspect. This does not establish validity or approval. |

The receiving product owns acceptance. A person still needs to review the
picture and decide whether the result meets requirements that AgentCAD did
not check.
