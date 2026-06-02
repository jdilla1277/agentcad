base = Box(30, 30, 10)
cyl = Cylinder(radius=8, height=20)
sphere = Sphere(radius=6).translate((10, 0, 5))

result = (base + sphere) - cyl
show_object(result)
