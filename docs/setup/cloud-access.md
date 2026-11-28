# Cloud access and credentials

AgentCAD Cloud preview access is self-service. Sign up or sign in at
[agentcad.dev/account](https://agentcad.dev/account), accept the terms, and
create a named API key at
[account/api-keys](https://agentcad.dev/account/api-keys). Copy its secret when
shown and store it privately. See the
[shared first-cloud-build guide](https://agentcad.dev/docs/cloud/first-cloud-build)
for the maintained production onboarding steps.

Production connections use:

| Setting | Value |
| --- | --- |
| Account and key management | `https://agentcad.dev` |
| `AGENTCAD_API_URL` | `https://api.agentcad.dev` (no path) |
| Hosted MCP URL | `https://api.agentcad.dev/mcp` |
| `AGENTCAD_API_KEY` | Your personal API key, loaded privately |
| HTTP authentication | `Authorization: Bearer <your API key>` |

Set the origin and load the key from your secret manager or private process
environment. Do not paste secrets into chat, source, URLs or committed config.
For the CLI/local MCP adapter, unset `AGENTCAD_KEY_ID` and
`AGENTCAD_KEY_SECRET` when choosing a personal key. Launch the agent client
from the environment containing the key; restart it after credential changes.

```sh
export AGENTCAD_API_URL='https://api.agentcad.dev'
unset AGENTCAD_KEY_ID AGENTCAD_KEY_SECRET
# Load AGENTCAD_API_KEY privately before starting the CLI or agent client.
```

The older operator-issued key-ID/secret pair remains supported for existing
integrations; it is not required for this walkthrough. A preview-site key belongs
to its matching service environment. Do not send it to production or silently
switch API origins. The [account guide](../accounts.md) also describes browser-
approved CLI installation credentials. MCP hosts can have their own connection
flows; the [remote MCP guide](remote-mcp.md) uses an explicit bearer header and
does not require an OAuth-only client to accept custom headers.

List projects to verify the connection before creating work. An empty list is
normal for a new account. For 401, check that the key is valid, unrevoked and
from the same environment. For 404, check returned references and account access.
Do not treat every 404 as transient or submit duplicate work after a polling
failure. Keep the accepted job/project IDs and original retry key.

## Replacement and revocation

Manage or revoke keys on the website's [API keys page](https://agentcad.dev/account/api-keys).
For replacement, create a new named key, check access to the same project, then
revoke the old key. Revocation blocks subsequent requests; accepted work and
project history are not deleted by removing a credential.

To disconnect locally, remove/disable the remote MCP entry, reload the client,
and unset the environment variables in shells and processes that inherited them:

```sh
unset AGENTCAD_API_URL AGENTCAD_API_KEY AGENTCAD_KEY_ID AGENTCAD_KEY_SECRET
```

Local removal does not revoke other copies. Key revocation is separate from
cleanup/deletion of retained designs. Read the current
[privacy policy](https://agentcad.dev/privacy), [terms](https://agentcad.dev/terms)
and shared guide for preview limits, retained outputs and deletion requests.
