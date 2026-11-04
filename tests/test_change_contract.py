"""Shared change records, independent of CAD engines and command adapters."""

import copy
import hashlib
import json

import pytest

from agentcad import change_contract as contract

BRACKET = b"""from build123d import *
plate = Box(60, 30, 6) - Cylinder(6, 12)
bracket = fillet(plate.edges().filter_by(Axis.Z), radius=1)
show_object(bracket, name="bracket")
"""


def artifact(data=BRACKET, **overrides):
    return {
        "name": "bracket.py",
        "role": "source",
        "path": "snapshots/r42/bracket.py",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        **overrides,
    }


def policy(**overrides):
    args = dict(
        target_id="cad",
        policy_id="bracket",
        version=1,
        checks=[
            {
                "check_id": "bore",
                "definition_version": 1,
                "settings": {"expected": 12.0, "tolerance": 0.1, "units": "mm"},
                "require_completion": True,
                "blocks_on_failure": True,
            },
            {
                "check_id": "mounts",
                "definition_version": 1,
                "settings": {"preserve": ["face_v1_12", "face_v1_18"]},
                "require_completion": True,
                "blocks_on_failure": True,
            },
        ],
    )
    args.update(overrides)
    return contract.make_policy(**args)


def candidate(p=None, **overrides):
    p = p or policy()
    args = {
        "revision_id": "r42",
        "parent_revision_id": "r41",
        "inputs": [
            artifact(),
            artifact(
                b"written STEP",
                name="output.step",
                role="geometry",
                path="snapshots/r42/output.step",
            ),
        ],
        "model": {"units": "mm", "runtime": "build123d"},
        "policies": [p],
        "promotion_mode": "automatic",
        "requested_change": "Make the bore 12 mm; preserve mounting holes.",
    }
    args.update(overrides)
    return contract.make_candidate(**args)


def results(c, p, bore="pass", mounts="pass"):
    return [
        contract.make_check_result(
            c,
            p,
            "bore",
            bore,
            observed=11.6 if bore == "fail" else 12.0,
            evidence=[{"artifact": "measurements/bore.json", "entity_id": "face_v1_5"}],
            actions=[{"action": "inspect", "check_id": "bore"}],
        ),
        contract.make_check_result(c, p, "mounts", mounts),
    ]


def request(c, p, **overrides):
    args = {
        "project_id": "bracket-project",
        "request_key": "caller-saved-key-42",
        "selection_generation": 7,
        "expected_promoted_revision_id": "r41",
    }
    args.update(overrides)
    return contract.make_promotion_request(c, p, **args)


def test_candidate_is_detached_from_mutable_inputs_and_json_round_trips():
    inputs = [artifact()]
    model = {"units": "mm", "parts": [{"id": "part_v1_1"}]}
    c = candidate(inputs=inputs, model=model)
    before = copy.deepcopy(c)
    inputs[0]["sha256"] = "0" * 64
    model["parts"][0]["id"] = "part_v2_1"
    assert c == before
    assert contract.validate_document(json.loads(json.dumps(c))) == c
    assert c["schema_version"] == 1


def test_model_fingerprint_changes_with_geometry_not_review_or_revision_metadata():
    c = candidate()
    administrative = candidate(
        revision_id="r43",
        parent_revision_id="r42",
        promotion_mode="staged",
        metadata={"comments": ["Looks right"], "review_state": "accepted"},
    )
    assert c["model_fingerprint"] == administrative["model_fingerprint"]
    assert c["content_sha256"] != administrative["content_sha256"]
    assert (
        candidate(inputs=[artifact(b"new source")])["model_fingerprint"] != c["model_fingerprint"]
    )
    assert candidate(model={"units": "m"})["model_fingerprint"] != c["model_fingerprint"]


def test_artifact_location_is_not_geometry_identity_and_input_order_is_canonical():
    a = artifact()
    b = artifact(
        b"written STEP", name="output.step", role="geometry", path="snapshots/r42/output.step"
    )
    c = candidate(inputs=[a, b])
    assert candidate(inputs=[b, a])["model_fingerprint"] == c["model_fingerprint"]
    a["path"] = "another-store/bracket.py"
    assert candidate(inputs=[a, b])["model_fingerprint"] == c["model_fingerprint"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema_version=2),
        lambda d: d.update(schema_version=True),
        lambda d: d["inputs"][0].update(sha256="0" * 64),
        lambda d: d.update(model_fingerprint="0" * 64),
    ],
)
def test_modified_or_unknown_documents_are_rejected(mutate):
    c = candidate()
    mutate(c)
    with pytest.raises(contract.ContractError):
        contract.validate_document(c)


