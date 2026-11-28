import cadquery as cq
plate = cq.Workplane("XY").box(40,30,6).edges("|Z").fillet(2).faces(">Z").workplane().hole(8)
rib = cq.Workplane("XY").box(4,20,4).translate((12,0,0))
body = cq.Compound.makeCompound([plate.val(), rib.val()])
boss = cq.Workplane("XY").circle(8).circle(4).extrude(6).translate((0,0,1))
show_object(body, name="body", options={"id":"body"})
show_object(boss, name="boss", options={"id":"boss"})
show_object(cq.Workplane("XY").box(4,4,4).translate((30,0,0)), name="spacer", options={"id":"spacer", "floating":True})
