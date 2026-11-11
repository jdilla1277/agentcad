# Connect a CLI installation

Local CAD commands do not require an account. Connecting authorizes this CLI
installation to use your private cloud workspace.

Install the cloud adapter, then start from your terminal:

```sh
pip install 'agentcad[cloud]'
agentcad account connect --name "My laptop"
```

Open the returned approval link if the browser did not open. Sign up or sign in,
complete workspace setup if needed, check the installation name and permissions,
and choose **Approve installation**. Then return to the terminal:

```sh
agentcad account status
agentcad cloud projects list
agentcad cloud projects create --name Bracket
```

`connect` initially returns `connection: "approval_pending"`, which is not cloud
access. `status` finishes an approved connection and reports `connected`. Repeating
commands reuses the pending request; it does not create another account. Finish
approval within 15 minutes. For an expired request, clear local pending state with
`agentcad account disconnect --forget`, then start `connect` again.

`--no-browser` returns the link for approval on another device. The URL contains a
request reference, not the private proof held by the CLI. Approve only requests
you started. The browser account approving the request determines the workspace.
Signing in alone does not approve an installation.

The CLI stores its credential in a private file (user-only permissions), outside
project directories, at `~/.config/agentcad/account.json`. It reports
`credential_storage: "private_file"`. Never copy this file into a project, prompt,
or issue. Credentials do not appear in command output. Use
`AGENTCAD_ACCOUNT_STATE=/absolute/private/path/account.json` to select an isolated
profile, and `AGENTCAD_ACCOUNT_URL=https://your-account-host.example` for a host
that has enabled account connections. Profiles bind both account and cloud API
origins. An API URL override cannot send a saved credential to another host.

For a preview deployment, use its account URL and a separate state file. Account
connection must be enabled by that host; installing this CLI does not enable the
service or activate production signup.

## Revoke and reconnect

The website account page lists approved CLI installations. **Revoke installation**
blocks subsequent status checks and cloud requests. Jobs already submitted may
continue. Reconnect with a new approval request after clearing the old profile.

```sh
agentcad account disconnect
```

Ordinary disconnect revokes the installation online before clearing local state.
If the service is unavailable, it preserves state so revocation can be retried.
`disconnect --forget` only clears local state; revoke any approved installation
on the website as well. Browser sign-out and CLI disconnect are separate actions.

An interrupted approval or exchange can have succeeded. Retry `account status`
with the same state file rather than creating another account. Temporary service
failures preserve saved state. A revoked or expired connection needs a new
request; local CAD work continues normally.

Existing pilot credentials remain supported through `AGENTCAD_API_URL`,
`AGENTCAD_KEY_ID`, and `AGENTCAD_KEY_SECRET`. Explicit pilot key settings take
precedence over a saved installation. Hosted MCP OAuth and personal API-key
management are separate journeys.
