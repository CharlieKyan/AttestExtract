"""Synthetic regressions for broad quotations and explicit lineage relationships.

These examples reproduce classes of integration failures without including papers
or actual model responses.
"""

from copy import deepcopy

import pytest

from attestextract.table_routes import build_table_input, evaluate_route


def sample(header="Pressure/MPa", unit="MPa", reading="17", object_name="S1"):
    source = ("| Sample | " + header + " |\n| --- | --- |\n| " + object_name + " | " + reading + " |\n")
    table = build_table_input(source)
    def ref(text, quote=None):
        cell = next(e for e in table["entries"] if e["quote"] == text)
        return {"ref": cell["ref"], "quote": quote if quote is not None else text}
    def binding(value, text, relation):
        return {"value": value, "sources": [ref(text)], "relation": relation}
    task = {"name": "Synthetic measurements", "record_types": {"measurement": {
        "fields": {name: {"type": "string", "required": True}
                   for name in ("object", "parameter", "value", "unit")},
        "identity_fields": ["object", "parameter"],
        "measurement_groups": {"reading": {"value_field": "value", "unit_field": "unit"}}}}}
    record = {"record_id": "r1", "record_type": "measurement", "scope": "individual", "scope_sources": [],
              "anchors": [ref(reading)], "fields": {
                  "object": binding(object_name, object_name, "row"),
                  "parameter": binding("Pressure", header, "column"),
                  "value": binding(reading, reading, "anchor"),
                  "unit": binding(unit, header, "column")}}
    return table, task, record


@pytest.mark.parametrize("header,unit", [("Pressure/MPa", "MPa"),
                                          ("Density/(kg/m3)", "kg/m3"),
                                          ("Inclination/(°)", "°")])
def test_broad_unit_evidence_preserves_supported_unit_instead_of_copying_header(header, unit):
    table, task, record = sample(header, unit)
    original = deepcopy(record)
    result = evaluate_route("A", {"records": [record]}, table, task)
    current = result["current_results"][0]
    assert current["values"]["unit"] == unit
    assert current["fields"]["unit"]["sources"][0]["quote"] == unit
    assert current["fields"]["unit"]["sources"][0]["ref"] == record["fields"]["unit"]["sources"][0]["ref"]
    assert {c["reason"] for c in result["changes"]} == {"source_refined"}
    assert record == original


def test_explicit_unique_unit_suffix_can_correct_an_incorrect_unit_value():
    table, task, record = sample(unit="Pa")
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["current_results"][0]["values"]["unit"] == "MPa"
    assert any(c["reason"] == "read_original_source" and c["before"] == "Pa" and c["after"] == "MPa"
               for c in result["changes"])


def test_partial_unit_evidence_does_not_expand_outside_cited_range():
    table, task, record = sample(unit="Pa")
    record["fields"]["unit"]["sources"][0]["quote"] = "Pa"
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["current_results"] == []
    assert "partial_literal_selection" in {i["code"] for i in result["issues"]}


@pytest.mark.parametrize("header", ["Pressure/MPa; Temperature/K", "This is a description"])
def test_ambiguous_or_nonunit_passage_is_not_written_into_unit(header):
    table, task, record = sample(header, "Pa")
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["current_results"] == []
    assert result["records"][0]["values"]["unit"] == "Pa"
    assert "unit_source_not_scalar" in {i["code"] for i in result["issues"]}


def test_only_mislabeled_anchor_or_cell_with_unique_axis_is_repaired():
    table, task, record = sample()
    record["fields"]["object"]["relation"] = "anchor"
    record["fields"]["unit"]["relation"] = "cell"
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert len(result["current_results"]) == 1
    corrections = {(c["field"], c["after"]) for c in result["changes"] if c["reason"] == "relation_label_corrected"}
    assert corrections == {("object", "row"), ("unit", "column")}
    assert record["fields"]["object"]["relation"] == "anchor"
    record["fields"]["object"]["relation"] = "column"
    blocked = evaluate_route("A", {"records": [record]}, table, task)
    assert blocked["current_results"] == []
    assert "relation_geometry_mismatch" in {i["code"] for i in blocked["issues"]}


@pytest.mark.parametrize("header,unit", [("Density kg/m3", "kg/m3"), ("Mass kg/m3", "kg/m3"),
                                          ("Viscosity/Pa*s", "Pa*s"), ("Viscosity/Pa·s", "Pa·s")])
def test_supported_compound_unit_is_preserved(header, unit):
    table, task, record = sample(header, unit)
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["current_results"][0]["values"]["unit"] == unit


@pytest.mark.parametrize("unit,claim", [("kg/m3", "m3"), ("m/s", "s"), ("Pa*s", "Pa"), ("Pa·s", "Pa")])
def test_compound_unit_is_read_whole_and_partial_evidence_is_rejected(unit, claim):
    table, task, record = sample("Quantity/" + unit, claim)
    selection = record["fields"]["unit"]["sources"][0]
    selection["quote"] = unit
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["current_results"][0]["values"]["unit"] == unit
    selection["quote"] = claim
    partial = evaluate_route("A", {"records": [record]}, table, task)
    assert partial["current_results"] == []
    assert "partial_literal_selection" in {i["code"] for i in partial["issues"]}


@pytest.mark.parametrize("reading,claim", [("12±3", "12"), ("10–20", "10"),
                                           ("≤10", "10"), ("12 (n=3)", "3")])
def test_unit_fix_does_not_narrow_numeric_ranges_or_qualifiers(reading, claim):
    table, task, record = sample(reading=reading)
    record["fields"]["value"]["value"] = claim
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["current_results"][0]["values"]["value"] == reading
    assert not any(c["reason"] == "source_refined" and c["field"] == "value" for c in result["changes"])


def test_a_whole_header_claim_is_not_accepted_as_a_scalar_unit():
    table, task, record = sample("Pressure/MPa", "Pressure/MPa")
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["current_results"] == []
    assert "unit_source_not_scalar" in {i["code"] for i in result["issues"]}
