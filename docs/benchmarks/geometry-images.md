# ID-linked inspection images

Import prepares a labeled overall sheet at `outputs.inspection_image`, as part
of its [ranked inspection bundle](inspection-bundle.md). It is a 1024 × 1024 PNG
containing isometric, top, front and right views, assembly-frame dimensions in
millimetres, and at most eight sampled labels: up to four part definitions and
four measured face/edge samples. The complete inventory remains in
`geometry_manifest.json`. Named feature recognition remains separate work.

## Retrieve a target

Use IDs returned by the manifest or inspection, rather than guessing these
example IDs:

```sh
agentcad inspect v1_part/output.step --image
agentcad inspect v1_part/output.step --id face_v1_17 --image
agentcad inspect v1_part/output.step --id edge_v1_42 --image --isolate
```

`--image` highlights the target in orange through a transparent gray context;
the legend states that hidden geometry is shown through the model. `--isolate`
removes the context. The same ID identifies the record, label and build123d
baseline selector. Leader endpoints are points on the actual target; a face's
area centroid can fall in a hole, so it is not used blindly as a surface point.

Part definitions show all their placed occurrences. Occurrence IDs show one
instance and its descendants. Both use assembly coordinates, including when
isolated. Each response carries the model, target and framed dimensions
separately. The sidecar metadata records each leader's model point, projected
pixel and label box; this camera evidence stays out of the compact response.
Face/edge topology IDs do not imply recognized holes, pockets or other named
features.

To skip the default sheet, pass `--no-inspection-image` to import. Lazy retrieval
remains available afterward. A graphics failure leaves committed STEP, IDs and
the edit scaffold usable. On headless Linux, run under X11/GLX, for example
`xvfb-run -a agentcad inspect ... --image`.

MCP `inspect(image=True, entity_id="...", isolate=True)` returns the equivalent
record and an actual PNG image content block. The image bytes are delivered to
remote MCP clients too; CLI paths and sidecar paths still refer to the execution
filesystem. No remote artifact-storage service is introduced here.

## Integrity and reuse

Images are derived artifacts keyed by baseline manifest identity, image renderer
revision, target and display mode. Each retrieval verifies the manifest, STEP
bytes and CAD kernel, then verifies cached metadata and PNG hashes. Corrupt
derived images regenerate. Changed or incompatible baselines fail explicitly.
Image generation uses the same checked normalized topology as selectors and
verifies the baseline again before publication. It does not rewrite issued IDs.

## Narrow performance check

Measured on 2026-10-27, Apple M4, macOS 15.6, Python 3.12.12,
build123d 0.10.0 and cadquery-ocp 7.8.1.1.post1. The fixture is 1,000 placed
references to one box definition: 6,000 faces and 12,000 edges. This stresses
overlapping orthographic projections; it is not a complex machined assembly.

The slice's predeclared local smoke limits were 15 seconds for a first overall
sheet and 0.5 seconds for verified cached retrieval. The first implementation
took 23.95 seconds. Most of that time was rendering the right view, where all
1,000 boxes overlap. Enabling the renderer's depth prepass for these sheets
avoids shading the same screen region repeatedly. No geometry is dropped.

With that optimization, the first sheet took **5.15 seconds** and cached
retrieval **0.155 seconds**. The PNG was 47,108 bytes and compact image metadata
in the response was about 1.6 KB. These are one-process local measurements,
not cold-filesystem guarantees or complete import/report/delivery timings.
Fixture creation, import validation, recognition, legacy preview/viewer work,
MCP transport and peak process memory are unmeasured in this check. These
numbers do not establish the final effort's supported-size envelope.

Regression checks cover real CADGenBench and pump-manifold faces, rotated
repeated instances, exact edge/image/selector correspondence, MCP content
delivery, graphics failures, opt-out/retry and corrupted cache recovery. An
independent agent followed a non-sampled edge through CLI and actual MCP stdio,
edited it with the generated scaffold, and saved a valid model; Boolean checks
found no material added to or removed from its sibling occurrence.
