"""Source-derived table examples, mutation probes, and metamorphic checks.

The JSON replays are hand-authored synthetic observations, not recorded LLM calls.
Gold values are independent of route evaluators and are never put in requests.
"""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from attestextract.table_routes import (
    build_route_request, build_table_input, evaluate_route,
    parse_route_response, reconcile_results,
)


EXAMPLES = Path(__file__).resolve().parents[1] / "src/attestextract/examples/table_routes"
CASES = [case for name in ("cases.json", "edge_cases.json")
         for case in json.loads((EXAMPLES / name).read_text(encoding="utf-8"))]
FAULT_CASES = json.loads((EXAMPLES / "failure_cases.json").read_text(encoding="utf-8"))


def case_named(name):
    return deepcopy(next(case for case in CASES if case["id"] == name))


def table(case):
    return build_table_input(case["source"], table_id=case.get("table_id"),
                             mineru_content=case.get("mineru_content"))


def evaluate(case, stage="A", parsed=None):
    return evaluate_route(stage, parsed or case["replays"][stage], table(case), case["task"])


def values(result):
    return [{"record_type": row["record_type"], "values": row["values"]}
            for row in result["candidates"]]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
@pytest.mark.parametrize("stage", ["A"])
def test_source_checked_synthetic_examples_match_independent_gold(case, stage):
    result = evaluate(case, stage)
    assert result["issues"] == []
    assert values(result) == case["gold"]
    assert result["semantic_acceptance"] == "not_expert_reviewed"


@pytest.mark.parametrize("case", FAULT_CASES, ids=lambda case: case["id"])
@pytest.mark.parametrize("stage", ["A"])
def test_user_visible_fault_demos_retain_expected_error_locations(case, stage):
    result = evaluate(case, stage)
    assert set(case["expected_diagnostics"][stage]) <= {issue["code"] for issue in result["issues"]}
    if case["id"] == "fault_literal_repair":
        assert values(result) != case["gold"]
        assert result["candidates"][-1]["values"]["value"] == "999"
        assert {i["field"] for i in result["issues"] if i["code"] == "literal_value_mismatch"} == {"value", "unit"}


def test_index_retains_physical_merge_once_and_exact_unicode_positions():
    case = case_named("before_after")
    index = table(case)
    samples = [entry for entry in index["entries"] if entry["quote"] == "S1"]
    assert len(samples) == 1
    assert samples[0]["rowspan"] == 2
    for entry in index["entries"]:
        assert case["source"][entry["start"]:entry["end"]] == entry["quote"]
    assert "hints" not in index


def test_numeric_and_unit_errors_are_reported_without_rewriting():
    case = case_named("before_after")
    proposed = deepcopy(case["replays"]["A"])
    proposed["records"][-1]["fields"]["value"]["value"] = "999"
    proposed["records"][-1]["fields"]["unit"]["value"] = "MPa"
    result = evaluate(case, parsed=proposed)
    assert values(result) != case["gold"]
    assert result["candidates"][-1]["values"]["value"] == "999"
    assert result["candidates"][-1]["values"]["unit"] == "MPa"
    assert {i["field"] for i in result["issues"] if i["code"] == "literal_value_mismatch"} == {"value", "unit"}
    assert proposed["records"][-1]["fields"]["value"]["value"] == "999"


@pytest.mark.parametrize("use_index", [False, True])
def test_history_association_preserves_current_values_and_original_history(use_index):
    case = case_named("before_after")
    result = reconcile_results(case["previous"], evaluate(case), case["task"],
                               table_input=table(case) if use_index else None)
    assert values(result) == case["gold"]
    assert result["associations"][0]["status"] == "unique"
    assert result["conflicts"][0]["code"] == "historical_value_conflict"
    assert "changes" not in result
    assert result["history"] == case["previous"]
    assert result["expert_review_status"] == "not_reviewed"


@pytest.mark.parametrize("use_index", [False, True])
def test_existing_checked_conflict_is_not_silently_overwritten(use_index):
    case = case_named("before_after")
    case["previous"][0]["status"] = "source_checked"
    result = reconcile_results(case["previous"], evaluate(case), case["task"],
                               table_input=table(case) if use_index else None)
    assert any(issue["code"] == "conflicting_checked_records" for issue in result["unresolved"])
    assert case["previous"][0] in result["history"]
    assert values(result) == case["gold"]


@pytest.mark.parametrize("mutation,expected", [
    ("unknown", "unknown_source_ref"),
    ("quote", "source_quote_missing"),
    ("unit_column", "relation_geometry_mismatch"),
])
def test_invalid_sources_identify_record_and_field(mutation, expected):
    case = case_named("before_after")
    response = deepcopy(case["replays"]["A"])
    field = "unit" if mutation == "unit_column" else "value"
    selected = response["records"][0]["fields"][field]["sources"][0]
    if mutation == "unknown":
        selected["ref"] = "nonexistent-cell"
    elif mutation == "quote":
        selected["quote"] = "not in original source"
    else:
        response["records"][0]["fields"][field] = deepcopy(response["records"][1]["fields"][field])
    result = evaluate(case, parsed=response)
    assert any(issue["code"] == expected and issue["record_id"] == "r1" and issue["field"] == field
               for issue in result["issues"])
    assert any(record["record_id"] == "r1" for record in result["candidates"])


