"""Shared plain-language explanations for surprising geometry reports."""


NO_SOLID_BODY_NOTE = (
    "No solid body was detected. volume, surface_area, and center_of_mass are "
    "not physical quantities for surface, shell, or wire geometry (reliable: "
    "false), and zero-thickness dimensions are expected. Solid-body operations "
    "such as fillet, chamfer, and boolean edits do not apply to this input; "
    "rebuild a solid from usable profiles or faces, for example by extruding "
    "or lofting sections, before editing."
)
