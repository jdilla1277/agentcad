# Friction Log: Designing Landing Gear with cadtool

**Task:** Design an aircraft landing gear assembly (bracket, strut, axle, two wheels) and export as GLB.

**Agent perspective:** Using cadtool as an AI agent to complete a real design task end-to-end.

---

## What Went Well

1. **Preamble is a huge win.** Not needing to import `cq` or `show_object` removes a common source of errors. The `cadtool docs preamble` command made it easy to check what's available. Smooth experience.

2. **`--preview` flag is great for iteration.** Getting a 256x256 PNG inline let me see the result immediately and decide to revise. Without this, I'd be flying blind.

3. **`--export glb` just works.** One flag, GLB appears. No fiddling with mesh settings or XCAF setup. This is exactly the right level of abstraction.

4. **Multi-`show_object()` → colored GLB.** Using separate `show_object()` calls for each part automatically gave each component a distinct color in the GLB. The auto-compound behavior with the warning message was clear and helpful.

5. **`cadtool render` for additional views.** Being able to render front/right/custom angles of the STEP file after the fact was useful for verifying the design without re-running the script.

---

## Friction Points

### F1: No way to position/transform parts in the assembly (Medium)

CadQuery's `.union()` is a boolean fuse — it merges geometry. For an assembly (landing gear with distinct bracket, strut, wheels), what I actually want is **spatial grouping without boolean ops**. I had to carefully position each part at absolute coordinates, which is error-prone.

**What would help:** A `cadtool.helpers.assemble()` or guidance in docs about using `cq.Assembly` vs `cq.Compound.makeCompound()` vs boolean union. When to use which isn't obvious.

### F2: Revolve/torus positioning is a CadQuery footgun (High)

Creating a torus (tire) at an arbitrary position required:
```python
cq.Workplane("XZ")
    .transformed(offset=(x_pos, axle_z, 0))
    .move(wheel_major_r, 0)
    .circle(wheel_tube_r)
    .revolve(360, (0, 0, 0), (0, 0, 1))
```

The interaction between `.transformed()`, `.move()`, and `.revolve()`'s axis specification is confusing. The revolve axis is relative to the *current* workplane origin, but `.move()` shifts the sketch without moving the origin. I got the first attempt wrong — the wheels were offset from the axle.

**What would help:** A helper like `torus(center, major_r, minor_r, axis)` that handles all the workplane gymnastics internally. Tori are common in mechanical design (tires, O-rings, seals, fillets).

### F3: `cadtool render` view spec parsing is strict (Low)

I tried `--view front,right,45,15` expecting it to render three views (front, right, and a custom 45,15 angle). It rejected the entire string because it mixes named views with angle specs.

**Current behavior:** Must be either all named views (`front,top`), `all`, or a single `azimuth,elevation` pair.

**What would help:** Allow mixing named and custom angle views in a single comma-separated list, e.g. `--view front,right,45:15` (using colon for angles to disambiguate from the comma separator).

### F4: No assembly/exploded view support (Medium)

For a multi-part design like landing gear, it would be valuable to:
- Render an exploded view showing how parts relate
- Export GLB with named parts/nodes (not just colored solids)
- Have a BOM (bill of materials) in the output JSON

This is a gap between "single part modeling" and "real mechanical design."

### F5: Metrics don't reflect multi-body intent (Low)

The metrics output gives a single volume/area/COM for the entire compound. For a multi-part assembly, per-part metrics would be more useful (e.g., "wheel: volume X, strut: volume Y"). The current metrics treat the assembly as one blob.

### F6: GLB files can't be previewed natively on macOS (High)

After exporting the GLB, I copied it to the Desktop for the user. They couldn't rotate or interact with it — macOS Quick Look doesn't support GLB, only USDZ. This means the export that's easiest for cadtool to produce (GLB via RWGltf_CafWriter) is the one that's hardest to casually view on a Mac.

**Options to consider:**
- Add `--export usdz` using Apple's Reality Converter CLI or xcrun tools
- Bundle a lightweight local HTML viewer that `cadtool` can `open` in the browser (e.g., a single-file three.js viewer that loads the GLB)
- At minimum, print a hint in the CLI output: "Open GLB in browser at https://gltf-viewer.donmccurdy.com or use VS Code glTF Tools"

The best option is probably a built-in `cadtool view output.glb` command that spins up a local HTML page and opens it in the default browser. Zero friction.

### F7: No parametric re-run (Low)

I defined all parameters at the top of the script (strut_length, wheel radius, etc.), but there's no way to override them from the CLI. Something like `cadtool run script.py --param strut_length=150` would enable rapid parametric exploration without editing the script.

---

## Summary

| Category | Rating |
|----------|--------|
| Script execution | Smooth |
| Export (GLB/STEP) | Smooth |
| Preview & render | Smooth |
| Positioning parts | Friction |
| Complex geometry (torus) | Friction |
| Assembly workflows | Gap |

**Overall:** cadtool handles the "write script → get output" loop well. The friction is concentrated in CadQuery's workplane/transform model (which is genuinely confusing for positioning parts in 3D space) and in the gap between single-part and multi-part assembly workflows. The biggest wins would be (1) a torus/positioning helper and (2) assembly-aware features.
