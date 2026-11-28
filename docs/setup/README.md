# Choose an AgentCAD setup

Use the same workflow locally or in AgentCAD Cloud: **Inspect -> Specify ->
Build -> Validate -> Deliver**. Your agent provider supplies the LLM; AgentCAD
supplies CAD execution and evidence. These guides use the published **0.8.2**
client. No repository checkout is needed.

| Mode | Runs CAD on | Install on your computer | First-use guide |
| --- | --- | --- | --- |
| Local CLI | Your computer | Python 3.10–3.12 and `agentcad` | [Local CLI](local-cli.md) |
| Local MCP | Your computer | Python 3.10–3.12 and `agentcad[mcp]` | [Local MCP](local-mcp.md) |
| Remote CLI | AgentCAD Cloud | Python 3.10–3.12 and `agentcad[cloud]` | [Remote CLI](remote-cli.md) |
| Remote MCP | AgentCAD Cloud | An MCP client supporting Streamable HTTP and bearer headers | [Remote MCP](remote-mcp.md) |
| REST API | AgentCAD Cloud | An HTTPS client | [API](api.md) |

All five paths use the same [bracket and spacer example](example.md): build
two solids, check two 10 mm holes, enlarge them to 12 mm, and verify the spacer
is preserved. Setup and file transport differ; the design and acceptance checks
do not. Keep returned version, project, job, and file references.

Local CAD requires no account. For cloud use, start with [cloud access](cloud-access.md).
Production uses `https://api.agentcad.dev`; account and key management live on
`https://agentcad.dev`. Cloud preview access is self-service. The
[shared cloud guide](https://agentcad.dev/docs/cloud/first-cloud-build) is the
maintained source for signup, keys and the production HTTP onboarding journey.
These mode guides apply that connection to the common local/cloud example.

The remote CLI uses the explicit `agentcad cloud` command group. Setting an API
URL does not move ordinary local commands to a server. Its package still installs
local CAD dependencies; direct remote MCP and REST do not need a local engine.
Local folders and cloud projects do not synchronize automatically. Upload a
script or STEP explicitly; list projects before creating a duplicate. Authorized
clients can continue the same cloud project using its returned ID.

## Checks, review, and capability differences

A successful operation, valid geometry, a passing specification, and human
acceptance are separate. Check `delivery_readiness.ready` for deliverable geometry
and the specification verdict separately. Failed, incomplete or stale declared
specifications block delivery; a successful job alone does not authorize it.
Review the image and the actual STEP, including what the edit must preserve.

Local viewers support [spatial review comments](../../README.md#spatial-review-comments).
Their loopback URLs work on your computer. Cloud supports private STEP, PNG,
JSON and mesh retrieval, including STL/OBJ/GLB export jobs. It does not provide
a hosted interactive review/comment page. Download and review the evidence;
private artifact references are not public sharing links.

Cloud uses full geometry validation. Local kernel-profile runs can save
intermediate geometry with unchecked gates; they are not ready for delivery.
Component tools, local filesystem controls and optional local dependencies do
not automatically have cloud equivalents. Consult the installed help or hosted
`cloud_help` for each mode's actual contract and limits.

The shell examples use macOS/Linux syntax. On Windows use `.venv\Scripts`
executables and your shell's environment syntax. These guides do not claim a
Windows/Linux end-to-end setup verification.
