# Experimental local geometry search

Search the geometry in one saved import or run version through the local CLI
or local MCP `inspect` tool. Every search, page and search-manifest read requires
`--experimental-geometry-search` (MCP: `experimental_geometry_search=true`).
This flag applies to that invocation only. Ordinary Inspect remains available
without opt-in. The deprecated `--experimental-inspect` flag does not enable
search. The hosted `cloud_*` tools do not expose this feature.

Use the saved `outputs.step` that has a companion `geometry_manifest.json`.
Search never re-imports geometry or creates CAD versions. IDs retain their
existing baseline scope; a result can be looked up with `inspect --id ID`,
highlighted with `--id ID --image`, and selected through `load_geometry`.
Faces and edges with exclusive occurrence membership return executable
`geometry.pick_face(...)` / `geometry.pick_edge(...)` selectors. Other results
explicitly require native selection. First load `geometry = load_geometry(path)`;
select from `geometry.shape`, and re-inspect the new version after saving edits.

```sh
agentcad inspect v1_supplier/output.step --experimental-geometry-search \
  --query '{"kind":"face","where":{"surface_type":"cylinder","radius":{"value":4,"units":"mm","tolerance":0.01}}}'

# No images; at most ten results per page, subject to the text allowance.
agentcad inspect v1_supplier/output.step --experimental-geometry-search \
  --query '{"kind":"edge","where":{"length":{"min":10,"units":"mm"}}}' \
  --search-images 0
```

The identical MCP query is an object, not an encoded JSON string:

```python
inspect(file="v1_supplier/output.step", cwd="/project",
        experimental_geometry_search=True, search_images=0,
        query={"kind": "face", "where": {
            "surface_type": "cylinder",
            "radius": {"value": 4, "units": "mm", "tolerance": 0.01}}})
```

## Query contract

The top-level object accepts only `kind` (required), `where` (default `{}`),
and `result_mode` (default `occurrences`). `kind` is `part`, `occurrence`,
`solid`, `face`, or `edge`.

`part` searches individual placed leaf parts and returns **occurrence IDs**.
`occurrence` also includes assembly occurrences. All geometry filters run in
assembly coordinates. `result_mode: "definitions"` is available for part and
occurrence queries: filter placements first, then group matches by part ID.
Grouped records contain matched occurrence IDs and `distinct_occurrence_count`;
their reported dimensions/location use definition-local coordinates. Grouped
search images highlight one representative **matched** occurrence, with its
`target_id` and scope explicit. Ordinary `inspect --id PART_ID --image` shows
all placements of that definition, including placements outside the query.

Fields within a `where` object are ANDed. Compose arbitrarily using
`all: [where, ...]`, `any: [where, ...]`, and `not: where`.
Queries are limited to 64 KiB, 100 filter objects and 12 nesting levels.
Unknown fields, duplicate JSON keys, unsupported units, nonfinite numbers,
and invalid or unknown baseline IDs fail explicitly. Filters are never relaxed.

| Filter | Value | Applies to |
| --- | --- | --- |
| `id` | Typed ID or list of IDs | All kinds; IDs are result identities |
| `part_id` | Part ID or list | All; assembly definitions include descendants |
| `occurrence_id` | Occurrence ID or list | All; assembly occurrences include descendants |
| `surface_type` | String or list | Faces |
| `curve_type` | String or list | Edges |
| `area` | Measurement | Faces |
| `length` | Measurement | Edges |
| `volume` | Measurement | Parts, occurrences, solids |
| `radius`, `diameter` | Measurement | Circular edges; cylindrical/spherical faces |
| `dimensions` | Object containing `x`, `y`, and/or `z` measurements | All; axis-aligned bounding dimensions |
| `bounds` | Explicit bounding-box predicate | All |
| `location` | Origin, units and tolerance | All; distance to bounding-box center |
| `placement` | Origin, units, tolerance, optional rotation matrix | Parts and occurrences |
| `orientation` | Direction, optional angular tolerance and sense | Planar face normal; analytic face/edge axis |
| `axis` | Origin, direction, units, distance/angular tolerance | Analytic faces/edges with an axis |
| `plane` | Origin, normal, units, tolerance, explicit relation | All |
| `relation` | Explicit relation to another baseline ID | All |

Measurements use `{"min": 1, "max": 5, "units": "mm"}` (either bound may
be omitted) or `{"value": 4, "tolerance": 0.01, "units": "mm"}`.
Tolerance is nonnegative and defaults to zero. Bounds are inclusive, extended
by the stated tolerance. Use explicit `mm`, `cm`, `m`, or `in`; area requires
`mm2`/`cm2`/`m2`/`in2`, and volume requires the corresponding `3` suffix.
Applied queries echo normalized mm-based units. Measurements inherited from
the geometry manifest are rounded to four decimal places; choose a tolerance
appropriate to the source precision. These measurements do not establish
physical absence of a named design feature.

Surface types: `plane`, `cylinder`, `cone`, `sphere`, `torus`, `bezier`,
`bspline`, `revolution`, `extrusion`, `offset`, `other`.
Curve types: `line`, `circle`, `ellipse`, `hyperbola`, `parabola`, `bezier`,
`bspline`, `offset`, `other`, `degenerate`. Degenerate edges have no analytic axis or radius.
An analytic predicate with no applicable property is false (for example,
`radius` on a planar face). A computation failure is recorded separately and
makes filter coverage partial. `not` negates the predicate, including false
predicates on inapplicable analytic properties.