@pytest.mark.parametrize(
    "changes",
    [
        {"promotion_mode": "maybe"},
        {"revision_id": ""},
        {"parent_revision_id": "r42"},
        {"model": {"dimension": float("nan")}},
        {"model": {1: "not a JSON object key"}},
        {"inputs": [artifact(path="../mutable.py")]},
        {"inputs": [artifact(path="/external/source.py")]},
        {"inputs": [artifact(path="C:/external/source.py")]},
        {"inputs": [artifact(sha256="not-a-hash")]},
        {"inputs": [artifact(size_bytes=True)]},
        {"inputs": [artifact(), artifact()]},
    ],
)
def test_bad_candidate_fields_fail_with_actionable_contract_errors(changes):
    with pytest.raises(contract.ContractError) as exc:
        candidate(**changes)
    assert exc.value.reason
    assert str(exc.value)


def test_policy_pins_definition_settings_and_separates_completion_from_failure():
    p = policy()
    p2 = copy.deepcopy(p)
    p2["checks"][0]["settings"]["expected"] = 13
    with pytest.raises(contract.ContractError):
        contract.validate_document(p2)
    assert candidate(p)["target_policies"]["cad"]["sha256"] == p["content_sha256"]
    with pytest.raises(contract.ContractError):
        policy(version=True)


def test_valid_geometry_does_not_mask_a_failed_required_dimension():
    p, c = policy(), candidate()
    receipt = contract.make_change_receipt(
        c, p, results(c, p, bore="fail"), promoted_revision_id="r41"
    )
    assert receipt["validation_state"] == "invalid"
    assert receipt["promotion_ready"] is False
    assert receipt["promoted_revision_id"] == "r41"
    failed = receipt["blockers"][0]
    assert failed["check_id"] == "bore"
    assert failed["settings"] == {"expected": 12.0, "tolerance": 0.1, "units": "mm"}
    assert failed["observed"] == 11.6
    assert failed["evidence"][0]["entity_id"] == "face_v1_5"
    assert failed["actions"] == [{"action": "inspect", "check_id": "bore"}]
    assert len(receipt["checks"]) == 2


@pytest.mark.parametrize(
    "bore,mounts,expected",
    [
        ("pass", "pass", "valid"),
        ("pending", "pass", "pending"),
        ("unavailable", "pass", "unknown"),
        ("fail", "pending", "invalid"),
        ("pending", "unavailable", "pending"),
    ],
)
def test_aggregate_validation_states(bore, mounts, expected):
    p, c = policy(), candidate()
    receipt = contract.make_change_receipt(c, p, results(c, p, bore, mounts))
    assert receipt["validation_state"] == expected


def test_missing_required_evidence_is_unknown_not_a_pass():
    p, c = policy(), candidate()
    receipt = contract.make_change_receipt(c, p, [])
    assert receipt["validation_state"] == "unknown"
    assert {b["check_id"] for b in receipt["blockers"]} == {"bore", "mounts"}


def test_required_analysis_completion_can_have_advisory_findings():
    p = contract.make_policy(
        target_id="cad",
        policy_id="analysis",
        version=1,
        checks=[
            {
                "check_id": "analysis",
                "definition_version": 1,
                "settings": {},
                "require_completion": True,
                "blocks_on_failure": False,
            }
        ],
    )
    c = candidate(p)
    completed = contract.make_check_result(
        c, p, "analysis", "fail", observed={"finding": "warning"}
    )
    receipt = contract.make_change_receipt(c, p, [completed])
    assert receipt["validation_state"] == "valid"
    assert receipt["promotion_ready"] is True
    assert receipt["warnings"][0]["observed"] == {"finding": "warning"}
    pending = contract.make_check_result(c, p, "analysis", "pending")
    assert contract.make_change_receipt(c, p, [pending])["promotion_ready"] is False


