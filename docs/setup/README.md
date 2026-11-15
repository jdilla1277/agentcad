# Choose an AgentCAD setup

These guides describe the published **0.7.2** client and the bounded hosted
pilot. You do not need a repository checkout. Your agent provider supplies the
LLM; AgentCAD supplies CAD execution and evidence.

| Mode | Runs CAD on | Install on your computer | First-use guide |
| --- | --- | --- | --- |
| Local CLI | Your computer | Python 3.10–3.12 and `agentcad` | [Local CLI](local-cli.md) |
| Local MCP | Your computer | Python 3.10–3.12 and `agentcad[mcp]` | [Local MCP](local-mcp.md) |
| Remote CLI | Your host | Python 3.10–3.12 and `agentcad[cloud]` | [Remote CLI](remote-cli.md) |
| Remote MCP | Your host | An MCP client with Streamable HTTP and custom headers | [Remote MCP](remote-mcp.md) |
| REST API | Your host | An HTTPS client | [API](api.md) |

Local CAD requires no account. The remote CLI is the explicit `agentcad cloud`
command group; adding an API URL does not move ordinary local commands to a
server. The remote CLI package still installs local CAD dependencies. Direct
remote MCP and API access do not require a local CAD engine.

For cloud use, start with [access and credential setup](cloud-access.md). The
current pilot uses an operator-issued key pair. These guides do not provide
self-service signup, browser login, OAuth, developer bearer keys, or a ChatGPT
plugin installation. A client that requires OAuth cannot use this header-based
pilot configuration.

All five guides use the same [bracket and spacer example](example.md): build
two solids, check two 10 mm holes, change them to 12 mm, and retrieve evidence.
Keep returned project, version, job, and file references. A successful operation,
valid geometry, a passing specification, and human acceptance are separate.

## Review, limits, and continuity

Local viewers support [spatial review comments](../../README.md#spatial-review-comments).
Their loopback URLs work on your computer. The hosted pilot returns private
STEP, PNG, and JSON files; it does not provide a hosted interactive review page
or shared cloud comment workflow. Download and review its PNG and STEP. A
download URL is not a public sharing link.

The hosted pilot supports build/import, stored measurements, bounded inspection
and spec checks, a fixed isometric render, directional comparison, polling, and
private file retrieval. It does not expose the complete local product:
STL/OBJ/GLB export jobs, component tools, retained-check/promotion workflows,
background inspection controls, and hosted review are unavailable through these
pilot interfaces. Local conversion of a downloaded STEP is possible but is not
server-side export support.

Cloud projects persist across authorized clients using the same returned project
ID. Local folders and cloud projects do not synchronize automatically. Upload a
script or STEP explicitly; list cloud projects before creating a duplicate.

The command examples use POSIX shells on macOS/Linux. On Windows, activate the
Python 3.12 environment using `.venv\Scripts\Activate.ps1`; executable paths
are under `.venv\Scripts` instead of `.venv/bin`. Windows-specific setup has not
been verified by these guides.