def test_equal_number_from_other_group_is_not_valid_evidence():
    case = case_named("grouped_members")
    response = deepcopy(case["replays"]["A"])
    wrong = {"ref": "table-0001-r0003-c0002", "quote": "17"}
    response["records"][0]["anchors"] = [wrong]
    response["records"][0]["fields"]["value"]["sources"] = [wrong]
    result = evaluate(case, parsed=response)
    assert any(issue["code"] == "relation_geometry_mismatch" for issue in result["issues"])
    assert len(result["candidates"]) == len(response["records"])


def test_group_total_cannot_be_distributed_to_a_member():
    case = case_named("group_total")
    proposed = deepcopy(case["replays"]["A"])
    proposed["records"][0]["fields"]["object"] = {
        "value": "M1", "sources": [{"ref": "table-0001-r0001-c0001", "quote": "M1"}], "relation": "row"}
    result = evaluate(case, parsed=proposed)
    assert "group_measurement_assigned_to_member" in {issue["code"] for issue in result["issues"]}
    assert result["candidates"] and result["issues"]


def test_merged_number_needs_collective_or_explicit_each_scope():
    case = case_named("group_total")
    proposed = deepcopy(case["replays"]["A"])
    proposed["records"][0]["scope"] = "individual"
    proposed["records"][0]["scope_sources"] = []
    result = evaluate(case, parsed=proposed)
    assert "merged_measurement_scope_unresolved" in {issue["code"] for issue in result["issues"]}


def test_negated_each_is_not_permission_to_distribute():
    case = case_named("explicit_each")
    before, after = "Each member has mass 5 kg.", "Not every member has mass 5 kg."
    case["source"] = case["source"].replace(before, after)
    for record in case["replays"]["A"]["records"]:
        record["scope_sources"][0]["quote"] = after
    result = evaluate(case)
    assert result["candidates"] and result["issues"]
    assert result["issues"]


def test_repeated_word_requires_explicit_cell_offset():
    case = case_named("multiple_facts_in_cell")
    response = deepcopy(case["replays"]["A"])
    response["records"][0]["fields"]["unit"]["sources"][0].pop("offset")
    result = evaluate(case, parsed=response)
    assert "source_quote_ambiguous" in {issue["code"] for issue in result["issues"]}


def test_swapping_columns_preserves_facts_with_updated_source_positions():
    case = case_named("transposed")
    case["source"] = ("# Synthetic tests\n\n| Parameter | E2 | E1 |\n|---|---|---|\n"
                      "| Pressure/MPa | 22.0 | 18.0 |\n| Recovery/% | 84.2 | 71.5 |\n")
    for record in case["replays"]["A"]["records"]:
        selections = record["anchors"] + [s for b in record["fields"].values() for s in b["sources"]]
        for selection in selections:
            ref = selection["ref"]
            if ref.endswith("c0001"):
                selection["ref"] = ref[:-5] + "c0002"
            elif ref.endswith("c0002"):
                selection["ref"] = ref[:-5] + "c0001"
    assert values(evaluate(case)) == case["gold"]


def test_unrelated_table_does_not_change_target_table_results():
    case = case_named("paired_parameters")
    case["source"] += "\n# Unrelated\n\n| Sample | Value |\n|---|---|\n| Z | 999 |\n"
    case["table_id"] = "table-0001"
    assert values(evaluate(case)) == case["gold"]


@pytest.mark.parametrize("text", ['{"records":[],"records":[]}', '{"records":NaN}', '{"records":'])
def test_malformed_json_is_not_repaired_or_silently_accepted(text):
    with pytest.raises(ValueError):
        parse_route_response("A", text)


def test_invalid_physical_grid_does_not_produce_guessed_cells():
    with pytest.raises(ValueError, match="physical cell grid"):
        build_table_input("<table><tr><td>A</td><td>1</td></tr><tr><td>B</td></tr></table>")


def test_narrow_each_quote_cannot_hide_negation_in_same_visible_entry():
    case = case_named("explicit_each")
    case["source"] = case["source"].replace("Each member has mass 5 kg.", "Not every member has mass 5 kg.")
    for record in case["replays"]["A"]["records"]:
        record["scope_sources"][0]["quote"] = "every"
    original = deepcopy(case["replays"]["A"])
    result = evaluate(case)
    assert "distributive_wording_negated_or_conditional" in {i["code"] for i in result["issues"]}
    assert len(result["candidates"]) == len(original["records"])
    assert all(r["scope_sources"][0]["quote"] == "every" for r in result["candidates"])
    assert case["replays"]["A"] == original