def test_review_readiness_is_separate_from_model_validation():
    p = policy(required_reviews=["mounting-approval"])
    c = candidate(p)
    receipt = contract.make_change_receipt(
        c, p, results(c, p), review_states={"mounting-approval": "stale"}
    )
    assert receipt["validation_state"] == "valid"
    assert receipt["promotion_ready"] is False
    assert receipt["blockers"][0]["reason"] == "required_review_stale"


def test_results_cannot_be_reused_across_revisions_targets_or_policy_settings():
    p, c = policy(), candidate()
    old_results = results(c, p)
    with pytest.raises(contract.ContractError):
        contract.make_change_receipt(candidate(revision_id="r43"), p, old_results)
    other = contract.make_policy(
        target_id="robot", policy_id="bracket", version=1, checks=p["checks"]
    )
    with pytest.raises(contract.ContractError):
        contract.make_change_receipt(c, other, old_results)
    changed = contract.make_policy(
        target_id="cad",
        policy_id="bracket",
        version=2,
        checks=p["checks"],
        previous_policy=p,
        actor="user",
        rationale="Confirm a successor policy explicitly.",
    )
    with pytest.raises(contract.ContractError):
        contract.make_change_receipt(c, changed, old_results)
    with pytest.raises(contract.ContractError):
        contract.make_change_receipt(c, p, [old_results[0], old_results[0]])


def test_promotion_request_replays_only_the_exact_request_with_the_same_known_key():
    p, c = policy(), candidate()
    first = request(c, p)
    assert contract.validate_replay(first, json.loads(json.dumps(first))) == first
    for retry in [
        request(c, p, selection_generation=8),
        request(candidate(revision_id="r43"), p),
    ]:
        with pytest.raises(contract.ContractError) as exc:
            contract.validate_replay(first, retry)
        assert exc.value.reason == "request_key_conflict"
    assert first["request_key"] == "caller-saved-key-42"


def test_promotion_outcome_does_not_masquerade_as_validation():
    p, c = policy(), candidate(promotion_mode="staged")
    check_receipt = contract.make_change_receipt(c, p, results(c, p), promoted_revision_id="r41")
    promotion = contract.make_promotion_receipt(
        request(c, p), check_receipt, outcome="promoted", promoted_revision_after="r42"
    )
    assert promotion["validation_state"] == "valid"
    assert promotion["outcome"] == "promoted"
    assert promotion["promoted_revision_before"] == "r41"
    assert promotion["promoted_revision_after"] == "r42"
    assert promotion["verified_artifacts"] == c["deliverables"]["cad"]
    assert promotion["request_key"] == "caller-saved-key-42"
    assert promotion["target_id"] == "cad"
    assert contract.validate_document(json.loads(json.dumps(promotion))) == promotion


def test_failed_or_pending_candidates_cannot_have_success_receipts():
    p, c = policy(), candidate()
    failed = contract.make_change_receipt(
        c, p, results(c, p, bore="fail"), promoted_revision_id="r41"
    )
    with pytest.raises(contract.ContractError):
        contract.make_promotion_receipt(
            request(c, p), failed, outcome="promoted", promoted_revision_after="r42"
        )
    refused = contract.make_promotion_receipt(
        request(c, p), failed, outcome="not_promoted", promoted_revision_after="r41"
    )
    assert refused["candidate_retained"] is True
    assert refused["blockers"] == failed["blockers"]
    assert refused["promoted_revision_before"] == refused["promoted_revision_after"]


def test_promotion_receipt_rejects_wrong_snapshot_and_wrong_accepted_target():
    p, c = policy(), candidate()
    checked = contract.make_change_receipt(c, p, results(c, p), promoted_revision_id="r41")
    with pytest.raises(contract.ContractError):
        contract.make_promotion_receipt(
            request(c, p), checked, outcome="promoted", promoted_revision_after="r43"
        )
    with pytest.raises(contract.ContractError):
        contract.make_promotion_receipt(
            request(c, p, expected_promoted_revision_id="r40"),
            checked,
            outcome="promoted",
            promoted_revision_after="r42",
        )


