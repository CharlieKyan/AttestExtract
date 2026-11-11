"""Synthetic checks for exact local position observation and source-range identity."""

from copy import deepcopy
import json

import pytest

from attestextract.table_routes import (
    build_route_request, build_table_input, evaluate_route, parse_route_response,
    reconcile_results,
)


def sample(*, reading="2600", owner="A", condition="before", header="Density/(kg/m3)"):
    source = ("# Group A and B\n\n| Object | Condition | " + header + " |\n"
              "| --- | --- | --- |\n| " + owner + " | " + condition + " | " + reading + " |\n")
    table = build_table_input(source)

    def selection(text):
        entry = next(e for e in table["entries"] if e["quote"] == text)
        return {"ref": entry["ref"], "quote": text, "offset": None}

    def binding(value, text, relation):
        return {"value": value, "sources": [selection(text)], "relation": relation}

    task = {"name": "Synthetic source positions", "record_types": {"observation": {
        "fields": {name: {"type": "string", "required": True}
                   for name in ("object", "condition", "value", "unit")},
        "identity_fields": ["object"], "condition_fields": ["condition"],
        "measurement_groups": {"density": {"value_field": "value", "unit_field": "unit"}}}}}
    record = {"record_id": "r1", "record_type": "observation", "scope": "individual",
              "scope_sources": [], "anchors": [selection(reading)], "fields": {
                  "object": binding(owner, owner, "row"),
                  "condition": binding(condition, condition, "row"),
                  "value": binding(reading, reading, "anchor"),
                  "unit": binding("kg/m3", header, "column")}}
    return table, task, record


def evaluate(table, task, *records):
    return evaluate_route("A", {"records": list(records)}, table, task)


def codes(result):
    return {issue["code"] for issue in result["issues"]}


def test_wrong_offsets_are_located_in_every_reference_kind_without_changing_output():
    table, task, record = sample(owner="A and B")
    record["scope"] = "group"
    heading = next(e for e in table["entries"] if e["kind"] == "heading")
    record["scope_sources"] = [{"ref": heading["ref"], "quote": heading["quote"], "offset": 3}]
    record["anchors"][0]["offset"] = 9
    record["fields"]["value"]["sources"][0]["offset"] = 8
    record["fields"]["unit"]["sources"][0]["offset"] = 5
    raw = json.dumps({"records": [record]}, ensure_ascii=False)
    parsed = parse_route_response("A", raw)
    original, original_table = deepcopy(parsed), deepcopy(table)

    result = evaluate_route("A", parsed, table, task)

    assert codes(result) == {"source_offset_mismatch"}
    assert result["candidates"][0]["values"]["value"] == "2600"
    assert result["candidates"][0]["values"]["unit"] == "kg/m3"
    diagnostics = [i for i in result["issues"] if i["code"] == "source_offset_mismatch"]
    assert {i["path"] for i in diagnostics} == {
        "anchors[0]", "scope_sources[0]", "fields.value.sources[0]", "fields.unit.sources[0]"}
    assert all(i["record_id"] == "r1" and i["source"]["offset"] > 0 and i["located_offset"] == 0
               for i in diagnostics)
    assert result["candidates"][0]["fields"] == original["records"][0]["fields"]
    assert result["candidates"][0]["literal_evidence"]["unit"]["selected_quote"] == "kg/m3"
    assert "changes" not in result
    assert parsed == original and table == original_table
    assert json.loads(raw) == {"records": [record]}


def test_absent_offset_resolves_unique_quote_without_inventing_a_model_correction():
    table, task, record = sample()
    result = evaluate(table, task, record)
    assert result["issues"] == []
    assert "source_offset_mismatch" not in codes(result)
    assert result["records"][0]["anchors"][0]["offset"] is None


def test_unique_substring_location_uses_original_unicode_character_positions():
    table, task, record = sample(reading="测量: 2600")
    record["anchors"][0].update(quote="2600", offset=99)
    record["fields"]["value"].update(value="2600", sources=deepcopy(record["anchors"]))
    result = evaluate(table, task, record)
    assert codes(result) == {"source_offset_mismatch"}
    current = result["candidates"][0]
    assert current["anchors"][0]["offset"] == 99
    assert current["fields"]["value"]["sources"][0]["offset"] == 99
    anchor = current["resolved_anchors"][0]
    assert anchor["selected_start"] == anchor["start"] + 4
    assert anchor["selected_end"] == anchor["start"] + 8


def test_valid_explicit_offset_disambiguates_repeated_measurements():
    table, task, record = sample(reading="17; 17")
    record["anchors"][0].update(quote="17", offset=4)
    record["fields"]["value"].update(value="17", sources=deepcopy(record["anchors"]))
    result = evaluate(table, task, record)
    assert result["issues"] == []
    assert result["candidates"][0]["anchors"][0]["offset"] == 4
    assert "source_offset_mismatch" not in codes(result)


