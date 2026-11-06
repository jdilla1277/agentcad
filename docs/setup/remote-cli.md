# Remote CLI: build on your host

Complete [cloud access](cloud-access.md), including operator approval, and load
the three issued environment variables. Install Python 3.10–3.12 first. Even
though CAD executes remotely, this package also installs local CAD dependencies.

```sh
mkdir cloud-example
cd cloud-example
python3.12 -m venv .venv
.venv/bin/python -m pip install 'agentcad[cloud]==0.7.2'
.venv/bin/python -m pip check
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
.venv/bin/agentcad cloud check-spec PROJECT VERSION spec.json
.venv/bin/agentcad cloud render PROJECT VERSION
```

Inspect, check-spec, and render each return a **different job**. Poll each
returned ID, then retrieve its evidence:

```sh
.venv/bin/agentcad cloud job PROJECT CHECK_JOB --wait
.venv/bin/agentcad cloud download PROJECT first-report.json --job CHECK_JOB --kind report
.venv/bin/agentcad cloud job PROJECT RENDER_JOB --wait
.venv/bin/agentcad cloud download PROJECT first.png --job RENDER_JOB --kind render
```

Read the report's `passed` verdict separately from job completion and geometry
validity. Open the actual PNG and review the STEP. The pilot's export is STEP;
it does not provide remote STL/OBJ/GLB export jobs or an interactive cloud viewer.

## Edit and recover

Submit `edited.py` from the example with a new label/key, poll it, check its
returned version against `edited-spec.json`, and compare actual versions:

```sh
.venv/bin/agentcad cloud run PROJECT edited.py --label edited \
  --idempotency-key bracket-edit
.venv/bin/agentcad cloud diff PROJECT ORIGINAL_VERSION EDITED_VERSION
```

Poll the comparison job and preserve its method, direction, and unavailable or
approximate status. Download the edited build's STEP and render for delivery.

After a lost submission response, repeat **identical** source/options with the
same key. Keep the source file unchanged for that retry. A new key means new
work; an accepted job should be polled instead. After a lost create-project
response, list projects before creating another. Downloads preserve existing
files; choose a new destination or explicitly use `--overwrite`.

Missing configuration requires setting the issued environment variables in this
process. A 401 requires checking credentials with the operator. A 404 can mean
the selected project/job is wrong or unavailable to this account; verify both
returned IDs before resubmitting. For limits, verdicts, and MCP variants, see
the [cloud workflow](../cloud-workflow.md). To disconnect or revoke access,
follow [credential cleanup and revocation](cloud-access.md#replacement-and-revocation).

An accepted job has occasionally returned a temporary 404 during polling.
Preserve its project/job IDs and retry that same read; contact the host if it
persists. A missing poll response does not establish that the job never started.