def test_in_progress_operation_has_no_terminal_promotion_outcome():
    p, c = policy(), candidate()
    status = contract.make_operation_status(request(c, p), "running", jobs=[{"job_id": "bore-job"}])
    assert status["lifecycle"] == "running"
    assert status["outcome"] is None
    assert status["request_key"] == "caller-saved-key-42"
    assert status["jobs"] == [{"job_id": "bore-job"}]


def test_contract_layer_does_not_write_history_or_advance_current(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p, c = policy(), candidate()
    checked = contract.make_change_receipt(c, p, results(c, p), promoted_revision_id="r41")
    contract.make_promotion_receipt(
        request(c, p), checked, outcome="promoted", promoted_revision_after="r42"
    )
    assert list(tmp_path.iterdir()) == []


def reseal(record):
    body = {k: v for k, v in record.items() if k != "content_sha256"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    record["content_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    return record


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c.pop("target_policies"),
        lambda c: c.update(target_policies=[]),
        lambda c: c.update(metadata=[]),
        lambda c: c.update(requested_change=42),
        lambda c: c.update(unrecognized_field="needs a new schema"),
    ],
)
def test_reloaded_candidate_has_a_real_schema_not_only_a_matching_digest(mutation):
    c = candidate()
    mutation(c)
    with pytest.raises(contract.ContractError):
        contract.validate_document(reseal(c))


def test_receipt_rejects_invalid_readiness_and_bad_evidence_shapes():
    p, c = policy(), candidate()
    checked = contract.make_change_receipt(c, p, results(c, p, bore="fail"))
    checked["promotion_ready"] = "yes"
    with pytest.raises(contract.ContractError):
        contract.validate_document(reseal(checked))
    with pytest.raises(contract.ContractError):
        contract.make_check_result(c, p, "bore", "pass", evidence="not a list")
    with pytest.raises(contract.ContractError):
        contract.make_check_result(c, p, "bore", [])


def test_all_records_round_trip_and_nested_evidence_keeps_its_integrity():
    p, c = policy(), candidate()
    check_results = results(c, p, bore="fail")
    checked = contract.make_change_receipt(c, p, check_results, promoted_revision_id="r41")
    req = request(c, p)
    terminal = contract.make_promotion_receipt(
        req, checked, outcome="not_promoted", promoted_revision_after="r41"
    )
    progress = contract.make_operation_status(req, "pending")
    for document in [p, c, *check_results, checked, req, terminal, progress]:
        assert contract.validate_document(json.loads(json.dumps(document))) == document
    for evidence in checked["checks"]:
        contract.validate_document(evidence, kind="check_result")
    # A blocker is a diagnostic referencing evidence, not a modified signed
    # check document retaining the original document's now-invalid digest.
    assert "content_sha256" not in checked["blockers"][0]


def test_check_definition_version_and_actual_settings_are_both_pinned():
    p, c = policy(), candidate()
    checked = results(c, p)
    assert checked[0]["definition_version"] == 1
    checked[0]["settings"]["expected"] = 13
    reseal(checked[0])
    with pytest.raises(contract.ContractError) as exc:
        contract.make_change_receipt(c, p, checked)
    assert exc.value.reason == "binding_mismatch"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(blockers="not a list"),
        lambda d: d.update(validation_state="maybe"),
        lambda d: d.update(lifecycle="running"),
        lambda d: d.update(verified_artifacts=[{"path": "missing identity"}]),
    ],
)
def test_terminal_receipts_reject_malformed_reloaded_fields(mutation):
    p, c = policy(), candidate()
    checked = contract.make_change_receipt(c, p, results(c, p), promoted_revision_id="r41")
    receipt = contract.make_promotion_receipt(
        request(c, p), checked, outcome="promoted", promoted_revision_after="r42"
    )
    mutation(receipt)
    with pytest.raises(contract.ContractError):
        contract.validate_document(reseal(receipt))


def test_optional_unavailable_check_is_visible_without_blocking():
    p = contract.make_policy(
        target_id="cad",
        policy_id="optional",
        version=1,
        checks=[
            {
                "check_id": "optional",
                "definition_version": 1,
                "settings": {},
                "require_completion": False,
                "blocks_on_failure": False,
            }
        ],
    )
    receipt = contract.make_change_receipt(candidate(p), p, [])
    assert receipt["validation_state"] == "valid"
    assert receipt["promotion_ready"] is True
    assert receipt["warnings"][0]["state"] == "unavailable"


