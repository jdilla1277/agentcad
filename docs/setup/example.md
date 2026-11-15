# Build a bracket and spacer

Save this as `bracket.py` in your working folder. It makes a 100 × 60 mm base,
an 8 mm thick wall, and a 40 mm overall bracket height. The hole centers are
at x = ±30 mm, y = 15 mm. The separate spacer makes the complete assembly
125 × 60 × 40 mm. These positions are example assumptions, not inferred
requirements for your own design.

```python
from build123d import Align, Box, Compound, Cylinder, Pos

HOLE_DIAMETER = 10
base = Box(100, 60, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
upright = Pos(0, -26, 0) * Box(100, 8, 40, align=(Align.CENTER, Align.CENTER, Align.MIN))
bracket = base + upright
for x in (-30, 30):
    bracket -= Pos(x, 15, 0) * Cylinder(HOLE_DIAMETER / 2, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
spacer = Pos(70, 0, 4) * Box(10, 10, 8)
show_object(Compound(children=[bracket, spacer]))
```

`show_object` is supplied by AgentCAD. Run the script with AgentCAD, rather than
with `python bracket.py`. It captures geometry so AgentCAD can write its STEP.

Before building, save this as `spec.json`:

```json
{
  "expect_solids": 2,
  "features": [
    {"name": "base_holes", "type": "cylinder", "diameter_mm": 10, "count": 2}
  ]
}
```

For the edit, copy `bracket.py` to `edited.py`, change only
`HOLE_DIAMETER = 10` to `HOLE_DIAMETER = 12`, and save `edited-spec.json` with
`diameter_mm` set to `12`. Use a new submission key and label. The edit should
remove about **552.9203 mm³**, preserve the spacer, and keep the same overall
dimensions. Check the returned measurements and inspect the pictures, rather
than treating this expected value as a test result.

The spec checks solid and cylindrical-surface counts and diameter. It does
not establish hole position, material, manufacturing readiness, or human
approval. A full `passed: null` verdict means some requirement is unsupported;
`passed: false` means a supported requirement failed, even if its job completed.
