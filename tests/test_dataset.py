from copy import deepcopy

import pytest

from attestextract import extract
from attestextract.analysis import _input_observations
from attestextract.backends import ReplayBackend
from attestextract.dataset import _has_value_source, assemble_dataset, query_dataset, validate_dataset_options


def frozen(owner="Batch A17", field="mass", value="12", unit="g", identity="owner", measurement=None):
    concept = measurement or field
    source = f"{owner}: {concept} = {value} {unit}."
    values = {"entity": owner, "value": value, "unit": unit, "conditions": None}
    backend = ReplayBackend([{"records": [{"record_type": field, "values": values,
        "quotes": {k: source if v is not None else "" for k, v in values.items()}}]}], output_protocol="flat-v1")
    fields = {identity: "Full name of the row subject", field: f"Reported {concept} of the row subject"}
    result = extract(source, prompt_description="Extract the reported observation.", field_list=fields,
                     backend=backend)
    evidence = {}
    observations, quarantined = _input_observations(source, result, evidence)
    observations[0].update(binding_status="supported", subject_id="obj-1", target_match="supported",
                           property_name=concept, statistic=None, binding_evidence_ids=list(evidence))
    analysis = {"analysis_version": "0.1", "source": result.source.model_dump(mode="json"),
                "source_format_status": "complete", "status": "complete", "semantic_status": "model_proposed",
                "task": {"name": result.task.name, "description": result.task.description, "fields": fields},
                "table_subject": "named subject", "evidence": list(evidence.values()), "relations": [],
                "objects": [{"object_id": "obj-1", "name": owner, "kind": "instance", "scope": owner,
                             "is_table_subject": True, "mention_evidence_ids": list(evidence),
                             "semantic_status": "model_proposed"}], "observations": observations,
                "quarantined": quarantined, "budget": {"reserved": 1}}
    return source, result.model_dump(mode="json"), analysis


@pytest.mark.parametrize("owner,field,concept,value,unit,identity", [
    ("Batch A17", "mass", "mass", "12", "g", "owner"),
    ("Coupon S3", "length", "length", "8", "mm", "specimen_id"),
    ("Station N2", "depth", "depth", "7", "m", "station_name"),
    ("Batch A17", "f01", "mass", "12", "g", "f00")])
def test_full_schema_uses_task_identity_with_readable_value_and_exact_lineage(owner, field, concept,
                                                                             value, unit, identity):
    source, result, analysis = frozen(owner, field, value, unit, identity, concept)
    before = deepcopy((result, analysis))
    view = assemble_dataset(source, result, analysis, subject_label_field=identity)
    assert view["rows"] == [{identity: owner, field: value}]
    assert [*view["rows"][0]] == view["schema"]
    [detail] = query_dataset(view, owner)
    assert detail["observation_id"] == view["lineage"]["rows"][0]["cells"][field][0]
    assert detail["source_record"] == "/candidates/0"
    assert detail["original_checks"]["semantic_status"] == "model_proposed"
    assert detail["unit"] == unit and detail["entity"] == owner
    assert view["budget"]["assembly_calls"] == 0
    for span in detail["evidence"]:
        assert source[span["start"]:span["end"]] == span["quote"]
    assert (result, analysis) == before


def test_field_policy_withholds_measurement_and_preserves_exploratory_observation():
    source, result, analysis = frozen()
    view = assemble_dataset(source, result, analysis, identity_fields=["owner"],
                            field_policies={"mass": {"allowed_units": ["kg"]}})
    assert view["rows"] == [] and query_dataset(view) == []
    [obs] = query_dataset(view, "Batch A17", exploratory=True)
    assert (obs["value"], obs["unit"], obs["fit"]["status"]) == ("12", "g", "unknown")
    assert obs["direct"]["status"] == "supported"


@pytest.mark.parametrize("fault", ["source", "metadata", "field", "value", "pointer", "span", "blocker"])
def test_reject_mixed_or_modified_archives(fault):
    source, result, analysis = frozen()
    if fault == "source":
        source += "\r\n"
    elif fault == "metadata":
        del analysis["source"]
    elif fault == "field":
        analysis["task"]["fields"]["mass"] += "x"
    elif fault == "span":
        analysis["evidence"][0]["quote"] = "forged"
    elif fault == "blocker":
        result["review"].append({"candidate_id": result["candidates"][0]["candidate_id"],
                                 "code": "value_mismatch", "message": "Check the extracted value."})
    else:
        analysis["observations"][0]["source_record" if fault == "pointer" else "value"] = "unrelated"
    with pytest.raises(ValueError):
        assemble_dataset(source, result, analysis)


def test_raw_candidate_missing_in_older_analysis_reappears_in_details():
    source, result, analysis = frozen()
    analysis["observations"] = []
    view = assemble_dataset(source, result, analysis)
    [obs] = view["observations"]
    assert obs["value"] == "12" and obs["original_checks"]["binding_status"] == "pending"
    assert any(gap["kind"] == "missing_analysis_observation" for gap in view["coverage_gaps"])


def test_invalid_mapping_is_reported_early():
    with pytest.raises(ValueError, match="identity_fields"):
        validate_dataset_options(["a"], identity_fields=["b"])
    with pytest.raises(ValueError, match="allowed"):
        validate_dataset_options(["a"], field_policies={"a": {"allowed_statistics": "mean"}})


def test_display_name_cannot_supply_an_unreported_serial_number():
    source, result, analysis = frozen(identity="serial_number")
    view = assemble_dataset(source, result, analysis, identity_fields=["serial_number"])
    assert not view["rows"] and not query_dataset(view)
    [obs] = query_dataset(view, "Batch A17", association_only=True)
    assert obs["projection_reason"] == "identity_missing_or_conflicting"


def test_related_parent_search_does_not_return_a_part_measurement_as_its_own():
    source, result, analysis = frozen()
    view = assemble_dataset(source, result, analysis, subject_label_field="owner")
    view["observations"][0]["related"].append({"id": "plant", "label": "East plant", "status": "supported"})
    assert not query_dataset(view, "East plant")
    assert len(query_dataset(view, "East plant", association_only=True)) == 1


@pytest.mark.parametrize("source,value", [("2e3", "2"), ("2E-3", "3"), ("A17", "17"), ("-12", "12")])
def test_numeric_grounding_checks_both_sides_of_exact_evidence_slice(source, value):
    start = source.index(value)
    assert not _has_value_source({"value": value, "source_evidence": [
        {"fields": ["value"], "span": {"start": start, "end": start + len(value)}}]}, source)
