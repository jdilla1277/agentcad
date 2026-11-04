# Retaining inspection reports and images

Inspection analysis, image sheets, catalogues and completed job result payloads
share a **512 MiB cache per selected build root**, with a **30-day** default age
limit. Configure positive integer limits in the source project's `agentcad.toml`:

```toml
[inspection.cache]
max_age_seconds = 2592000
max_bytes = 536870912
```

The policy applies to imported and generated baselines. Inspection reports and
individual images return `retention`: its state, expiry, possible earlier
byte-pressure eviction, applied limits and bytes protected for resumable jobs.
Reports use one conservative deadline for the artifacts they reference. Reading
an artifact does not refresh its lifetime; increasing the configured age does
not extend an existing unprotected deadline. Reducing it can shorten retention.

Source files, normalized STEP/BREP, the issued geometry-ID manifest, edit
scripts, acceptance receipts, import retry records and job request/checkpoint
records are preserved. Cleanup removes only recognized derived artifact names
inside registered inspection directories. It adopts existing version caches
when the selected build root is first inspected. The checked cache index has a
separate 4 MiB metadata allowance.

## Eviction and fresh inspection

Publication and retrieval remove expired groups first, then the oldest groups
until the retained bytes fit. A PNG and its JSON sidecar form one group. Complete
reports and completed job result payloads can be evicted. A shared process lock
protects publication, reads and cleanup; MCP copies the selected PNG bytes while
holding that lock. Abandoned render and publication scratch files are reclaimed
on the next access. Rendering uses temporary working space in addition to the
retained-byte limit; one managed renderer runs per build root.

An expired or missing historical job result fails explicitly with a fresh-report
action. A saved response can still be retrieved within its own snapshot lifetime,
but its image references may have expired separately. MCP then reports attachment
failure rather than silently replacing those images with new renders. Request a
fresh `inspect --report` or `inspect --id ID --image` to rebuild derived artifacts
against the same baseline and issued IDs.

```sh
agentcad inspect v1_supplier/output.step --report
agentcad inspect v1_supplier/output.step --id face_v1_1 --image
```

The fresh request creates current evidence. It does not recover an evicted
historical payload. Retained payloads and ID mappings are never retargeted.

## Jobs and limited disk space

Queued, running, stopped, failed, canceled and resource-limited jobs protect the
completed artifacts needed to resume. Their bootstrap/checkpoint JSON also
counts toward the quota. The protected state has no expiry until those jobs
complete; its retention clock starts when that protection is released. An
actual recorded completion time starts that clock, including after a restart.
ordinary read does not release or renew a job's protection. The remaining job
history is retained independently after completion; its derived result payload
can be evicted.

If protected work leaves insufficient room, a worker stops with
`inspection_job.status=resource_limit`, an explanation and its existing resume
action. It is no longer doing CAD work. The imported baseline stays usable and
successful. Increase `max_bytes` and follow `inspection_job.actions.resume` to
continue the same job/version without recomputing completed checkpoints.
Direct report/image requests return `inspection_cache_resource_limit` and a
retry action. Corrupt protected checkpoint bytes require restoration before
resumption; another request cannot overwrite that evidence.

```sh
agentcad inspect v1_supplier/output.step --report --job RETURNED_JOB_ID \
  --job-action resume --build-dir /project/build
```

Selecting `--build-dir` contains derived artifacts for an external standalone
STEP inside that build root. A legacy standalone request that writes beside a
file outside the selected build root returns `retention.state=unmanaged` with
this guidance. Its shared source-side cache is outside this project's cleanup.
Run the same installed AgentCAD version for cooperating cache writers; older
processes do not participate in the retention lock.

Saved full-response JSON has its own [snapshot limits](inspection-budgets.md).
Search-page storage and remote artifact retrieval remain separate capabilities.
