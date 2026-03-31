import math

import pytest

from cadtool.metrics import compute_metrics


def _make_box(dx=10, dy=10, dz=10):
    """Create a TopoDS_Shape box centered at origin."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    box = BRepPrimAPI_MakeBox(gp_Pnt(-dx / 2, -dy / 2, -dz / 2), dx, dy, dz).Shape()
    return box


def _make_cylinder(radius=5, height=20):
    """Create a TopoDS_Shape cylinder along Z axis."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    cyl = BRepPrimAPI_MakeCylinder(radius, height).Shape()
    return cyl


class TestBoxMetrics:
    """Metrics on a 10x10x10 box centered at origin."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.shape = _make_box(10, 10, 10)
        self.metrics = compute_metrics(self.shape)

    def test_bounding_box(self):
        bb = self.metrics["bounding_box"]
        assert bb["x"][0] == pytest.approx(-5.0, abs=0.01)
        assert bb["x"][1] == pytest.approx(5.0, abs=0.01)
        assert bb["y"][0] == pytest.approx(-5.0, abs=0.01)
        assert bb["y"][1] == pytest.approx(5.0, abs=0.01)
        assert bb["z"][0] == pytest.approx(-5.0, abs=0.01)
        assert bb["z"][1] == pytest.approx(5.0, abs=0.01)

    def test_dimensions(self):
        dims = self.metrics["dimensions"]
        assert dims["x"] == pytest.approx(10.0, abs=0.01)
        assert dims["y"] == pytest.approx(10.0, abs=0.01)
        assert dims["z"] == pytest.approx(10.0, abs=0.01)

    def test_volume(self):
        assert self.metrics["volume"] == pytest.approx(1000.0, abs=0.1)

    def test_surface_area(self):
        # 6 faces * 100 = 600
        assert self.metrics["surface_area"] == pytest.approx(600.0, abs=0.1)

    def test_center_of_mass(self):
        com = self.metrics["center_of_mass"]
        assert com["x"] == pytest.approx(0.0, abs=0.01)
        assert com["y"] == pytest.approx(0.0, abs=0.01)
        assert com["z"] == pytest.approx(0.0, abs=0.01)

    def test_face_count(self):
        assert self.metrics["face_count"] == 6

    def test_edge_count(self):
        assert self.metrics["edge_count"] == 12

    def test_is_valid(self):
        assert self.metrics["is_valid"] is True


class TestCylinderMetrics:
    """Metrics on a cylinder (radius=5, height=20) to verify different values."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.shape = _make_cylinder(radius=5, height=20)
        self.metrics = compute_metrics(self.shape)

    def test_volume(self):
        expected = math.pi * 5**2 * 20  # ~1570.8
        assert self.metrics["volume"] == pytest.approx(expected, rel=0.01)

    def test_surface_area(self):
        # lateral + 2 caps = 2*pi*r*h + 2*pi*r^2
        expected = 2 * math.pi * 5 * 20 + 2 * math.pi * 25  # ~785.4
        assert self.metrics["surface_area"] == pytest.approx(expected, rel=0.01)

    def test_face_count(self):
        # cylinder: 1 lateral + 2 caps = 3
        assert self.metrics["face_count"] == 3

    def test_is_valid(self):
        assert self.metrics["is_valid"] is True


class TestMetricsReturnTypes:
    """Verify the return structure is JSON-serializable and correct types."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.metrics = compute_metrics(_make_box())

    def test_all_keys_present(self):
        expected_keys = {
            "bounding_box", "dimensions", "volume", "surface_area",
            "center_of_mass", "face_count", "edge_count", "is_valid",
        }
        assert set(self.metrics.keys()) == expected_keys

    def test_types(self):
        assert isinstance(self.metrics["volume"], float)
        assert isinstance(self.metrics["surface_area"], float)
        assert isinstance(self.metrics["face_count"], int)
        assert isinstance(self.metrics["edge_count"], int)
        assert isinstance(self.metrics["is_valid"], bool)
        assert isinstance(self.metrics["bounding_box"], dict)
        assert isinstance(self.metrics["dimensions"], dict)
        assert isinstance(self.metrics["center_of_mass"], dict)


# --- M24: Validity diagnostics ---


class TestValidityDiagnostics:
    def test_valid_shape_has_no_validity_errors(self):
        shape = _make_box()
        m = compute_metrics(shape)
        assert m["is_valid"] is True
        assert m.get("validity_errors") is None

    def test_valid_shape_no_warnings(self):
        shape = _make_box()
        m = compute_metrics(shape)
        assert m.get("warnings") is None


class TestNegativeVolumeWarning:
    def test_positive_volume_no_warning(self):
        shape = _make_box()
        m = compute_metrics(shape)
        assert m["volume"] > 0
        assert m.get("warnings") is None

    def test_negative_volume_produces_warning(self):
        """A reversed shell produces negative volume — should get a warning."""
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.TopoDS import TopoDS
        box = BRepPrimAPI_MakeBox(10, 10, 10).Shape()
        # Reverse the shape to flip normals → negative volume
        reversed_shape = box.Reversed()
        m = compute_metrics(reversed_shape)
        assert m["volume"] < 0
        assert "warnings" in m
        assert any("negative volume" in w.lower() for w in m["warnings"])

    def test_negative_volume_warning_mentions_winding(self):
        """Negative volume warning should mention wire winding order."""
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        box = BRepPrimAPI_MakeBox(10, 10, 10).Shape()
        reversed_shape = box.Reversed()
        m = compute_metrics(reversed_shape)
        assert any("winding" in w.lower() for w in m["warnings"])