def test_policy_successor_records_actor_rationale_and_actual_requirement_diff():
    original = policy()
    updated_checks = copy.deepcopy(original["checks"])
    updated_checks[0]["settings"]["expected"] = 13.0
    successor = contract.make_policy(
        target_id="cad",
        policy_id="bracket",
        version=2,
        checks=updated_checks,
        previous_policy=original,
        actor="user",
        rationale="The bearing bore is now 13 mm.",
    )
    change = successor["change"]
    assert change["actor"] == "user"
    assert change["rationale"] == "The bearing bore is now 13 mm."
    assert change["previous_policy"]["sha256"] == original["content_sha256"]
    assert change["requirements_diff"]["checks"]["before"] == original["checks"]
    assert change["requirements_diff"]["checks"]["after"] == updated_checks
    assert original["checks"][0]["settings"]["expected"] == 12.0
    assert successor["content_sha256"] != original["content_sha256"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"previous_policy": None},
        {"actor": ""},
        {"rationale": ""},
        {"version": 3},
    ],
)
def test_policy_changes_cannot_silently_replace_requirements(overrides):
    p = policy()
    args = dict(
        target_id="cad",
        policy_id="bracket",
        version=2,
        checks=p["checks"],
        previous_policy=p,
        actor="user",
        rationale="Explicitly change requirements.",
    )
    args.update(overrides)
    with pytest.raises(contract.ContractError):
        contract.make_policy(**args)


def test_multiple_targets_have_separate_deliverables_and_receipts():
    first = policy()
    second = contract.make_policy(
        target_id="other-cad", policy_id="other", version=1, checks=first["checks"]
    )
    a = artifact(b"part A", name="a.step", role="geometry", path="snapshots/r42/a.step")
    b = artifact(b"part B", name="b.step", role="geometry", path="snapshots/r42/b.step")
    c = candidate(
        policies=[first, second],
        inputs=[artifact(), a, b],
        deliverables={"cad": [a], "other-cad": [b]},
    )
    checked = contract.make_change_receipt(c, first, results(c, first), promoted_revision_id="r41")
    receipt = contract.make_promotion_receipt(
        request(c, first), checked, outcome="promoted", promoted_revision_after="r42"
    )
    assert receipt["verified_artifacts"] == [a]
    assert b not in receipt["verified_artifacts"]
    assert receipt["target_id"] == "cad"


def test_no_output_candidate_cannot_get_a_successful_handoff_receipt():
    p = policy()
    c = candidate(inputs=[artifact()])
    checked = contract.make_change_receipt(c, p, results(c, p), promoted_revision_id="r41")
    assert checked["promotion_ready"] is False
    assert any(item["reason"] == "required_artifacts_missing" for item in checked["blockers"])
    with pytest.raises(contract.ContractError):
        contract.make_promotion_receipt(
            request(c, p), checked, outcome="promoted", promoted_revision_after="r42"
        )


def test_deliverables_must_be_explicit_for_multiple_targets_and_pin_available_inputs():
    p = policy()
    second = contract.make_policy(
        target_id="other-cad", policy_id="other", version=1, checks=p["checks"]
    )
    with pytest.raises(contract.ContractError):
        candidate(policies=[p, second])
    with pytest.raises(contract.ContractError):
        candidate(deliverables={"cad": [artifact(b"not in snapshot", role="geometry")]})


@pytest.mark.parametrize("conflict", ["hash", "size"])
def test_candidate_rejects_conflicting_content_at_one_snapshot_path(conflict):
    source = artifact(name="source", path="snapshots/r42/model.step")
    output = artifact(
        name="output",
        role="geometry",
        path=source["path"],
        **({"sha256": "0" * 64} if conflict == "hash" else {"size_bytes": len(BRACKET) + 1}),
    )
    with pytest.raises(contract.ContractError) as exc:
        candidate(inputs=[source, output])
    assert exc.value.reason == "artifact_path_conflict"
    assert source["path"] in str(exc.value)


