"""View names and camera-angle parsing without importing the CAD renderer."""

ALL_VIEWS = ["front", "right", "top", "iso"]
NAMED_VIEWS = {"front", "back", "left", "right", "top", "bottom", "iso"}


def parse_view_spec(spec):
    """Parse a --view spec string into a list of (type, value) tuples.

    Returns:
        List of ("named", view_name) or ("custom", (azimuth, elevation)) tuples.

    Raises:
        ValueError: If the spec is invalid.
    """
    parts = [p.strip() for p in spec.split(",")]

    # "all" shorthand
    if parts == ["all"]:
        return [("named", v) for v in ALL_VIEWS]

    # All named views (fast path, backward compat)
    if all(p in NAMED_VIEWS for p in parts):
        return [("named", p) for p in parts]

    # Check for colon-separated angles (mixed mode)
    if any(":" in p for p in parts):
        result = []
        for p in parts:
            if p in NAMED_VIEWS:
                result.append(("named", p))
            elif ":" in p:
                az_s, el_s = p.split(":", 1)
                try:
                    result.append(("custom", (float(az_s), float(el_s))))
                except ValueError:
                    raise ValueError(f"Invalid angle spec '{p}'. Use 'azimuth:elevation'.")
            else:
                raise ValueError(
                    f"Invalid view '{p}' in spec '{spec}'. "
                    f"Named views: {', '.join(sorted(NAMED_VIEWS))}. "
                    f"Custom angles: 'azimuth:elevation'."
                )
        return result

    # Legacy: exactly 2 numeric parts → single custom angle
    if len(parts) == 2:
        try:
            return [("custom", (float(parts[0]), float(parts[1])))]
        except ValueError:
            pass

    raise ValueError(
        f"Invalid view spec '{spec}'. Use named views "
        f"({', '.join(sorted(NAMED_VIEWS))}), 'all', or 'azimuth:elevation'."
    )
