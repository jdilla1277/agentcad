# Friction Log — Export Journeys (2026-03-08)

**Tester:** Claude Code (AI agent)
**Scope:** 3 journeys testing `--export stl,glb` flag: basic mesh export, combined render+export, default behavior
**Overall verdict:** Clean sweep. All three journeys passed with zero friction. The export pipeline is well-implemented.

---

## Journey 1: Basic Mesh Export

```
cadtool init --name "mesh-test"
cadtool run box.py --output box --export stl,glb  → success
```

### Files produced

```
v1_box/
  output.step   15,504 bytes  (STEP — always produced)
  output.stl       684 bytes  (ASCII STL, Open CASCADE)
  output.glb     3,256 bytes  (GLB v2, valid glTF magic bytes)
  script.py                   (script copy)
  meta.json                   (metadata)
```

### JSON response

```json
{"command": "run", "status": "success", "version": 1, "label": "box",
 "outputs": {"step": "v1_box/output.step", "script": "v1_box/script.py",
             "stl": "v1_box/output.stl", "glb": "v1_box/output.glb"}}
```

### meta.json

```json
"outputs": {
  "step": "v1_box/output.step",
  "stl": "v1_box/output.stl",
  "glb": "v1_box/output.glb"
}
```

**Result: Clean pass.** Both mesh formats appear in the version directory alongside the STEP file. JSON response and meta.json both include all output paths. File formats validated:
- GLB starts with `glTF` magic bytes (binary glTF v2)
- STL is ASCII format with Open CASCADE header

No friction at all.

---

## Journey 2: Combined Render + Export

```
cadtool run box.py --output filleted_box --export glb --render iso  → success
```

### Files produced

```
v1_filleted_box/
  output.step    31,851 bytes
  output.glb      8,296 bytes
  renders/
    iso.png        5,972 bytes
  script.py
  meta.json
```

### JSON response

```json
{"command": "run", "status": "success", "version": 1, "label": "filleted_box",
 "outputs": {"step": "...", "script": "...", "glb": "..."},
 "renders": {"iso": "..."}}
```

**Result: Clean pass.** `--export` and `--render` compose correctly in a single command. The GLB for a web/Preview viewer and the PNG for agent inspection both appear. Response JSON separates them cleanly — `outputs` for files, `renders` for images.

Note: the filleted box GLB is 8KB vs 3KB for the plain box, which makes sense — fillets add tessellation triangles. Good sign that the mesh is actually tessellating the exact geometry, not a bounding box.

---

## Journey 3: Default Behavior (No --export)

```
cadtool run box.py --output plain  → success
```

### Files produced

```
v1_plain/
  output.step
  script.py
  meta.json
```

### JSON response

```json
{"command": "run", "status": "success", "version": 1, "label": "plain",
 "outputs": {"step": "v1_plain/output.step", "script": "v1_plain/script.py"}}
```

### meta.json

```json
"outputs": {"step": "v1_plain/output.step"}
```

**Result: Clean pass.** No stray mesh files. JSON and meta.json only reference step and script. The `--export` flag is purely opt-in — default behavior is unchanged.

---

## Files copied to Desktop for manual testing

- `~/Desktop/filleted_box.glb` — filleted box, should open in macOS Preview or any glTF viewer
- `~/Desktop/box.stl` — plain box, can open in any mesh viewer

Worth checking: does the GLB actually open in macOS Preview? The PRD says GLB opens natively in Preview with 3D spin. If Preview can't handle it, that's a PRD assumption to revisit (same issue as STEP — Preview may need a specific glTF/GLB QuickLook generator).

---

## Summary

### Issues found

None. This is the cleanest batch of journeys yet.

### What's working well

1. **`--export stl,glb` produces valid files** — correct magic bytes, reasonable file sizes
2. **`--export` and `--render` compose** — both flags work together in a single command
3. **Default behavior preserved** — no export flag means no mesh files, no extra JSON keys
4. **meta.json tracks all outputs** — consistent with JSON response, includes export paths
5. **File naming is clean** — `output.step`, `output.stl`, `output.glb` in the same directory

### Open question

Does the GLB actually open in macOS Preview? If not, the "human review via Preview" workflow from the PRD needs a fallback (Autodesk viewer worked for STEP, likely works for GLB too).
