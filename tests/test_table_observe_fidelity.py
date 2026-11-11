"""Independent contract probes for delivery without automatic correction."""
from copy import deepcopy
import json

import pytest

from attestextract.table_routes import (
    build_table_input, evaluate_route, parse_route_response_partial, reconcile_results,
)


def fixture():
    table = build_table_input("| Sample | Reading/m |\n| --- | --- |\n| S1 | length=3m |\n")

    def cite(text, offset=0):
        entry = next(e for e in table["entries"] if e["quote"] == text)
        return {"ref": entry["ref"], "quote": text, "offset": offset}

    record = {"record_id": "first", "record_type": "measurement", "scope": "individual",
              "scope_sources": [], "anchors": [cite("length=3m")], "fields": {
                  "object": {"value": "S1", "sources": [cite("S1")], "relation": "row"},
                  "value": {"value": "3", "sources": [cite("length=3m")], "relation": "anchor"},
                  "unit": {"value": "m", "sources": [cite("Reading/m", 2)], "relation": "cell"},
              }}
    task = {"name": "Synthetic immutable delivery", "record_types": {"measurement": {
        "fields": {name: {"type": "string", "required": True} for name in ("object", "value", "unit")},
        "identity_fields": ["object"],
        "measurement_groups": {"reading": {"value_field": "value", "unit_field": "unit"}},
    }}}
    return table, task, record


def evaluate(table, task, records):
    parsed = parse_route_response_partial("A", json.dumps({"records": records}))
    original = deepcopy(parsed)
    result = evaluate_route("A", parsed, table, task)
    assert parsed == original
    assert len(result["candidates"]) == len(parsed["records"])
    for candidate, raw in zip(result["candidates"], parsed["records"]):
        assert {key: candidate[key] for key in raw} == raw
        assert candidate["values"] == {key: field["value"] for key, field in raw["fields"].items()}
        assert candidate["semantic_verification"] == "not_run"
    return result


def test_broad_sentence_and_wrong_unit_offset_never_replace_claim_or_source():
    table, task, record = fixture()
    result = evaluate(table, task, [record])
    candidate = result["candidates"][0]
    assert candidate["values"]["value"] == "3"
    assert candidate["fields"]["unit"]["sources"][0]["offset"] == 2
    assert candidate["fields"]["unit"]["relation"] == "cell"
    assert candidate["resolved_fields"]["unit"]
    assert candidate["field_checks"]["value"]["literal_check"] == "passed"
    assert candidate["field_checks"]["unit"]["literal_check"] == "passed"
    assert candidate["field_checks"]["unit"]["relation_check"] != "locally_supported"
    assert candidate["issues"]
    assert result["schema_version"] == "table-route-v3"
    assert result["processing_policy"] == "observe_only"
    assert not ({"current_results", "changes", "all_changes"} & result.keys())


@pytest.mark.parametrize("mutation", ["unknown_field", "missing_field", "wrong_type", "unknown_record_type"])
def test_schema_mismatch_is_visible_without_rewriting_legal_model_records(mutation):
    table, task, record = fixture()
    if mutation == "unknown_field":
        record["fields"]["extra_observation"] = deepcopy(record["fields"]["object"])
    elif mutation == "missing_field":
        del record["fields"]["unit"]
    elif mutation == "wrong_type":
        record["fields"]["value"]["value"] = True
    else:
        record["record_type"] = "unrecognized_measurement"
    result = evaluate(table, task, [record])
    assert result["issues"]
    assert result["candidates"][0]["status"] == "extracted"
    if mutation == "missing_field":
        assert "unit" not in result["candidates"][0]["fields"]
    if mutation == "wrong_type":
        assert result["candidates"][0]["fields"]["value"]["value"] is True
        assert result["candidates"][0]["field_checks"]["value"]["literal_check"] != "passed"


def test_wrong_numeric_claim_stays_wrong_and_is_explicitly_detected():
    table, task, record = fixture()
    record["fields"]["value"]["value"] = "999"
    result = evaluate(table, task, [record])
    candidate = result["candidates"][0]
    assert candidate["values"]["value"] == "999"
    assert candidate["field_checks"]["value"]["literal_check"] == "failed"
    assert any(i.get("field") == "value" for i in candidate["issues"])


def test_two_identical_facts_keep_both_ids_and_input_order():
    table, task, record = fixture()
    second = deepcopy(record)
    second["record_id"] = "second"
    result = evaluate(table, task, [record, second])
    assert [r["record_id"] for r in result["candidates"]] == ["first", "second"]
    assert [r["input_indices"] for r in result["candidates"]] == [[0], [1]]


def test_historical_checked_value_cannot_replace_or_append_to_current_delivery():
    table, task, record = fixture()
    result = evaluate(table, task, [record])
    previous = deepcopy(result["candidates"][0])
    previous["record_id"] = "historical"
    previous["status"] = "source_checked"
    previous["fields"]["value"]["value"] = "999"
    previous["values"]["value"] = "999"
    frozen_previous = deepcopy(previous)
    frozen_result = deepcopy(result)
    reconciled = reconcile_results([previous], result, task, table_input=table)
    assert previous == frozen_previous and result == frozen_result
    assert reconciled["history"] == [frozen_previous]
    assert len(reconciled["candidates"]) == 1
    assert reconciled["candidates"][0]["fields"] == result["candidates"][0]["fields"]
    assert reconciled["candidates"][0]["values"]["value"] == "3"
    assert reconciled["conflicts"]
    assert not ({"current_results", "changes", "all_changes"} & reconciled.keys())
