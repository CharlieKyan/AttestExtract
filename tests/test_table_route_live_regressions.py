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
    current = result["candidates"][0]
    assert current["values"]["unit"] == unit
    assert current["fields"]["unit"]["sources"][0]["quote"] == header
    assert current["literal_evidence"]["unit"]["selected_quote"] == unit
    assert current["fields"]["unit"]["sources"][0]["ref"] == record["fields"]["unit"]["sources"][0]["ref"]
    assert current["field_checks"]["unit"]["literal_check"] == "passed"
    assert "changes" not in result
    assert record == original


def test_explicit_unit_suffix_reports_but_does_not_correct_incorrect_value():
    table, task, record = sample(unit="Pa")
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["candidates"][0]["values"]["unit"] == "Pa"
    assert result["candidates"][0]["field_checks"]["unit"]["literal_check"] == "failed"
    assert "partial_literal_selection" in {i["code"] for i in result["issues"]}


def test_partial_unit_evidence_does_not_expand_outside_cited_range():
    table, task, record = sample(unit="Pa")
    record["fields"]["unit"]["sources"][0]["quote"] = "Pa"
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["candidates"] and result["issues"]
    assert "partial_literal_selection" in {i["code"] for i in result["issues"]}


@pytest.mark.parametrize("header", ["Pressure/MPa; Temperature/K", "This is a description"])
def test_ambiguous_or_nonunit_passage_is_not_written_into_unit(header):
    table, task, record = sample(header, "Pa")
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["candidates"] and result["issues"]
    assert result["records"][0]["values"]["unit"] == "Pa"
    assert result["candidates"][0]["field_checks"]["unit"]["literal_check"] == "failed"


def test_mislabeled_anchor_or_cell_is_reported_without_rewriting():
    table, task, record = sample()
    record["fields"]["object"]["relation"] = "anchor"
    record["fields"]["unit"]["relation"] = "cell"
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert len(result["candidates"]) == 1
    mismatches = {i["field"] for i in result["issues"] if i["code"] == "relation_geometry_mismatch"}
    assert mismatches == {"object", "unit"}
    assert result["candidates"][0]["fields"]["unit"]["relation"] == "cell"
    assert result["candidates"][0]["field_checks"]["unit"]["literal_check"] == "passed"
    assert record["fields"]["object"]["relation"] == "anchor"
    record["fields"]["object"]["relation"] = "column"
    blocked = evaluate_route("A", {"records": [record]}, table, task)
    assert blocked["candidates"] and blocked["issues"]
    assert "relation_geometry_mismatch" in {i["code"] for i in blocked["issues"]}


@pytest.mark.parametrize("header,unit", [("Density kg/m3", "kg/m3"), ("Mass kg/m3", "kg/m3"),
                                          ("Viscosity/Pa*s", "Pa*s"), ("Viscosity/Pa·s", "Pa·s")])
def test_supported_compound_unit_is_preserved(header, unit):
    table, task, record = sample(header, unit)
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["candidates"][0]["values"]["unit"] == unit


@pytest.mark.parametrize("unit,claim", [("kg/m3", "m3"), ("m/s", "s"), ("Pa*s", "Pa"), ("Pa·s", "Pa")])
def test_compound_unit_is_read_whole_and_partial_evidence_is_rejected(unit, claim):
    table, task, record = sample("Quantity/" + unit, claim)
    selection = record["fields"]["unit"]["sources"][0]
    selection["quote"] = unit
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["candidates"][0]["values"]["unit"] == claim
    assert result["candidates"][0]["field_checks"]["unit"]["literal_check"] == "failed"
    selection["quote"] = claim
    partial = evaluate_route("A", {"records": [record]}, table, task)
    assert partial["candidates"] and partial["issues"]
    assert "partial_literal_selection" in {i["code"] for i in partial["issues"]}


@pytest.mark.parametrize("reading,claim", [("12±3", "12"), ("10–20", "10"),
                                           ("≤10", "10"), ("12 (n=3)", "3")])
def test_unit_fix_does_not_narrow_numeric_ranges_or_qualifiers(reading, claim):
    table, task, record = sample(reading=reading)
    record["fields"]["value"]["value"] = claim
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["candidates"][0]["values"]["value"] == claim
    assert "partial_literal_selection" in {i["code"] for i in result["issues"]}


def test_a_whole_header_claim_is_not_accepted_as_a_scalar_unit():
    table, task, record = sample("Pressure/MPa", "Pressure/MPa")
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["candidates"] and result["issues"]
    assert "unit_source_not_scalar" in {i["code"] for i in result["issues"]}


@pytest.mark.parametrize("unit,claim", [
    ("m²", "m"), (r"\text{m}^{2}", "m"),
    (r"\mathrm{kg}/\mathrm{m}^{3}", "kg"),
    (r"\mathrm{kg}/\mathrm{m}^{3}", "m"),
    ("m s-1", "m"), ("m s-1", "s-1"), ("mg L-1", "mg"),
    (r"m s^{-1}", "m"), (r"m s^{-1}", "s^{-1}"),
])
def test_unit_claim_cannot_drop_exponents_or_explicit_factors(unit, claim):
    table, task, record = sample("Quantity/" + unit, claim)
    result = evaluate_route("A", {"records": [record]}, table, task)
    candidate = result["candidates"][0]
    assert candidate["values"]["unit"] == claim
    assert candidate["field_checks"]["unit"]["literal_check"] == "failed"
    assert "partial_literal_selection" in {i["code"] for i in result["issues"]}


@pytest.mark.parametrize("unit", ["m²", r"\text{m}^{2}",
                                  r"\mathrm{kg}/\mathrm{m}^{3}", "m s-1", "mg L-1", r"m s^{-1}"])
def test_complete_unit_factors_are_supported_verbatim(unit):
    table, task, record = sample("Quantity/" + unit, unit)
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["candidates"][0]["field_checks"]["unit"]["literal_check"] == "passed"
    assert result["candidates"][0]["values"]["unit"] == unit


def test_unit_followed_by_ordinary_annotation_remains_supported():
    table, task, record = sample("Length/m (dry)", "m")
    result = evaluate_route("A", {"records": [record]}, table, task)
    assert result["candidates"][0]["field_checks"]["unit"]["literal_check"] == "passed"
