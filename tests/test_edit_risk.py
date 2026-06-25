"""Edit-risk classification (issue #32)."""

from agentcad.edit_risk import classify_edit_risk


class TestLowRisk:
    def test_valid_small_part_is_low_risk_none(self):
        # A clean box: nothing worth warning about -> None.
        assert classify_edit_risk(
            face_count=6, edge_count=12, is_valid=True, free_edge_count=0
        ) is None


class TestHighRisk:
    """CADGenBench 202: invalid + large + many free edges -> high."""

    def test_cadgenbench_202_is_high(self):
        risk = classify_edit_risk(
            face_count=2157,
            edge_count=5239,
            free_edge_count=526,
            is_valid=False,
            validity_errors=[
                "BRepCheck_SelfIntersectingWire",
                "BRepCheck_UnorientableShape",
            ],
        )
        assert risk is not None
        assert risk["edit_risk"] == "high"

    def test_202_reasons_name_each_condition(self):
        risk = classify_edit_risk(
            face_count=2157,
            edge_count=5239,
            free_edge_count=526,
            is_valid=False,
            validity_errors=[
                "BRepCheck_SelfIntersectingWire",
                "BRepCheck_UnorientableShape",
            ],
        )
        reasons = risk["risk_reasons"]
        assert any("BRepCheck_UnorientableShape" in r for r in reasons)
        assert any("BRepCheck_SelfIntersectingWire" in r for r in reasons)
        assert any("2157 faces" in r and "5239 edges" in r for r in reasons)
        assert any("526" in r for r in reasons)

    def test_202_workflow_warns_against_load_step_boolean(self):
        risk = classify_edit_risk(
            face_count=2157,
            edge_count=5239,
            free_edge_count=526,
            is_valid=False,
            validity_errors=["BRepCheck_UnorientableShape"],
        )
        workflow = " ".join(risk["recommended_workflow"]).lower()
        assert "load_step" in workflow
        assert "boolean" in workflow
        # command-oriented guidance about avoiding huge per-feature payloads
        assert "--features" in workflow or "--ids" in workflow

    def test_high_face_count_invalid_is_high_even_without_free_edges(self):
        # Acceptance: "high face count + invalid topology" classification.
        risk = classify_edit_risk(
            face_count=1500,
            edge_count=300,
            is_valid=False,
            free_edge_count=0,
        )
        assert risk["edit_risk"] == "high"

    def test_high_edge_count_invalid_is_high(self):
        risk = classify_edit_risk(
            face_count=200,
            edge_count=3000,
            is_valid=False,
            free_edge_count=0,
        )
        assert risk["edit_risk"] == "high"

    def test_invalid_with_severe_error_is_high_even_when_small(self):
        # An unorientable shape is fragile regardless of size.
        risk = classify_edit_risk(
            face_count=20,
            edge_count=40,
            is_valid=False,
            free_edge_count=0,
            validity_errors=["BRepCheck_UnorientableShape"],
        )
        assert risk["edit_risk"] == "high"


class TestMediumRisk:
    """Distinguish invalid-small from invalid-large."""

    def test_invalid_small_is_medium(self):
        risk = classify_edit_risk(
            face_count=12, edge_count=30, is_valid=False, free_edge_count=0
        )
        assert risk["edit_risk"] == "medium"

    def test_invalid_small_guidance_suggests_heal(self):
        risk = classify_edit_risk(
            face_count=12, edge_count=30, is_valid=False, free_edge_count=0
        )
        workflow = " ".join(risk["recommended_workflow"]).lower()
        assert "heal" in workflow or "sew" in workflow

    def test_invalid_small_differs_from_invalid_large(self):
        small = classify_edit_risk(
            face_count=12, edge_count=30, is_valid=False, free_edge_count=0
        )
        large = classify_edit_risk(
            face_count=2000, edge_count=30, is_valid=False, free_edge_count=0
        )
        assert small["edit_risk"] == "medium"
        assert large["edit_risk"] == "high"
        assert small["recommended_workflow"] != large["recommended_workflow"]

    def test_large_but_valid_is_medium(self):
        risk = classify_edit_risk(
            face_count=1500, edge_count=400, is_valid=True, free_edge_count=0
        )
        assert risk["edit_risk"] == "medium"
        workflow = " ".join(risk["recommended_workflow"]).lower()
        assert "slow" in workflow or "heavy" in workflow

    def test_many_free_edges_but_valid_is_medium(self):
        risk = classify_edit_risk(
            face_count=50, edge_count=80, is_valid=True, free_edge_count=200
        )
        assert risk["edit_risk"] == "medium"
        assert any("free edges" in r for r in risk["risk_reasons"])
