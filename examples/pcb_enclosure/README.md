# Parametric PCB enclosure

This example turns the measured PCB drawing into a two-part printed enclosure:

- `board.json` — the board description used by the template.
- `enclosure.py` — builds the tray and lid from that one spec.
- `checks.json` — standalone feature assertions for the exported STEP.
- `requirements.json` — the same assertions as retained-change checks.

The template exposes `BoardSpec`, `pcb_case`/`pcb_enclosure`, and
`pcb_lid`/`pcb_enclosure_lid` through `agentcad.api`.

## Drawing dimensions and assumptions

The board outline and the main openings come from the drawing. A drawing does
not always dimension the enclosure-side values, so this example records each
assumption explicitly instead of hiding it in the script.

| Item | Value | Source |
| --- | --- | --- |
| Board outline | 28.17 × 51.31 mm | Drawing |
| Board thickness | 1.60 mm | Assumed (common 1.6 mm PCB) |
| Board corner radius | 1.00 mm | Assumed |
| Mounting holes | ⌀3.00 mm, four, 3.00 mm from each edge | Drawing diameter; 3.00 mm inset assumed |
| USB opening | 7.62 × 4.65 mm, centered on the bottom edge | Drawing; center = 28.17 / 2 |
| Button opening | 6.15 × 2.41 mm, right edge, center y = 25.71 | Drawing; edge assignment and Z offset assumed |
| Antenna window | 23.50 × 20.00 mm at x = 2.30, y = 25.50 | Derived from the red dashed antenna region |
| Antenna keepout | Same XY footprint, from the board top to the rim | Assumption: no metal or plastic above the antenna |
| Standoff height | 3.00 mm | Assumed M2.5 hardware stack |
| Bottom component height | 1.50 mm | Assumed; must be ≤ standoff |
| Top component height | 8.50 mm | Assumed |
| Lid clearance | 0.50 mm | Assumed |
| Wall / floor | 2.00 / 1.60 mm | Assumed |
| Board-to-wall clearance | 0.30 mm | Assumed |
| Fasteners | M2.5 | Assumed |

The drawing's `25.50` and `25.71` values are used as the antenna-window start
and the button center respectively. The top connector and pin rows are board
features; the drawing does not dimension a matching wall opening, so the
example assumes the connector fits below the lid and leaves the side wall
closed there.

## Coordinate frame

The board's lower-left corner is `(0, 0)`, its bottom face is `z = 0`, and its
top face is `z = board.thickness_mm`. The tray floor is below the standoffs at
`z = -(standoff + floor)`. The rim is at
`z = thickness + top_max + lid_clearance`. The lid occupies the next
`lid_thickness` above that rim, so the two parts can be assembled without a
transform.

## Build

From the repository root, with the project environment active:

```console
agentcad run examples/pcb_enclosure/enclosure.py --label pcb_v1 --render iso
```

The run output reports `outputs.step`. Check the exported assembly against the
feature spec:

```console
agentcad check-spec <outputs.step> examples/pcb_enclosure/checks.json
```

`checks.json` verifies the four M2.5 lid clearance holes and four case pilot
holes by position and diameter, the four standoff bosses by diameter and count,
that the USB opening is empty material, and that a standoff boss contains
material. The same file demonstrates the template vocabulary: a spec drives the
board outline, mounting holes, edge cutouts, lid window, keepout, and height
stack.

## Retained checks

`requirements.json` declares the same assertions in the retained-change schema.
Pass it to a build so the receipt pins the policy and ties each result to the
written STEP hash:

```console
agentcad run examples/pcb_enclosure/enclosure.py --label pcb_checked \
  --checks examples/pcb_enclosure/requirements.json
```

The `holes` checks prove that each declared position matches one measured
cylinder axis, not merely that the required diameter exists somewhere. The
`keepout` and `material` checks measure the material volume inside an explicit
assembly-frame box. For Z-axis holes the position plane is `(x, y)`; X uses
`(y, z)` and Y uses `(x, z)`. A probe measures volume only — it does not prove
wall thickness or a minimum clearance.
