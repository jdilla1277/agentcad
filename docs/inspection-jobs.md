# Local inspection jobs

Import waits for inspection by default. Its response now includes
`inspection_job`: the baseline, analysis revision, configuration, stage progress
and executable status/wait/resume/cancel actions. The source STEP and issued
geometry IDs stay unchanged when analysis stops or resumes.
Inline stages show status and completed-artifact counts; `checkpoints` points
to the complete saved paths and hashes without repeating them in every response.

For a large import, choose a background worker or a bounded inspection wait:

```sh
agentcad import supplier.step --init --idempotency-key supplier-001 --inspection-background
agentcad import supplier.step --init --idempotency-key supplier-001 --inspection-wait-timeout 2
```

These are alternative wait controls. They do not limit parsing, validation,
STEP export or legacy preview/viewer/diff work. A successful import remains
successful if inspection is unfinished or failed. Its `inspection_job` tells
you whether work is queued, running, stopped, failed, canceled, resource-limited
or complete.
The report/gallery is included only when available. Recognition currently
reports `not_available`, with a null recognized-feature count.

Keep the original import options, bytes and key on retry. Wait controls can
change without a conflict, but the retry returns the original response and
job reference, including its original progress snapshot. Follow the job's
status action for fresh progress; retry does not automatically resume stopped
analysis or start another import.

## Inspecting an existing version

Both imported and script-generated baselines support the same job workflow:

```sh
agentcad inspect v1_part/output.step --report --background
agentcad inspect v1_part/output.step --report --wait-timeout 2
agentcad inspect v1_part/output.step --report --job inspection_RETURNED_ID --job-action status
agentcad inspect v1_part/output.step --report --job inspection_RETURNED_ID --job-action wait --wait-timeout 30
agentcad inspect v1_part/output.step --report --job inspection_RETURNED_ID --job-action resume
agentcad inspect v1_part/output.step --report --job inspection_RETURNED_ID --job-action cancel
```

Copy the actual `inspection_job.actions` commands. They contain the complete
ID, absolute STEP path and selected build root, so they work from a project
subdirectory too. `--job` uses the saved image configuration; omit
`--no-images` and `--max-images`. Controls also reopen standalone jobs beside
an external baseline, keeping their original output location when the returned
command makes the build root explicit. To request a different image configuration,
start a report without `--job`. `--text-budget` changes report delivery without
creating another job. Plain `inspect --report` retains its existing flat,
synchronous response and can regenerate missing derived image caches.

Job controls return an envelope with `inspection_job`, `wait_expired`, and a
nested `inspection` report when ready. Polling (`status`) returns progress
without attaching a gallery. A wait deadline stops only that caller's wait;
the detached worker continues after the CLI or MCP call ends. No background
service or open-source-external dependency is required.
Full waits finish after the worker releases execution ownership. A deadline can
return earlier with `wait_expired=true` and the current `worker_running` value.

Cancellation is cooperative. During a native stage, `cancel_requested` can be
true while `worker_running` remains true. The current stage may finish; the
worker then stops before further stages. Finished artifacts remain available,
and explicit `resume` continues the same job. Status/cancel controls return
exit code zero when successful; waiting for failed, stopped, canceled or resource-limited work
returns nonzero. A partial image failure can return its useful report alongside
a failed job and a resume action.

## MCP equivalents

```python
import_cad(file="supplier.step", cwd="/project", initialize=True,
           idempotency_key="supplier-001", inspection_background=True)
inspect(file="/project/v1_supplier/output.step", cwd="/project", report=True,
        job_id="inspection_RETURNED_ID", job_action="wait", wait_timeout=30)
```

`inspection_wait_timeout` controls import waiting; `background` and
`wait_timeout` control inspection. The actions are `status`, `wait`, `resume`
and `cancel`. Completed job retrieval attaches the PNGs named by the nested
report, with the same hash checks as ordinary inspection.

## Checkpoints and limits

Jobs are bound to the project/build root, exact geometry manifest and STEP,
analysis/renderer revisions, frozen quality evidence and image configuration.
Concurrent identical requests share one worker. Analysis, individual sample
images and the delivery catalogue have persistent checkpoints. After worker
exit or process death, OS lock ownership distinguishes stopped work from a
still-running stage. Resume verifies completed artifact hashes and reuses the
existing analysis/image caches rather than repeating finished native work.
A worker that has been queued but has not started within 30 seconds becomes
stopped and can be resumed. Running work has no stale-time cutoff.

Changed software or an incompatible baseline cannot silently resume an old
job. Request a new report after software/configuration changes; geometry IDs
are never rewritten. Missing, altered or misplaced job/checkpoint records fail
explicitly. Restore the original evidence before relying on that job; do not
delete job records as a recovery strategy. Jobs and logs currently remain on
the execution filesystem. [Cache limits](inspection-cache.md) protect resumable
checkpoints, stop workers with `resource_limit` when protected bytes fill the
quota, and allow completed derived results to expire. Increase the quota and
follow the same resume action. [Delivery budgets](inspection-budgets.md) bound
full import/report responses by default; `--text-budget` separately bounds the nested
report. Remote artifact transfer remains separate work.
