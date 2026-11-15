# Cloud access and credentials

The current hosted pilot requires an API origin, key ID, and secret issued by
your host. Contact the host/operator to request access and confirm the agreed
pilot dates, processing limits, and data retention before uploading a design.
If access is pending, wait for operator approval. Possessing credentials or
signing in to an agent provider does not establish permission to run CAD.

There is no self-service AgentCAD signup/login or approval-status command in
the published 0.7.2 pilot client. Ask the operator to confirm approval/status;
do not try an invented login command or treat an authentication-only read as
proof of compute approval. These instructions do not claim that the current
pilot implements the later self-service enrollment or cutoff flows.

Your host supplies these values through your secret store or process environment:

| Variable | Meaning |
| --- | --- |
| `AGENTCAD_API_URL` | Actual HTTPS API origin, without `/mcp`, a path, or credentials |
| `AGENTCAD_KEY_ID` | Issued key identifier |
| `AGENTCAD_KEY_SECRET` | Issued secret |

Pass them to the process that launches the CLI or local MCP server. Remote MCP
uses the two credential values as HTTP headers and a separately supplied `/mcp`
URL. The API uses `X-AgentCAD-Key-ID` and `X-AgentCAD-Key-Secret` on every
private request. This key pair is not a single `Authorization: Bearer` token.

Store secrets outside project files, prompts, URLs, and version control. Load
them from your existing secret manager; these guides do not require a custom
AgentCAD launcher. Restart the client/server after changing its environment.
The example hostname `api.example.test` is a placeholder, not a deployed service.
Use your issued origin; do not switch domains without the host's confirmation.

List existing projects and select a returned ID before creating a new design.
Project names are labels, not unique identifiers. Clients using the same
authorized account can continue a saved project and download its versions.
There is no automatic upload or synchronization of local folders/history.

## Replacement and revocation

The pilot has no self-service key list/rotate/revoke interface. Request a
replacement or revocation from the operator. Test the replacement on the same
project, then have the old pair revoked. Keep accepted job IDs and retry keys;
do not create another project just because credentials changed.

To stop a shell from using its loaded credentials:

```sh
unset AGENTCAD_API_URL AGENTCAD_KEY_ID AGENTCAD_KEY_SECRET
```

Also stop/reload any client or MCP process that inherited them. Remove the
remote MCP configuration when disconnecting that client. Local removal does
**not** revoke the issued key pair on the service or disconnect other copies.

Read the host's terms for operator access, the role of your LLM provider,
retention, deletion, usage records, and pilot expiry. These guides do not
promise an automatic trial, deletion schedule, paid conversion, or features
that have not been implemented and verified.
