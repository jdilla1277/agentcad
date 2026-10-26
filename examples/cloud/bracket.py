"""Cloud example: an L bracket with two 10 mm holes and a separate spacer.

Change HOLE_DIAMETER to 12 and submit with a new label/key for a second version.
The edit removes 552.9203 mm^3; the spacer remains unchanged.
"""
from build123d import Align, Box, Compound, Cylinder, Pos

HOLE_DIAMETER = 10
base = Box(100, 60, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
upright = Pos(0, -26, 0) * Box(100, 8, 40, align=(Align.CENTER, Align.CENTER, Align.MIN))
bracket = base + upright
for x in (-30, 30):
    bracket -= Pos(x, 15, 0) * Cylinder(HOLE_DIAMETER / 2, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
spacer = Pos(70, 0, 4) * Box(10, 10, 8)
show_object(Compound(children=[bracket, spacer]))
