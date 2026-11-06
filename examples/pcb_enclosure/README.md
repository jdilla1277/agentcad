# Parametric PCB enclosure

This example turns the measured PCB drawing into a two-part printed enclosure:

- `board.json` — the board description used by the template.
- `enclosure.py` — builds the tray and lid from that one spec.
- `checks.json` — feature assertions for the exported STEP.

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

`checks.json` verifies that the four M2.5 lid clearance holes and four case
pilot holes survived export, and that the assembly contains two solids. The
same run demonstrates the template vocabulary: a spec drives the board
outline, mounting holes, edge cutouts, lid window, keepout, and height stack.
