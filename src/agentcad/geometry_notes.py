"""Shared plain-language explanations for surprising geometry reports."""


NO_SOLID_BODY_NOTE = (
    "No solid body was detected. Zero volume and any zero-thickness dimension "
    "are expected for surface, shell, or wire geometry. Solid-body operations "
    "such as fillet, chamfer, and boolean edits do not apply to this input; "
    "rebuild a solid from usable profiles or faces, for example by extruding "
    "or lofting sections, before editing."
)
