from build123d import Box, Cylinder, Compound, Pos, Axis, fillet
plate = Box(40,30,6)
plate = fillet(plate.edges().filter_by(Axis.Z), 2) - Cylinder(4,20)
rib = Pos(12,0,0)*Box(4,20,4)
body = Compound(children=[plate, rib])
boss = Pos(0,0,4)*(Cylinder(8,6)-Cylinder(4,6))
show_object(body, name="body", options={"id":"body"})
show_object(boss, name="boss", options={"id":"boss"})
show_object(Pos(30,0,0)*Box(4,4,4), name="spacer", options={"id":"spacer", "floating":True})
