"""Independent review probes: wrong ownership and partial literals must not pass."""

import pytest

from attestextract.table_routes import build_table_input, evaluate_route


def selection(table, cell_text, quote=None):
    cell = next(item for item in table["entries"] if item["quote"] == cell_text)
    return {"ref": cell["ref"], "quote": quote if quote is not None else cell_text}


def binding(table, value, cell_text, relation, quote=None):
    return {"value": value, "sources": [selection(table, cell_text, quote)], "relation": relation}


def task(fields, groups, *, conditions=()):
    return {"name": "independent scientific table probe", "record_types": {"observation": {
        "fields": {name: {"type": "string", "required": True} for name in fields},
        "identity_fields": ["object"], "condition_fields": list(conditions), "measurement_groups": groups}}}


def test_two_value_anchors_cannot_join_before_pressure_to_after_time():
    table = build_table_input(
        '<table><tr><td>Sample</td><td>Condition</td><td>Pressure/MPa</td><td>Time/min</td></tr>'
        '<tr><td rowspan="2">S1</td><td>before</td><td>10</td><td>300</td></tr>'
        '<tr><td>after</td><td>11</td><td>400</td></tr></table>')
    spec = task(("object", "condition", "pressure", "pressure_unit", "time", "time_unit"), {
        "pressure": {"value_field": "pressure", "unit_field": "pressure_unit", "condition_fields": ["condition"]},
        "time": {"value_field": "time", "unit_field": "time_unit", "condition_fields": ["condition"]}},
        conditions=("condition",))
    record = {"record_id": "mixed", "record_type": "observation", "scope": "individual", "scope_sources": [],
              "anchors": [selection(table, "10"), selection(table, "400")], "fields": {
                  "object": binding(table, "S1", "S1", "row"),
                  "condition": binding(table, "before", "before", "row"),
                  "pressure": binding(table, "10", "10", "anchor"),
                  "pressure_unit": binding(table, "MPa", "Pressure/MPa", "column", "MPa"),
                  "time": binding(table, "400", "400", "anchor"),
                  "time_unit": binding(table, "min", "Time/min", "column", "min")}}
    result = evaluate_route("A", {"records": [record]}, table, spec)
    assert result["candidates"] and result["issues"]
    assert any(issue.get("field") == "condition" for issue in result["issues"])


def test_horizontal_group_total_cannot_be_assigned_to_one_member_header():
    table = build_table_input(
        '<table><tr><td>Group</td><td>First</td><td>Second</td></tr>'
        '<tr><td>Total length/m</td><td colspan="2">100</td></tr></table>')
    spec = task(("object", "value", "unit"), {"length": {"value_field": "value", "unit_field": "unit"}})
    record = {"record_id": "member", "record_type": "observation", "scope": "group",
              "scope_sources": [selection(table, "Total length/m", "Total length")],
              "anchors": [selection(table, "100")], "fields": {
                  "object": binding(table, "First", "First", "column"),
                  "value": binding(table, "100", "100", "anchor"),
                  "unit": binding(table, "m", "Total length/m", "row", "m")}}
    result = evaluate_route("A", {"records": [record]}, table, spec)
    assert result["candidates"] and result["issues"]
    assert any(issue.get("field") == "object" for issue in result["issues"])


def simple_record(source_value="100", claimed_value="10", selected_value="10", selected_unit="MPa"):
    table = build_table_input('<table><tr><td>Sample</td><td>Pressure/MPa</td></tr>'
                              f'<tr><td>A</td><td>{source_value}</td></tr></table>')
    spec = task(("object", "value", "unit"), {"pressure": {"value_field": "value", "unit_field": "unit"}})
    record = {"record_id": "literal", "record_type": "observation", "scope": "individual", "scope_sources": [],
              "anchors": [selection(table, source_value, selected_value)], "fields": {
                  "object": binding(table, "A", "A", "row"),
                  "value": binding(table, claimed_value, source_value, "anchor", selected_value),
                  "unit": binding(table, selected_unit, "Pressure/MPa", "column", selected_unit)}}
    return table, spec, record


def test_numeric_substring_cannot_turn_100_into_10():
    table, spec, record = simple_record()
    result = evaluate_route("A", {"records": [record]}, table, spec)
    assert result["candidates"] and result["issues"]
    assert any(issue.get("field") == "value" for issue in result["issues"])


def test_unit_substring_cannot_turn_mpa_into_pa():
    table, spec, record = simple_record(claimed_value="100", selected_value="100", selected_unit="Pa")
    result = evaluate_route("A", {"records": [record]}, table, spec)
    assert result["candidates"] and result["issues"]
    assert any(issue.get("field") == "unit" for issue in result["issues"])


def test_complete_literal_exposes_an_incorrect_value_without_correction():
    table, spec, record = simple_record(claimed_value="999", selected_value="100")
    result = evaluate_route("A", {"records": [record]}, table, spec)
    assert [item["values"]["value"] for item in result["candidates"]] == ["999"]
    assert any(issue["code"] == "literal_value_mismatch" for issue in result["issues"])


@pytest.mark.parametrize("source_value,claim,field_type", [
    ("1e-999", 0, "number"),
    ("9007199254740993", 9007199254740992, "integer"),
    ("9007199254740993", 9007199254740992.0, "number"),
])
def test_numeric_comparison_does_not_accept_float_underflow_or_rounding(source_value, claim, field_type):
    table, spec, record = simple_record(source_value=source_value, claimed_value=claim,
                                        selected_value=source_value)
    spec["record_types"][record["record_type"]]["fields"]["value"]["type"] = field_type
    result = evaluate_route("A", {"records": [record]}, table, spec)
    candidate = result["candidates"][0]
    assert candidate["values"]["value"] == claim
    assert candidate["field_checks"]["value"]["literal_check"] == "failed"
    assert any(i["code"] == "literal_value_mismatch" for i in result["issues"])