@pytest.mark.parametrize("conflict", ["hash", "size"])
def test_reloaded_candidate_rejects_conflicting_snapshot_path_identities(conflict):
    source = artifact(name="source", path="snapshots/r42/model.step")
    output = artifact(
        name="output",
        role="geometry",
        path="snapshots/r42/other.step",
        **({"sha256": "0" * 64} if conflict == "hash" else {"size_bytes": len(BRACKET) + 1}),
    )
    c = candidate(inputs=[source, output])
    # Paths are excluded from model identity. Updating both references and
    # resealing avoids failing on a stale fingerprint or deliverable binding.
    c["inputs"][1]["path"] = source["path"]
    c["deliverables"]["cad"][0]["path"] = source["path"]
    with pytest.raises(contract.ContractError) as exc:
        contract.validate_document(reseal(c))
    assert exc.value.reason == "artifact_path_conflict"


def test_identical_content_aliases_can_share_a_snapshot_path():
    source = artifact(name="source", path="snapshots/r42/model.step")
    output = {**source, "name": "output", "role": "geometry"}
    c = candidate(inputs=[source, output])
    assert contract.validate_document(json.loads(json.dumps(c))) == c
    checked = contract.make_change_receipt(c, policy(), results(c, policy()))
    assert checked["validation_state"] == "valid"
    assert checked["promotion_ready"] is True


def observed_operation(c, p, *, generation=8):
    return {
        "candidate": {k: c[k] for k in ("revision_id", "model_fingerprint", "content_sha256")},
        "policy": {
            "policy_id": p["policy_id"],
            "version": p["version"],
            "sha256": p["content_sha256"],
        },
        "selection_generation": generation,
        "promoted_revision_id": "r41",
    }


def test_ready_candidate_refusal_preserves_observed_selection_and_reconciliation():
    p, c = policy(), candidate()
    selected = candidate(revision_id="r43", parent_revision_id="r42")
    checked = contract.make_change_receipt(c, p, results(c, p), promoted_revision_id="r41")
    assert checked["promotion_ready"] is True
    checked_before = copy.deepcopy(checked)
    req = request(c, p)
    observed = observed_operation(selected, p)
    receipt = contract.make_promotion_receipt(
        req,
        checked,
        outcome="not_promoted",
        promoted_revision_after="r41",
        operation_diagnostics=[
            {
                "reason": "selection_conflict",
                "observed": observed,
                "actions": [{"action": "reconcile", "revision_id": "r43"}],
            }
        ],
    )
    diagnostic = receipt["operation_diagnostics"][0]
    assert diagnostic["requested"] == observed_operation(c, p, generation=7)
    assert diagnostic["observed"] == observed
    assert diagnostic["actions"] == [{"action": "reconcile", "revision_id": "r43"}]
    assert receipt["reason_codes"] == ["selection_conflict"]
    assert receipt["blockers"] == []
    assert receipt["validation_state"] == "valid"
    assert receipt["check_receipt_sha256"] == checked["content_sha256"]
    assert receipt["promoted_revision_before"] == receipt["promoted_revision_after"] == "r41"
    assert checked == checked_before
    observed["candidate"]["revision_id"] = "r99"
    observed["selection_generation"] = 100
    restored = contract.validate_document(json.loads(json.dumps(receipt)))
    assert restored["operation_diagnostics"][0]["observed"]["candidate"]["revision_id"] == "r43"
    assert restored["operation_diagnostics"][0]["observed"]["selection_generation"] == 8


