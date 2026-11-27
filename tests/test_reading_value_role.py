import json

import pytest

from niceextract import extract
from niceextract.dataset_snapshot import checked_snapshot
from niceextract.table_routes import build_table_input
from test_source_statement_protocol import FixtureBackend


TASK = {"prompt_description": "Record the grade of each test item.", "field_list": {"grade": "Material grade."},
        "field_types": {"grade": {"kind": "text"}}}


@pytest.mark.parametrize("table", [False, True])
@pytest.mark.parametrize("supplied,expected", [("label", "label"), ("unrecognized", "unknown")])
def test_source_reading_role_follows_original_record_through_export(table, supplied, expected):
    source = "| Item | Grade |\n|---|---|\n| Q | 003 |" if table else "The grade of item Q was 003."
    if table:
        cells = {e["quote"].strip(): {"ref": e["ref"], "quote": e["quote"]} for e in build_table_input(source)["entries"]}
        record = {"record_id": "g1", "scope": "individual", "scope_sources": [], "anchors": [cells["003"]],
                  "fields": {"entity": {"value": "Q", "relation": "row", "sources": [cells["Q"]]},
                             "value": {"value": "003", "relation": "anchor", "sources": [cells["003"]]}}}
    else:
        record = {"values": {"entity": "item Q", "value": "003", "unit": None, "conditions": None},
                  "quotes": {"entity": source, "value": source, "unit": "", "conditions": ""}}
    record.update(record_type="grade", statement_basis="reported", reported_use="unspecified",
                  source_property="grade", value_role=supplied)
    backend = FixtureBackend({"records": [record]})
    result = extract(source, TASK, backend=backend)
    schema = backend.requests[0].output_schema
    assert '"value_role"' in json.dumps(schema)
    obs, = checked_snapshot(source, result.model_dump(mode="json"), None)[0]["observations"]
    assert (obs["value"], obs["value_role"]) == ("003", expected)


def test_typed_concentration_accepts_a_json_number_with_separate_unit():
    task = {"prompt_description": "Read sample concentrations.", "field_list": {"c": "Concentration."},
            "field_types": {"c": {"kind": "numeric"}}}
    source = "Sample Q contained 5 ppm."
    record = {"record_type": "c", "value_role": "quantity", "statement_basis": "reported", "reported_use": "measured",
              "values": {"entity": "Sample Q", "value": 5, "unit": "ppm", "conditions": None},
              "quotes": {"entity": source, "value": source, "unit": source, "conditions": ""}}
    result = extract(source, task, backend=FixtureBackend({"records": [record]}), dataset_options={})
    obs, = result.dataset["observations"]
    assert obs["row_ids"] and (obs["numeric_value"], obs["numeric_unit"]) == ({"value": "5"}, "ppm")