@pytest.mark.parametrize("owner,quote", [("A; A", "A"), ("aaa", "aa")])
@pytest.mark.parametrize("offset", [None, 99])
def test_repeated_and_overlapping_quotes_are_not_guessed(owner, quote, offset):
    table, task, record = sample(owner=owner)
    record["fields"]["object"]["sources"][0].update(quote=quote, offset=offset)
    result = evaluate(table, task, record)
    assert result["candidates"] and result["issues"]
    assert "source_quote_ambiguous" in codes(result)
    assert result["candidates"][0]["fields"]["object"]["sources"][0]["offset"] == offset


def test_valid_offset_remains_valid_when_other_occurrences_overlap():
    table, task, record = sample(owner="aaa")
    record["fields"]["object"].update(value="aa")
    record["fields"]["object"]["sources"][0].update(quote="aa", offset=1)
    result = evaluate(table, task, record)
    assert result["issues"] == []
    assert result["candidates"][0]["fields"]["object"]["sources"][0]["offset"] == 1


@pytest.mark.parametrize("mutation,expected", [
    ("unknown_ref", "unknown_source_ref"),
    ("missing_quote", "source_quote_missing"),
    ("quote_in_other_cell", "source_quote_missing"),
])
def test_position_observation_never_searches_another_cell_or_invents_a_quote(mutation, expected):
    table, task, record = sample()
    selection = record["fields"]["object"]["sources"][0]
    selection["offset"] = 99
    if mutation == "unknown_ref":
        selection["ref"] = "missing-cell"
    else:
        selection["quote"] = "missing text" if mutation == "missing_quote" else "2600"
    result = evaluate(table, task, record)
    assert result["candidates"] and result["issues"]
    assert expected in codes(result)
    assert record["fields"]["object"]["sources"][0]["offset"] == 99


def test_equal_ranges_keep_absent_zero_and_incorrect_offsets_separately():
    table, task, record = sample()
    records = []
    for index, offset in enumerate((None, 0, 99)):
        copy = deepcopy(record)
        copy["record_id"] = f"r{index}"
        copy["anchors"][0]["offset"] = offset
        copy["fields"]["value"]["sources"][0]["offset"] = offset
        records.append(copy)
    result = evaluate(table, task, *records)
    assert codes(result) == {"source_offset_mismatch"}
    assert len(result["records"]) == len(result["candidates"]) == 3
    assert [r["anchors"][0]["offset"] for r in result["candidates"]] == [None, 0, 99]
    assert all(record["status"] == "extracted" for record in result["records"])


def test_equal_numbers_at_different_source_positions_remain_distinct():
    table, task, record = sample(reading="17; 17")
    records = []
    for index, offset in enumerate((0, 4)):
        copy = deepcopy(record)
        copy["record_id"] = f"r{index}"
        copy["anchors"][0].update(quote="17", offset=offset)
        copy["fields"]["value"].update(value="17", sources=deepcopy(copy["anchors"]))
        records.append(copy)
    result = evaluate(table, task, *records)
    assert result["issues"] == []
    assert len(result["candidates"]) == 2


def test_shared_source_position_does_not_merge_distinct_conditions():
    table, task, record = sample(condition="before; after")
    records = []
    for condition in ("before", "after"):
        copy = deepcopy(record)
        copy["record_id"] = condition
        copy["fields"]["condition"]["value"] = condition
        copy["fields"]["condition"]["sources"][0]["quote"] = condition
        records.append(copy)
    result = evaluate(table, task, *records)
    assert result["issues"] == []
    assert len(result["candidates"]) == 2


@pytest.mark.parametrize("offset", [None, 0, 99])
def test_old_candidate_links_by_resolved_range_without_changing_history(offset):
    table, task, record = sample()
    previous = [deepcopy(record)]
    previous[0]["anchors"][0]["offset"] = offset
    previous[0]["fields"]["value"]["value"] = "999"
    original = deepcopy(previous)
    evaluation = evaluate(table, task, record)
    result = reconcile_results(previous, evaluation, task, table_input=table)
    assert len(result["candidates"]) == 1
    assert result["associations"][0]["status"] == "unique"
    assert result["conflicts"][0]["code"] == "historical_value_conflict"
    assert result["candidates"][0]["values"]["value"] == "2600"
    assert result["history"] == previous == original


def test_checked_prior_conflict_survives_canonical_range_association():
    table, task, record = sample()
    prior = deepcopy(evaluate(table, task, record)["candidates"][0])
    prior["record_id"] = "previous-r1"
    prior["status"] = "source_checked"
    prior["anchors"][0]["offset"] = 99
    prior["values"]["value"] = "999"
    original = deepcopy(prior)
    result = reconcile_results([prior], evaluate(table, task, record), task, table_input=table)
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["record_id"] == "r1"
    assert result["candidates"][0]["values"]["value"] == "2600"
    assert "conflicting_checked_records" in {i["code"] for i in result["unresolved"]}
    conflict = next(i for i in result["unresolved"] if i["code"] == "conflicting_checked_records")
    assert conflict["history_record_id"] == "previous-r1" and conflict["record_id"] == "r1"
    assert result["history"] == [original] and prior == original


