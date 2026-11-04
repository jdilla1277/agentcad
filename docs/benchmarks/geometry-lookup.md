# Imported geometry ID lookup

This benchmark measures the actual file verification and response work for one
typed ID. A dictionary lookup against an already loaded manifest does not
measure this path: each request reads and parses the manifest, serializes it
for its integrity digest, reads and hashes the normalized STEP, retrieves the
record, and scans the inventory to build the compact summary.

## Reproduce

Install the default CAD dependencies and MCP extra, then run from the checkout:

```sh
PYTHONPATH=src python scripts/benchmark_geometry_lookup.py --repeats 3
```

Pass STEP paths to measure other inputs. Setup preserves the assembly document
in normalized `output.step`, reloads it and issues a manifest. Setup time is
excluded. The benchmark selects the last face ID, checks the returned ID and
manifest identity on every request, and emits JSON. It retains all integrity
checks; there is no lookup cache.

The default inputs include two real workflow parts, 100 and 1,000 placed
references to one box definition, and a single part containing 150 disjoint
boxes (900 faces). The generated fixtures stress inventory size and repeated
instances; they do not represent complex machined parts or establish a limit
for arbitrary assemblies.

## What each measurement includes

- **Verified path:** `read_manifest → record → summary`, including both file
  reads, manifest parsing/digest, baseline hashing and summary traversal.
- **CLI dispatch:** the real Click command entry point, argument handling,
  verified lookup, JSON output and response parsing, within a prepared process.
- **MCP tool:** the actual MCP `inspect(entity_id=...)` function, including its
  CLI wrapper and JSON handling. MCP transport/client overhead is excluded.
- **Fresh CLI process:** a new `python -m agentcad inspect … --id … --no-daemon`
  subprocess per sample, including interpreter startup, imports and exit.

The first three modes import their entry points before timing. Each runs in its
own fresh worker, isolating its peak RSS from fixture construction and other
modes. Peak RSS includes imports and all requests in that worker, rather than
only allocations during lookup. On platforms without Python's `resource`
module, RSS is reported as null. Fresh CLI subprocess RSS is not measured.

The JSON includes the first request and subsequent median/maximum separately.
All requests verify the files again. A process's first request is not a cold
filesystem measurement: setup and prior modes can warm the OS file cache.
Validation, recognition, rendering, selection-context construction and report
generation are outside this benchmark.

## Measurements

Measured on 2026-10-27, Apple M4, macOS 15.6, Python 3.12.12,
build123d 0.10.0, cadquery-ocp 7.8.1.1.post1, MCP 1.30.0. Medians use three
requests after an initial request. Every fresh-CLI sample starts a new process.
[Raw JSON](geometry-lookup.json) records exact counts, file sizes, first-request
times, maxima, memory and environment.

| Input | Faces | Part occurrences | Verified path | CLI dispatch | MCP tool | Fresh CLI process |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CADGenBench 101 | 150 | 1 | 2.8 ms | 4.5 ms | 4.3 ms | 70.2 ms |
| Pump manifold | 118 | 1 | 3.0 ms | 4.3 ms | 4.5 ms | 83.3 ms |
| Repeated assembly, 100 instances | 600 | 100 | 8.5 ms | 10.2 ms | 10.3 ms | 79.4 ms |
| Repeated assembly, 1,000 instances | 6,000 | 1,000 | 139.2 ms | 139.6 ms | 123.9 ms | 222.1 ms |
| Multi-solid part | 900 | 1 | 20.0 ms | 21.8 ms | 21.9 ms | 96.1 ms |

Peak worker RSS, including imports:

| Input | Verified path | CLI dispatch | MCP tool |
| --- | ---: | ---: | ---: |
| CADGenBench 101 | 26.4 MiB | 32.7 MiB | 62.0 MiB |
| Pump manifold | 28.5 MiB | 31.6 MiB | 63.8 MiB |
| Repeated assembly, 100 instances | 32.7 MiB | 40.6 MiB | 69.8 MiB |
| Repeated assembly, 1,000 instances | 114.8 MiB | 121.9 MiB | 144.7 MiB |
| Multi-solid part | 48.5 MiB | 53.5 MiB | 80.7 MiB |

These measurements demonstrate growth with inventory size. The 1,000-instance
manifest is about 10.2 MiB even though its STEP is about 0.6 MiB: repeated
geometry is compact in STEP, while the manifest records each placed entity.
The measured path exceeds 100 ms at this size. No universal lookup-latency
guarantee follows from these samples. Future caching or indexing decisions
must preserve baseline, manifest and kernel verification.
