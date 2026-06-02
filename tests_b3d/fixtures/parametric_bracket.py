length = 60
width = 20
thickness = 4
hole_dia = 6

plate = Box(length, width, thickness)
cutout = Cylinder(radius=hole_dia / 2, height=thickness * 2)
bracket = plate - cutout
show_object(bracket)
