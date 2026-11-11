"""Synthetic acceptance for complete delivery with separate checking states."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from attestextract.table_routes import (
    build_table_input, evaluate_route, parse_route_response,
    parse_route_response_partial, reconcile_results,
)


EXAMPLES = Path(__file__).resolve().parents[1] / "src/attestextract/examples/table_routes"


def case_named(name):
    for filename in ("cases.json", "edge_cases.json", "failure_cases.json"):
        for case in json.loads((EXAMPLES / filename).read_text(encoding="utf-8")):
            if case["id"] == name:
                return case
    raise AssertionError(name)


def evaluate(case, parsed=None):
    table = build_table_input(case["source"])
    return evaluate_route("A", parsed or case["replays"]["A"], table, case["task"])


@pytest.mark.parametrize("relation", ["omitted", None])
def test_empty_optional_binding_does_not_require_an_invented_relationship(relation):
    case = case_named("paired_parameters")
    raw = deepcopy(case["replays"]["A"])
    unit = next(r["fields"]["unit"] for r in raw["records"] if r["fields"]["unit"]["value"] is None)
    unit.pop("relation")
    if relation is None:
        unit["relation"] = None
    text = json.dumps(raw)
    parsed = parse_route_response_partial("A", text)
    assert parsed["rejections"] == []
    assert parsed["record_indices"] == [0, 1]
    assert len(parse_route_response("A", text)["records"]) == 2
    result = evaluate(case, parsed)
    assert len(result["candidates"]) == len(result["candidates"]) == 2
    empty = next(r for r in result["candidates"] if r["values"]["unit"] is None)
    assert empty["field_checks"]["unit"]["relation_check"] == "not_applicable"


def test_nonempty_binding_without_relation_is_isolated_and_strict_parser_still_fails():
    case = case_named("paired_parameters")
    raw = deepcopy(case["replays"]["A"])
    raw["records"][0]["fields"]["value"].pop("relation")
    original = deepcopy(raw)
    with pytest.raises(ValueError):
        parse_route_response("A", json.dumps(raw))
    parsed = parse_route_response_partial("A", json.dumps(raw))
    assert parsed["record_indices"] == [1]
    assert parsed["rejections"][0]["index"] == 0
    assert parsed["rejections"][0]["raw"] == original["records"][0]
    assert parsed["rejections"][0]["errors"][0]["loc"] == ["fields", "value"]
    result = evaluate(case, parsed)
    assert result["status"] == "partial_format_error"
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["input_indices"] == [1]
    assert result["parse_rejections"] == parsed["rejections"]
    assert raw == original


def test_duplicate_ids_all_isolated_including_otherwise_invalid_duplicate():
    case = case_named("before_after")
    raw = deepcopy(case["replays"]["A"])
    raw["records"] = raw["records"][:2]
    raw["records"][1]["record_id"] = raw["records"][0]["record_id"]
    raw["records"][1]["fields"]["value"].pop("relation")
    remaining = deepcopy(raw["records"][0])
    remaining["record_id"] = "unique"
    raw["records"].append(remaining)
    parsed = parse_route_response_partial("A", json.dumps(raw))
    assert parsed["record_indices"] == [2]
    assert [r["index"] for r in parsed["rejections"]] == [0, 1]
    assert all(any(e["type"] == "duplicate_record_id" for e in r["errors"])
               for r in parsed["rejections"])


@pytest.mark.parametrize("text", [
    '{"records":[', '{"records":[],"records":[]}', '{"records":[NaN]}', '{"records":[1e999]}',
    '[]', '{"records":{}}', '{"records":[],"extra":null}',
    '{"records":[{"record_id":"r","record_id":"s"}]}',
])
def test_partial_parser_does_not_salvage_invalid_json_or_envelope(text):
    with pytest.raises(ValueError):
        parse_route_response_partial("A", text)


def test_no_valid_records_is_format_error_but_a_legitimate_empty_array_is_distinct():
    case = case_named("paired_parameters")
    failed = evaluate(case, parse_route_response_partial("A", '{"records":[42]}'))
    empty = evaluate(case, parse_route_response_partial("A", '{"records":[]}'))
    assert failed["status"] == "format_error" and failed["parse_rejections"]
    assert empty["status"] == "checked" and not empty["parse_rejections"]
    assert failed["candidates"] == empty["candidates"] == []


def test_unknown_merge_relation_remains_delivered_without_semantic_acceptance():
    case = case_named("group_total")
    raw = deepcopy(case["replays"]["A"])
    raw["records"][0].update(scope="individual", scope_sources=[])
    original = deepcopy(raw)
    result = evaluate(case, raw)
    assert result["candidates"] and result["issues"]
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["values"]["value"] == raw["records"][0]["fields"]["value"]["value"]
    assert candidate["mechanical_errors"] == []
    assert "merged_measurement_scope_unresolved" in {i["code"] for i in candidate["relationship_warnings"]}
    assert candidate["field_checks"]["value"]["literal_check"] == "passed"
    assert candidate["field_checks"]["value"]["relation_check"] == "not_confirmed"
    assert candidate["semantic_verification"] == "not_run"
    assert raw == original


def test_optional_relation_does_not_turn_a_required_missing_value_into_success():
    case = case_named("paired_parameters")
    raw = deepcopy(case["replays"]["A"])
    raw["records"][0]["fields"]["value"] = {"value": None, "sources": []}
    parsed = parse_route_response_partial("A", json.dumps(raw))
    assert parsed["rejections"] == []
    result = evaluate(case, parsed)
    candidate = result["candidates"][0]
    assert any(i["code"] == "required_value_missing" for i in candidate["mechanical_errors"])
    assert candidate["field_checks"]["value"]["quote_location"] == "missing"
    assert candidate["field_checks"]["value"]["literal_check"] == "failed"
    assert candidate["field_checks"]["value"]["relation_check"] == "not_confirmed"
    assert candidate["record_id"] in {r["record_id"] for r in result["candidates"]}


def test_relation_cannot_be_absent_for_null_value_with_nonempty_sources():
    case = case_named("paired_parameters")
    raw = deepcopy(case["replays"]["A"])
    raw["records"][0]["fields"]["value"]["value"] = None
    raw["records"][0]["fields"]["value"].pop("relation")
    parsed = parse_route_response_partial("A", json.dumps(raw))
    assert parsed["record_indices"] == [1]
    assert parsed["rejections"][0]["errors"][0]["loc"] == ["fields", "value"]


def test_partial_literal_is_still_an_error_in_delivered_result():
    case = case_named("before_after")
    raw = deepcopy(case["replays"]["A"])
    record = raw["records"][-1]
    record["fields"]["value"]["value"] = "30"
    record["fields"]["value"]["sources"][0]["quote"] = "30"
    record["anchors"][0]["quote"] = "30"
    result = evaluate(case, raw)
    candidate = next(r for r in result["candidates"] if r["record_id"] == record["record_id"])
    assert "partial_literal_selection" in {i["code"] for i in candidate["mechanical_errors"]}
    assert candidate["field_checks"]["value"]["literal_check"] == "failed"
    assert candidate["values"]["value"] == "30"
    assert record["record_id"] in {r["record_id"] for r in result["candidates"]}


def test_group_total_given_to_member_remains_a_priority_risk():
    case = case_named("group_total")
    raw = deepcopy(case["replays"]["A"])
    raw["records"][0]["fields"]["object"] = {
        "value": "M1", "sources": [{"ref": "table-0001-r0001-c0001", "quote": "M1"}], "relation": "row"}
    result = evaluate(case, raw)
    candidate = result["candidates"][0]
    assert "group_measurement_assigned_to_member" in {i["code"] for i in candidate["risk_diagnostics"]}
    assert result["candidates"] and result["issues"]
    assert candidate["field_checks"]["object"]["relation_check"] == "not_confirmed"


def test_position_diagnostics_remain_visible_when_geometry_is_unconfirmed():
    case = case_named("before_after")
    raw = deepcopy(case["replays"]["A"])
    raw["records"][0]["fields"]["unit"] = deepcopy(raw["records"][1]["fields"]["unit"])
    raw["records"][0]["anchors"][0]["offset"] = 99
    result = evaluate(case, raw)
    assert any(i["record_id"] == "r1" and i["code"] == "source_offset_mismatch" for i in result["issues"])
    assert result["candidates"][0]["anchors"][0]["offset"] == 99
    assert "changes" not in result and "all_changes" not in result


def test_evidence_is_exact_actual_table_input_not_additional_paper_context():
    case = case_named("group_total")
    table = build_table_input(case["source"])
    result = evaluate_route("A", case["replays"]["A"], table, case["task"])
    assert result["evidence"]["table_input"] == table
    assert result["candidates"][0]["evidence_ref"] == result["evidence"]["evidence_id"]
    result["evidence"]["table_input"]["entries"][0]["quote"] = "changed copy"
    assert table["entries"][0]["quote"] != "changed copy"


def test_same_source_candidates_remain_independent_with_all_original_ids():
    case = case_named("paired_parameters")
    raw = deepcopy(case["replays"]["A"])
    first = raw["records"][0]
    duplicate = deepcopy(first)
    duplicate["record_id"] = "duplicate-fact"
    duplicate["anchors"][0]["offset"] = 99
    duplicate["fields"]["value"]["sources"][0]["offset"] = 0
    raw["records"].append(duplicate)
    result = evaluate(case, raw)
    assert len(result["candidates"]) == 3
    separate = next(c for c in result["candidates"] if c["record_id"] == "duplicate-fact")
    assert separate["input_indices"] == [2]
    assert separate["record_ids"] == ["duplicate-fact"]
    assert len(separate["original_records"]) == 1
    assert separate["original_records"][0]["anchors"][0]["offset"] == 99


def test_unconfirmed_new_result_cannot_silently_override_checked_history():
    case = case_named("before_after")
    original = evaluate(case)
    old = deepcopy(original["candidates"][0])
    old["status"] = "source_checked"
    old["values"]["value"] = "historic-conflict"
    raw = deepcopy(case["replays"]["A"])
    raw["records"][0]["fields"]["unit"] = deepcopy(raw["records"][1]["fields"]["unit"])
    result = reconcile_results([old], evaluate(case, raw), case["task"],
                               table_input=build_table_input(case["source"]))
    assert result["history"] == [old]
    assert old not in result["candidates"]
    assert len(result["candidates"]) == len(raw["records"])
    assert any(i["code"] == "conflicting_checked_records" for i in result["risk_diagnostics"])
    assert any(c["risk_diagnostics"] for c in result["candidates"])


def mixed_measurement_case(*, conditions_global=True, same_condition=False, single=False):
    table = build_table_input(
        '<table><tr><td>Condition</td><td>Pressure/MPa</td><td>Time/min</td></tr>'
        '<tr><td>before</td><td>10</td><td>300</td></tr>'
        '<tr><td>after</td><td>11</td><td>400</td></tr></table>')

    def selection(text):
        entry = next(e for e in table["entries"] if e["quote"] == text)
        return {"ref": entry["ref"], "quote": text}

    def binding(value, text, relation):
        return {"value": value, "sources": [selection(text)], "relation": relation}

    task = {"name": "Shared synthetic conditions", "record_types": {"observation": {
        "fields": {name: {"type": "string", "required": True}
                   for name in ("condition", "pressure", "pressure_unit", "time", "time_unit")},
        "condition_fields": ["condition"] if conditions_global else [],
        "measurement_groups": {
            "pressure": {"value_field": "pressure", "unit_field": "pressure_unit", "condition_fields": ["condition"]},
            "time": {"value_field": "time", "unit_field": "time_unit", "condition_fields": ["condition"]},
        }}}}
    reading = "300" if same_condition else "400"
    record = {"record_id": "mixed", "record_type": "observation", "scope": "individual",
              "scope_sources": [], "anchors": [selection("10"), selection(reading)], "fields": {
                  "condition": binding("before", "before", "row"),
                  "pressure": binding("10", "10", "anchor"),
                  "pressure_unit": binding("MPa", "Pressure/MPa", "column"),
                  "time": binding(reading, reading, "anchor"),
                  "time_unit": binding("min", "Time/min", "column")}}
    if single:
        spec = task["record_types"]["observation"]
        del spec["measurement_groups"]["pressure"]
        for name in ("pressure", "pressure_unit"):
            del spec["fields"][name]
            del record["fields"][name]
        record["anchors"] = [selection(reading)]
    return table, task, record


@pytest.mark.parametrize("conditions_global", [False, True])
def test_mixing_two_measurement_conditions_is_a_specific_priority_risk(conditions_global):
    table, task, record = mixed_measurement_case(conditions_global=conditions_global)
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["candidates"] and result["issues"]
    assert len(result["candidates"]) == 1
    risks = result["candidates"][0]["risk_diagnostics"]
    risk = next(i for i in risks if i["code"] == "mixed_measurement_conditions")
    assert risk["field"] == "condition"
    assert risk["locally_covered_groups"] == ["pressure"]
    assert risk["locally_uncovered_groups"] == ["time"]
    assert result["candidates"][0]["semantic_verification"] == "not_run"


@pytest.mark.parametrize("single,same_condition", [(False, True), (True, False)])
def test_consistent_or_single_measurement_does_not_invent_mixed_condition_risk(single, same_condition):
    table, task, record = mixed_measurement_case(single=single, same_condition=same_condition)
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert not any(i["code"] == "mixed_measurement_conditions" for i in result["risk_diagnostics"])
    if single:
        assert result["relationship_warnings"]
    else:
        assert result["candidates"]
