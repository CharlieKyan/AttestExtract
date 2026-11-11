"""Synthetic negative controls for model-chosen partial evidence, not just values."""

from copy import deepcopy

import pytest

from attestextract.table_routes import build_table_input, evaluate_route


def task():
    return {"name": "Synthetic source boundaries", "record_types": {"measurement": {
        "fields": {name: {"type": "string", "required": True} for name in ("object", "value", "unit")},
        "identity_fields": ["object"],
        "measurement_groups": {"length": {"value_field": "value", "unit_field": "unit"}}}}}


def selection(table, text, quote=None):
    cell = next(entry for entry in table["entries"] if entry["quote"] == text)
    return {"ref": cell["ref"], "quote": quote if quote is not None else text, "offset": None}


def binding(table, value, text, relation, quote=None):
    return {"value": value, "sources": [selection(table, text, quote)], "relation": relation}


def measurement(reading, selected=None):
    table = build_table_input('| Sample | Length/m |\n| --- | --- |\n| S1 | ' + reading + ' |\n')
    selected = reading if selected is None else selected
    record = {"record_id": "r1", "record_type": "measurement", "scope": "individual", "scope_sources": [],
              "anchors": [selection(table, reading)], "fields": {
                  "object": binding(table, "S1", "S1", "row"),
                  "value": binding(table, selected, reading, "anchor", selected),
                  "unit": binding(table, "m", "Length/m", "column", "m")}}
    return table, record


@pytest.mark.parametrize("reading,selected", [
    ("12±3", "12"), ("12±3", "3"), ("12 +/- 3", "12"), (r"12 \pm 3", "3"),
    ("10–20", "10"), ("10–20", "20"), ("10-20", "10"), ("10 to 20", "20"),
    ("10至20", "10"), ("≤10", "10"), (">=10", "10"), (r"\leq 10", "10"),
    ("≈10", "10"), ("12 (n=3)", "3"), ("12 (n=3)", "12"),
    ("12（n=3）", "3"), ("1e-3 ± 2e-4", "1e-3"),
    (r"3 \times 10^{-15}", "3"), ("3 × 10^-15", "3"), ("10 ^ 3", "10"),
    (r"3 \times 10^{-15}", "10"), (r"3 \times 10^{-15}", "-15"),
    (r"3 \times 10^{-15}", "15"), ("10 ^ 3", "3"),
    (r"3 \times 10 ^ { -15 }", "3"), ("3 * 10^-15", "3"),
])
def test_selected_number_cannot_drop_an_explicit_qualifier(reading, selected):
    table, record = measurement(reading, selected)
    before = deepcopy(record)
    result = evaluate_route("A", {"records": [record]}, table, task())
    assert result["candidates"] and result["issues"]
    assert "partial_literal_selection" in {item["code"] for item in result["issues"]}
    assert result["records"][0]["fields"]["value"]["sources"] == record["fields"]["value"]["sources"]
    assert record == before


@pytest.mark.parametrize("reading", ["12±3", "10–20", "≤10", "12 (n=3)", r"12 \pm 3", "-12", "1e-3",
                                      r"3 \times 10^{-15}", "3 × 10^-15", "10 ^ 3",
                                      r"3 \times 10 ^ { -15 }", "3 * 10^-15"])
def test_complete_measurement_expression_is_preserved(reading):
    table, record = measurement(reading)
    result = evaluate_route("A", {"records": [record]}, table, task())
    assert result["issues"] == []
    assert result["candidates"][0]["values"]["value"] == reading


@pytest.mark.parametrize("reading,selected", [("first=12; second=18", "12"),
                                           ("first=12; second=18", "18"),
                                           ("first=12±3; second=18", "18"),
                                           ("12 (dry)", "12"),
                                           ("<b>12</b>", "12"),
                                           ("<span class='reading'>12</span>", "12"),
                                           ("length=12; n=3", "3")])
def test_unrelated_same_cell_fact_and_non_numeric_annotation_remain_selectable(reading, selected):
    table, record = measurement(reading, selected)
    result = evaluate_route("A", {"records": [record]}, table, task())
    assert result["issues"] == []
    assert result["candidates"][0]["values"]["value"] == selected


def group_record(label="A and B", value="A and B", quote=None):
    table = build_table_input('<table><tr><td>Group</td><td>Member</td><td>Total length/m</td></tr>'
                              '<tr><td rowspan="2">' + label + '</td><td>First</td>'
                              '<td rowspan="2">100</td></tr><tr><td>Second</td></tr></table>')
    record = {"record_id": "r1", "record_type": "measurement", "scope": "group",
              "scope_sources": [selection(table, label), selection(table, "Total length/m")],
              "anchors": [selection(table, "100")], "fields": {
                  "object": binding(table, value, label, "group", quote),
                  "value": binding(table, "100", "100", "anchor"),
                  "unit": binding(table, "m", "Total length/m", "column", "m")}}
    return table, record


@pytest.mark.parametrize("label,value,quote", [
    ("A and B", "A", "A"), ("A and B", "A", "A and B"),
    ("A and B", "A and BC", "A and B"), ("A and B and C", "A and B", "A and B and C"),
    ("A & B", "A", "A & B"), ("甲和乙", "甲", "甲和乙"), ("甲、乙", "甲", "甲"),
])
def test_group_total_cannot_be_assigned_to_one_cited_member(label, value, quote):
    table, record = group_record(label, value, quote)
    before = deepcopy(record)
    result = evaluate_route("A", {"records": [record]}, table, task())
    assert result["candidates"] and result["issues"]
    assert "group_identity_narrowed" in {item["code"] for item in result["issues"]}
    assert record == before


@pytest.mark.parametrize("label,value", [("A and B", "A and B"), ("A and B", "A and B samples"),
                                        ("A and B", "A 和 B"), ("甲和乙", "甲和乙"),
                                        ("G1", "Group G1")])
def test_complete_group_identity_and_supported_expansion_remain_usable(label, value):
    table, record = group_record(label, value)
    result = evaluate_route("A", {"records": [record]}, table, task())
    assert result["issues"] == []
    assert result["candidates"][0]["values"]["object"] == value


def test_shared_identity_is_not_mistaken_for_a_collective_measurement():
    table = build_table_input('<table><tr><td>Sample</td><td>Length/m</td></tr>'
                              '<tr><td rowspan="2">A and B blend</td><td>10</td></tr>'
                              '<tr><td>20</td></tr></table>')
    records = [{"record_id": "r" + value, "record_type": "measurement", "scope": "individual",
                "scope_sources": [], "anchors": [selection(table, value)], "fields": {
                    "object": binding(table, "A and B blend", "A and B blend", "row"),
                    "value": binding(table, value, value, "anchor"),
                    "unit": binding(table, "m", "Length/m", "column", "m")}} for value in ("10", "20")]
    result = evaluate_route("A", {"records": records}, table, task())
    assert result["issues"] == []
    assert len(result["candidates"]) == 2
