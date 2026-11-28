# Remote CLI: build on your host

Complete [cloud access](cloud-access.md): use production API origin
`https://api.agentcad.dev` and privately load your personal `AGENTCAD_API_KEY`.
Install Python 3.10–3.12 first. Even
though CAD executes remotely, this package also installs local CAD dependencies.

```sh
mkdir cloud-example
cd cloud-example
python3.12 -m venv .venv
.venv/bin/python -m pip install 'agentcad[cloud]==0.8.2'
.venv/bin/python -m pip check
export AGENTCAD_API_URL='https://api.agentcad.dev'
unset AGENTCAD_KEY_ID AGENTCAD_KEY_SECRET
# AGENTCAD_API_KEY must already be loaded privately.
.venv/bin/agentcad cloud projects list
```

Select an existing project's returned `id`, or create one for a new design:

```sh
.venv/bin/agentcad cloud projects create --name Bracket --runtime build123d
```

No local `init` is needed. Save the [example files](example.md). Replace
`PROJECT` with the returned project ID and choose a unique key for this build:

```sh
.venv/bin/agentcad cloud run PROJECT bracket.py --label first \
  --idempotency-key bracket-first
.venv/bin/agentcad cloud job PROJECT BUILD_JOB --wait
```

Replace `BUILD_JOB` with the submission's `id`. Repeat that polling command
while `queued` or `running`; `--wait` waits up to 30 seconds per call. Once
`succeeded`, keep its actual `version` and download the STEP:

```sh
.venv/bin/agentcad cloud download PROJECT first.step --job BUILD_JOB
.venv/bin/agentcad cloud measure PROJECT VERSION
.venv/bin/agentcad cloud inspect PROJECT VERSION --summary
.venv/bin/agentcad cloud check-spec PROJECT VERSION spec.json --idempotency-key bracket-first-check
.venv/bin/agentcad cloud render PROJECT VERSION --idempotency-key bracket-first-render
```

Inspect, check-spec, and render each return a **different job**. Poll each
returned ID, then retrieve its evidence:

```sh
.venv/bin/agentcad cloud job PROJECT CHECK_JOB --wait
.venv/bin/agentcad cloud download PROJECT first-report.json --job CHECK_JOB --kind report
.venv/bin/agentcad cloud job PROJECT RENDER_JOB --wait
.venv/bin/agentcad cloud download PROJECT first.png --job RENDER_JOB --kind render
```

Require `delivery_readiness.ready` and read the report's `passed` verdict
separately from job completion and geometry validity. Open the actual PNG and
review the STEP. Cloud also supports saved-version STL/OBJ/GLB exports through
`agentcad cloud export`; check its help and each mesh's validation before
delivery. Hosted browser review/comments are not available.

## Edit and recover

Submit `edited.py` from the example with a new label/key, poll it, check its
returned version against `edited-spec.json`, and compare actual versions:

```sh
.venv/bin/agentcad cloud run PROJECT edited.py --label edited \
  --idempotency-key bracket-edit
.venv/bin/agentcad cloud diff PROJECT ORIGINAL_VERSION EDITED_VERSION
```

If a verification job fails or times out, keep the saved model. Diagnose the
check and submit the same specification again with a new verification key;
replaying the failed job key returns its saved failure. Rebuild only when the
design itself needs an edit. An incomplete check blocks delivery.

Poll the comparison job and preserve its method, direction, and unavailable or
approximate status. Download the edited build's STEP and render for delivery.

After a lost submission response, repeat **identical** source/options with the
same key. Keep the source file unchanged for that retry. A new key means new
work; an accepted job should be polled instead. After a lost create-project
response, list projects before creating another. Downloads preserve existing
files; choose a new destination or explicitly use `--overwrite`.

Missing configuration requires setting the origin and personal key in this
process. For 401, check the key and environment using
[cloud access](cloud-access.md). A 404 can mean
the selected project/job is wrong or unavailable to this account; verify both
returned IDs before resubmitting. For limits, verdicts, and MCP variants, see
the [cloud workflow](../cloud-workflow.md). To disconnect or revoke access,
follow [credential cleanup and revocation](cloud-access.md#replacement-and-revocation).

For a retryable service response, retain the same IDs and follow its retry advice.
A genuine missing-job or cross-account 404 remains an error; do not assume every
404 is transient. A polling failure does not establish that no work started.
