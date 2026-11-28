# API: unattended build and private download

Complete [cloud access](cloud-access.md): create your own named API key at
[agentcad.dev/account/api-keys](https://agentcad.dev/account/api-keys). Production
uses **`https://api.agentcad.dev`**. Privately load `AGENTCAD_API_KEY` and set:

```sh
export AGENTCAD_API_URL='https://api.agentcad.dev'
```

Requests use `Authorization: Bearer <API key>`. No interactive browser session is
needed per job. The [shared cloud guide](https://agentcad.dev/docs/cloud/first-cloud-build)
maintains the production account/key and HTTP onboarding instructions; this
Python example uses the same service for the common local/cloud CAD task.

You need an HTTPS client, not a local CAD engine. This example uses Python 3.12
and HTTPX; `agentcad` is not required:

```sh
mkdir api-example
cd api-example
python3.12 -m venv .venv
.venv/bin/python -m pip install 'httpx==0.28.1'
```

Save the [example files](example.md) and the following as `build.py`.
The script selects `AGENTCAD_PROJECT_ID` when supplied; otherwise it lists
projects and exits so you can explicitly select one. It never creates a project
silently. Set `AGENTCAD_RETRY_KEY` **once per intended submission** and preserve
it after a lost response.

```python
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

origin = os.environ["AGENTCAD_API_URL"].rstrip("/")
parts = urlsplit(origin)
if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
        or parts.path or parts.query or parts.fragment):
    raise ValueError("Use an HTTPS API origin without a path or credentials")
headers = {"Authorization": "Bearer " + os.environ["AGENTCAD_API_KEY"]}

def get_json(client, path):
    # Use same-origin relative paths. Do not forward secrets to another host.
    if not path.startswith("/v1/") or path.startswith("//"):
        raise ValueError("Expected a versioned API path")
    return client.get(path).raise_for_status().json()

with httpx.Client(base_url=origin, headers=headers, timeout=60,
                  follow_redirects=False) as client:
    if sys.argv[1:] == ["--create-project"]:
        created = client.post("/v1/projects", json={
            "name": "Bracket", "runtime": "build123d"
        }).raise_for_status().json()
        print(json.dumps(created, indent=2))
        print("Use this project ID on the next invocation")
        raise SystemExit(0)
    project = os.environ.get("AGENTCAD_PROJECT_ID")
    if not project:
        print(json.dumps(get_json(client, "/v1/projects"), indent=2))
        print("Set AGENTCAD_PROJECT_ID to a returned project ID")
        raise SystemExit(0)
    get_json(client, f"/v1/projects/{project}")
    retry_key = os.environ["AGENTCAD_RETRY_KEY"]
    source = Path(os.environ.get("AGENTCAD_SCRIPT", "bracket.py"))
    label = os.environ.get("AGENTCAD_LABEL", "first")
    with source.open("rb") as content:
        job = client.post(
            f"/v1/projects/{project}/runs",
            headers={"Idempotency-Key": retry_key},
            data={"label": label},
            files={"file": (source.name, content, "text/x-python")},
        ).raise_for_status().json()
    print("Job:", job["id"], flush=True)
    Path("job.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
    deadline = time.monotonic() + 2100
    while job["status"] in {"queued", "running"}:
        if time.monotonic() >= deadline:
            raise TimeoutError("Keep job.json; poll this same job later")
        time.sleep(5)
        # Retry explicitly retryable reads; never submit another build here.
        try:
            next_job = get_json(client, job["poll_url"])
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {429, 502, 503, 504}:
                continue
            raise
        job = next_job
        Path("job.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
    if job["status"] != "succeeded":
        raise RuntimeError(f"Job failed: {job.get('reason')}")
    measurement = get_json(client, job["measure_url"])
    print(json.dumps(measurement, indent=2))
    if measurement.get("delivery_readiness", {}).get("ready") is not True:
        raise RuntimeError("Not ready for delivery; inspect geometry/spec blockers")
    path = job["download_url"]
    if not path.startswith("/v1/") or path.startswith("//"):
        raise ValueError("Expected a private API download path")
    response = client.get(path).raise_for_status()
    destination = Path(f"{project}-v{job['version']}.step")
    with destination.open("xb") as output:
        output.write(response.content)
    print("STEP:", destination)
```

Run `.venv/bin/python build.py` to list projects when none is selected. For a
new design, run `.venv/bin/python build.py --create-project` once and keep its
returned `id`; if that response is lost, list projects before creating another.
For an existing design, select its returned ID directly. Set
`AGENTCAD_PROJECT_ID` to that ID and a unique `AGENTCAD_RETRY_KEY`, then run
`.venv/bin/python build.py` to build. These two variables are nonsecret
references; keep credential values in your secret store.

## Check, render, edit, and deliver

Every route below needs the same credential headers. Substitute actual
`PROJECT`, `VERSION`, and `JOB` values;
[production OpenAPI](https://api.agentcad.dev/openapi.json) describes
the deployed API.

| Action | Request |
| --- | --- |
| Inspect | `POST /v1/projects/PROJECT/versions/VERSION/inspect` with `{"summary":true}` |
| Check | `POST /v1/projects/PROJECT/versions/VERSION/check-spec` with the contents of `spec.json` |
| Render | `POST /v1/projects/PROJECT/versions/VERSION/render` with `{}` |
| Compare | `POST /v1/projects/PROJECT/diff` with `{"from_version":1,"to_version":2}` using your actual versions |
| Poll | `GET /v1/projects/PROJECT/jobs/JOB` |
| Read report | `GET` the completed job's returned `result_url` |
| Read measurement | `GET` the build job's returned `measure_url` |
| Download | `GET` the completed job's returned private `download_url` |

Use a different `Idempotency-Key` for each intended POST. Poll the same returned
job; a 202 is acceptance, not completion. For a check, read its report's
`geometry_valid` and `passed` separately from job status. Retrieve the render's
actual PNG and the spec JSON report. Require `delivery_readiness.ready` and
passing task checks before delivery.
Saved-version STL/OBJ/GLB exports are available at
`POST /v1/projects/PROJECT/versions/VERSION/exports`; consult OpenAPI for the
request, poll the job and inspect each mesh's validation. Hosted browser
review/comments are not available.

To edit, use `edited.py`, a new label and a new retry key, check the returned
version with `edited-spec.json`, then compare the actual original/edited
versions. Preserve comparison limitations and review the PNG/STEP. Do not
deliver a generated picture as proof of validity or acceptance.

## Recover and revoke

A timed-out or failed verification leaves the model saved but blocks delivery.
After diagnosis, submit the same specification with a new verification key;
replaying the failed key returns that failure. Do not rebuild a valid model
just to retry its check.

If a POST response is lost, repeat identical inputs with its saved key. If
polling fails, keep `job.json` and retry that job's read instead of submitting
again. A reused key with changed input conflicts. Local destination files are
opened exclusively; choose another filename instead of overwriting evidence.

The example retries explicitly retryable polling statuses within its deadline.
A genuine missing-job or cross-account 404 remains an error. On network failure,
keep `job.json` and resume reading its `poll_url` with the same credentials;
do not create a new submission merely because a read failed.

The example saves a ready STEP for subsequent task checks; the download
alone is not final delivery approval. It buffers the STEP in memory for this
small fixture. Use a bounded streaming download for larger production files.
Scripts/specs are limited to 256 KiB and imported STEP to 20 MiB. Do not follow
redirects with credentials or fetch a different origin with these headers.
For 401, check the key, revocation and matching service environment; for 404,
verify the project
and job IDs and account access. Removing local credentials is not server-side
revocation. Follow [key replacement/revocation](cloud-access.md#replacement-and-revocation)
for unattended integrations and stop processes that retain old credentials.