```json
{
  "kind": "face",
  "where": {
    "all": [
      {"surface_type": "plane"},
      {"area": {"min": 100, "units": "mm2"}},
      {"orientation": {"direction": [0, 0, 1], "sense": "same", "angle_tolerance_deg": 1}}
    ]
  }
}
```

Additional predicate forms:

```json
{"bounds":{"min":[0,0,0],"max":[20,30,40],"relation":"contained","units":"mm","tolerance":0.01}}
{"location":{"origin":[10,20,0],"units":"mm","tolerance":0.1}}
{"placement":{"origin":[100,90,0],"units":"mm","tolerance":0.01}}
{"axis":{"origin":[0,0,0],"direction":[0,0,1],"units":"mm","tolerance":0.01,"angle_tolerance_deg":1}}
{"plane":{"origin":[0,0,5],"normal":[0,0,1],"units":"mm","tolerance":0.01,"relation":"coplanar"}}
{"relation":{"type":"adjacent","to":"face_v1_1"}}
{"relation":{"type":"distance","to":"face_v1_1","distance":{"max":2,"units":"mm"}}}
{"relation":{"type":"coaxial","to":"face_v1_1","units":"mm","tolerance":0.01,"angle_tolerance_deg":1}}
```

`bounds.relation` is `contained` or `intersects`; it tests bounding boxes,
including contact. `plane.relation` must be `coplanar` (the entire candidate
lies in the plane within tolerance) or `intersecting` (the actual candidate
touches/crosses the plane within tolerance). A disconnected candidate whose
box straddles a plane does not automatically intersect it.

`orientation.sense` is `same` or `either` (default); angular tolerance defaults
to 0.000001 degrees, with a maximum of 90. Plane normals respect face
orientation; analytic axes are geometric axes. `axis` checks parallel direction
and perpendicular axis separation. `relation.type: "shared_axis"` is an alias
for `coaxial`. Minimum `distance` uses actual geometry, not box centers.
`adjacent` requires shared boundary topology: vertices for edges, edges for
faces, faces for solids/occurrences. Coincident but topologically separate
parts are not adjacent. A candidate is not adjacent to itself. Optional
`placement.rotation` is a row-major 3×3 matrix matched within 1e-9 per entry;
its translation is measured in the stated units.

## Results, pages and images

The response echoes baseline identity, normalized query, analysis/index
revisions, result mode/frame and filter coverage. `match_count_complete` is
false if any candidate could not be evaluated; failure IDs/reasons remain in
the complete candidate manifest. Zero matches preserve the applied filters
and provide refinement suggestions. Recognition remains `not_available`,
with `recognized_feature_count: null`.

Exact queries perform zero shape-similarity comparisons. `ranking_mode` is
`not_applicable`, and `ranking_complete` and `comparison_counts` are null.
Results use deterministic **lexicographic public-ID order**, without scores.
`filter_coverage.eligible = evaluated + failed + pending`; match counts are
separate. Definition grouping reports matched occurrence counts separately.

`--page-size` / `page_size` defaults to 10, with a maximum of 100. Default
delivery allows 2,000 estimated tokens for the entire JSON, including a
32-token transport reserve. Tail results are reduced as needed; always follow
`page.next.cli` or `page.next.mcp`, which carries the WIP flag and original build
root. The complete inventory remains available through
`candidate_manifest.retrieve`. Full grouped topology membership is in this
manifest; inline records summarize member counts and at most ten occurrence
IDs, with explicit truncation counts. These actions read base64 chunks with pinned
hash and expiry, without requiring direct access to the MCP filesystem.

`--text-budget` bounds search text; the existing `--response-text-budget`,
`--context-window`, `--response-budget`, `--image-budget`, `--vision-profile`
and MCP equivalents can tighten delivery. Combined budgets reserve text
first. Without a vision profile, constrained images are omitted. Unconstrained
image token cost is reported as unknown. `--full-response` explicitly removes
the text projection (page size and image caps still apply) and cannot combine
with caller delivery budgets. A budget too small for one result plus required
evidence returns an explicit error, never a falsely complete page.

The initial page prepares up to ten highlighted 1024×1024 four-view images.
Use `--search-images 0..10` / `search_images`, or `--no-images` (MCP:
`report_images=False`). Local MCP attaches verified PNGs in `media` order.
Images omitted by the text allowance remain in the saved candidate manifest.
Retained pages copy existing image bytes only; missing, expired or evicted
images are reported unavailable. They never silently regenerate historical
image evidence. Further targets can be viewed through a fresh ordinary
`inspect --id ID --image` request. Inspect image retention is separate from
search-page retention.

Use `--search-snapshot ID` to replay the first page, `--cursor CURSOR` for a
continuation, or `--search-snapshot ID --search-id ID` to retrieve one match.
MCP accepts `search_snapshot`, `cursor`, and `search_id`. Replays use the saved
query, delivery settings and candidate order without native CAD work. New
queries create new snapshots; they do not change old cursors.

## Retention

Candidate manifests and pages share a project/build-root cache, defaulting to
30 days and 64 MiB. They are separate from durable baselines, ID manifests,
inspection jobs, and report/image caches. Configure:

```toml
[inspection.search]
max_age_seconds = 2592000
max_bytes = 67108864
```

Expiry and possible earlier eviction under byte pressure accompany each
snapshot. Reads never extend its lifetime. Lowering the configured age can
shorten retention; continuations reject a changed expiry. An oversized single
manifest returns `search_resource_limit` before publication. Cleanup and reads
share a lock; the current read is protected while other snapshots are pruned.
An unavailable/expired/corrupt snapshot fails explicitly. Rerunning the original
query produces new evidence; it cannot recover the historical page.