def test_checked_prior_same_fact_does_not_duplicate_after_position_observation():
    table, task, record = sample()
    prior = deepcopy(evaluate(table, task, record)["candidates"][0])
    prior["anchors"][0]["offset"] = 99
    original = deepcopy(prior)
    result = reconcile_results([prior], evaluate(table, task, record), task, table_input=table)
    assert len(result["candidates"]) == 1 and result["unresolved"] == []
    assert result["associations"][0]["status"] == "unique"
    assert result["conflicts"] == []
    assert result["history"] == [original] and prior == original


@pytest.mark.parametrize("field,whole,first,second", [
    ("condition", "before; after", "before", "after"),
    ("object", "A; B", "A", "B"),
])
def test_historical_different_conditions_and_owners_share_value_without_being_conflicts(field, whole, first, second):
    table, task, record = sample(**({"condition": whole} if field == "condition" else {"owner": whole}))
    record["fields"][field]["value"] = first
    record["fields"][field]["sources"][0]["quote"] = first
    prior = evaluate(table, task, record)["candidates"][0]
    record["record_id"] = "r2"
    record["anchors"][0]["offset"] = 0
    record["fields"][field]["value"] = second
    record["fields"][field]["sources"][0]["quote"] = second
    result = reconcile_results([prior], evaluate(table, task, record), task, table_input=table)
    assert result["conflicts"] == []
    assert result["associations"][0]["status"] == "unmatched"
    assert {r["values"][field] for r in result["candidates"]} == {second}
    assert result["history"][0]["values"][field] == first


def test_historical_different_scopes_share_value_without_overwriting_each_other():
    table, task, record = sample(owner="A and B")
    prior = evaluate(table, task, record)["candidates"][0]
    record["record_id"] = "group"
    record["scope"] = "group"
    record["scope_sources"] = deepcopy(record["fields"]["object"]["sources"])
    record["anchors"][0]["offset"] = 0
    result = reconcile_results([prior], evaluate(table, task, record), task, table_input=table)
    assert result["conflicts"] == []
    assert result["associations"][0]["status"] == "unmatched"
    assert {r["scope"] for r in result["candidates"]} == {"group"}
    assert result["history"][0]["scope"] == "individual"


def test_no_index_preserves_strict_legacy_anchor_matching():
    table, task, record = sample()
    prior = deepcopy(record)
    prior["anchors"][0]["offset"] = 0
    result = reconcile_results([prior], evaluate(table, task, record), task)
    assert "previous_record_not_uniquely_resolved" in {i["code"] for i in result["unresolved"]}
    assert result["associations"][0]["status"] == "unmatched"


def test_unresolvable_historical_anchors_are_not_equal_to_other_unresolvable_anchors():
    table, task, record = sample()
    prior = deepcopy(evaluate(table, task, record)["candidates"][0])
    prior["anchors"][0]["ref"] = "missing-cell"
    second = deepcopy(prior)
    second["record_id"] = "old-2"
    result = reconcile_results([prior, second], {"schema_version": "table-route-v3", "candidates": [], "issues": []},
                               task, table_input=table)
    assert result["candidates"] == []
    assert len(result["history"]) == 2
    assert all(a["status"] == "unmatched" for a in result["associations"])


@pytest.mark.parametrize("problem,expected", [
    ("range", "partial_literal_selection"),
    ("unit", "partial_literal_selection"),
    ("group", "group_identity_narrowed"),
])
def test_successful_position_observation_still_runs_literal_and_group_guards(problem, expected):
    table, task, record = sample(reading="12±3" if problem == "range" else "2600",
                                 owner="A and B" if problem == "group" else "A",
                                 header="Pressure/MPa" if problem == "unit" else "Density/(kg/m3)")
    field, quote = {"range": ("value", "12"), "unit": ("unit", "Pa"), "group": ("object", "A")}[problem]
    record["fields"][field]["value"] = quote
    record["fields"][field]["sources"][0].update(quote=quote, offset=99)
    if problem == "group":
        record["scope"] = "group"
        record["scope_sources"] = [{**record["fields"]["object"]["sources"][0], "quote": "A and B"}]
    result = evaluate(table, task, record)
    assert result["candidates"] and result["issues"] and expected in codes(result)
    assert any(i["code"] == "source_offset_mismatch" and i["field"] == field for i in result["issues"])
    assert result["candidates"][0]["fields"][field]["sources"][0]["offset"] == 99


@pytest.mark.parametrize("stage", ["B1", "B2", "unknown"])
def test_removed_routes_cannot_be_requested_parsed_or_evaluated(stage):
    table, task, record = sample()
    with pytest.raises(ValueError, match="Only table route A"):
        build_route_request(stage, task, table)
    with pytest.raises(ValueError, match="Only table route A"):
        parse_route_response(stage, '{"records":[]}')
    with pytest.raises(ValueError, match="Only table route A"):
        evaluate_route(stage, {"records": [record]}, table, task)