def test_policy_conflict_preserves_the_observed_policy_not_only_the_requested_one():
    p, c = policy(), candidate()
    updated = contract.make_policy(
        target_id="cad",
        policy_id="bracket",
        version=2,
        checks=p["checks"],
        previous_policy=p,
        actor="user",
        rationale="Explicitly update the policy.",
    )
    selected = candidate(updated, revision_id="r43", parent_revision_id="r42")
    checked = contract.make_change_receipt(c, p, results(c, p), promoted_revision_id="r41")
    receipt = contract.make_promotion_receipt(
        request(c, p),
        checked,
        outcome="not_promoted",
        promoted_revision_after="r41",
        operation_diagnostics=[
            {
                "reason": "policy_conflict",
                "observed": observed_operation(selected, updated),
                "actions": [{"action": "reconcile_policy", "policy_id": "bracket", "version": 2}],
            }
        ],
    )
    diagnostic = receipt["operation_diagnostics"][0]
    assert diagnostic["requested"]["policy"]["version"] == 1
    assert diagnostic["observed"]["policy"]["version"] == 2
    assert diagnostic["observed"]["policy"]["sha256"] == updated["content_sha256"]
    assert contract.validate_document(json.loads(json.dumps(receipt))) == receipt


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["observed"].update(selection_generation=True),
        lambda d: d["observed"]["policy"].update(sha256="not-a-hash"),
        lambda d: d["observed"]["candidate"].update(revision_id=""),
        lambda d: d.update(actions=[]),
        lambda d: d.update(reason=""),
    ],
)
def test_operation_diagnostic_schema_is_validated_for_builders_and_reloaded_records(mutation):
    p, c = policy(), candidate()
    checked = contract.make_change_receipt(c, p, results(c, p), promoted_revision_id="r41")
    data = {
        "reason": "selection_conflict",
        "observed": observed_operation(c, p, generation=8),
        "actions": [{"action": "reconcile", "revision_id": "r42"}],
    }
    receipt = contract.make_promotion_receipt(
        request(c, p),
        checked,
        outcome="not_promoted",
        promoted_revision_after="r41",
        operation_diagnostics=[data],
    )
    bad = copy.deepcopy(data)
    mutation(bad)
    with pytest.raises(contract.ContractError):
        contract.make_promotion_receipt(
            request(c, p),
            checked,
            outcome="not_promoted",
            promoted_revision_after="r41",
            operation_diagnostics=[bad],
        )
    mutation(receipt["operation_diagnostics"][0])
    with pytest.raises(contract.ContractError):
        contract.validate_document(reseal(receipt))


def test_reloaded_operation_diagnostic_cannot_change_the_original_request():
    p, c = policy(), candidate()
    checked = contract.make_change_receipt(c, p, results(c, p), promoted_revision_id="r41")
    receipt = contract.make_promotion_receipt(
        request(c, p),
        checked,
        outcome="not_promoted",
        promoted_revision_after="r41",
        operation_diagnostics=[
            {
                "reason": "selection_conflict",
                "observed": observed_operation(c, p, generation=8),
                "actions": [{"action": "reconcile", "revision_id": "r42"}],
            }
        ],
    )
    receipt["operation_diagnostics"][0]["requested"]["selection_generation"] = 3
    with pytest.raises(contract.ContractError) as exc:
        contract.validate_document(reseal(receipt))
    assert exc.value.reason == "binding_mismatch"


def test_success_receipt_cannot_include_operation_failure_diagnostics():
    p, c = policy(), candidate()
    checked = contract.make_change_receipt(c, p, results(c, p), promoted_revision_id="r41")
    with pytest.raises(contract.ContractError):
        contract.make_promotion_receipt(
            request(c, p),
            checked,
            outcome="promoted",
            promoted_revision_after="r42",
            operation_diagnostics=[
                {
                    "reason": "selection_conflict",
                    "observed": observed_operation(c, p, generation=8),
                    "actions": [{"action": "reconcile", "revision_id": "r42"}],
                }
            ],
        )


def test_operation_diagnostics_can_retain_an_absent_observed_selection():
    p, c = policy(), candidate()
    checked = contract.make_change_receipt(c, p, results(c, p), promoted_revision_id="r41")
    observed = {
        "candidate": None,
        "policy": None,
        "selection_generation": 8,
        "promoted_revision_id": "r41",
    }
    receipt = contract.make_promotion_receipt(
        request(c, p),
        checked,
        outcome="not_promoted",
        promoted_revision_after="r41",
        reason_codes=["selection_conflict"],
        operation_diagnostics=[
            {
                "reason": "selection_conflict",
                "observed": observed,
                "actions": [{"action": "select_candidate", "revision_id": "r42"}],
            }
        ],
    )
    assert receipt["reason_codes"] == ["selection_conflict"]
    assert receipt["operation_diagnostics"][0]["observed"] == observed
    assert contract.validate_document(json.loads(json.dumps(receipt))) == receipt
