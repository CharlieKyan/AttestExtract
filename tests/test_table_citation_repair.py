"""A wrong table pointer can be repaired only at the intended owner/header intersection."""

import pytest

from niceextract import extract
from niceextract.dataset_citation import TableCitations, check_reading
from niceextract.table_routes import build_table_input
from test_source_statement_protocol import FixtureBackend


def _extract_table(source, owner, original_value, value, unit_header, *, axis="row", unit="N", property_name=None):
    entries = build_table_input(source)["entries"]
    def field(reading, relation, citation):
        found = next(e for e in entries if e["quote"].strip() == citation)
        return {"value": reading, "relation": relation, "sources": [{"ref": found["ref"], "quote": found["quote"]}]}
    fields = {"entity": field(owner, axis, owner), "value": field(value, "anchor", original_value)}
    if unit_header:
        fields["unit"] = field(unit, "column" if axis == "row" else "row", unit_header)
    record = {"record_id": "r1", "record_type": "reading", "scope": "individual", "scope_sources": [],
              "anchors": fields["value"]["sources"], "statement_basis": "reported", "reported_use": "measured",
              "source_property": property_name, "name_handling": "as_reported", "row_relation": None, "fields": fields}
    run = extract(source, prompt_description="Report the measured load of each sample.",
                  field_list={"reading": "Load of the sample."}, backend=FixtureBackend({"records": [record]}),
                  dataset_options={"row_subject": "samples"})
    obs, = run.dataset["observations"]
    return obs


@pytest.mark.parametrize("table,axis", [
    ("| Sample | Load (N) | Other (N) |\n|---|---|---|\n| A | 7 | 15 |\n| B | 11 | 17 |", "row"),
    ("| Metric | A | B |\n|---|---|---|\n| Load (N) | 7 | 11 |\n| Other (N) | 15 | 17 |", "column"),
])
def test_repair_uses_original_owner_and_metric_in_either_orientation(table, axis):
    obs = _extract_table(table, "A", "11", "7", "Load (N)", axis=axis)
    assert obs["citation_status"] == "located" and obs["citation_reason"] == "引文已找回"
    refs = obs["citation_evidence"]
    assert {r["quote"] for r in refs} >= {"A", "7", "Load (N)"}
    assert all(table[r["start"]:r["end"]] == r["quote"] for r in refs)
    assert any("value" in s["fields"] and s["quote"].strip() == "11" for s in obs["source_evidence"])


@pytest.mark.parametrize("original", ["7", "11"])
def test_matching_digits_from_other_sample_or_heading_remain_unverified(original):
    table = "| Sample | Load (N) | Other (N) |\n|---|---|---|\n| A | 5 | 7 |\n| B | 7 | 11 |"
    obs = _extract_table(table, "A", original, "7", "Load (N)")
    assert obs["citation_status"] == "unverified" and obs["value"] == "7"
    assert {(r["row"], r["column"]) for r in obs["citation_candidates"]} == {(1, 2), (2, 1)}


def test_missing_metric_preserves_candidates_until_reread():
    source = "| Sample | Load | Other |\n|---|---|---|\n| A | 7 | 7 |\n| B | 11 | 17 |"
    obs = _extract_table(source, "A", "11", "7", None)
    assert obs["citation_status"] == "unverified" and len(obs["citation_candidates"]) == 2
    obs = _extract_table(source, "A", "11", "7", None, property_name="Load")
    assert obs["citation_reason"] == "引文已找回" and obs["citation_evidence"][0]["column"] == 1


def test_an_identical_owner_heading_pair_in_another_table_is_outside_the_search():
    source = ("| Sample | Load |\n|---|---|\n| A | 11 |\n\nNext series\n\n"
              "| Sample | Load |\n|---|---|\n| A | 7 |")
    index = TableCitations(source, [{"table_id": "table-0001", "candidates": [{}]}])
    obs = {"source_record": "/table_results/0/candidates/0", "source_kind": "table", "entity": "A",
           "field": "load", "value": "7", "unit": None, "source_evidence": []}
    result = check_reading(source, obs, table_citations=index)
    assert result["citation_status"] == "unverified" and result["citation_candidates"] == []


def test_parameter_table_can_locate_value_and_adjacent_unit_independently_of_owner():
    table = "| Key | Value | Key | Value |\n|---|---|---|---|\n| Sites | A and B | Load (N) | 7 |"
    obs = _extract_table(table, "A and B", "7", "7", "Load (N)", property_name="Load")
    assert obs["citation_status"] == "located"
    assert any("Load (N)" in ref["quote"] for ref in obs["citation_evidence"])


def test_table_cannot_publish_a_reading_copied_only_from_adjacent_prose():
    source = "A separate gauge read 99 kg.\n\n| Object | Mass |\n|---|---|\n| Bottle P | 3 |"
    entries = build_table_input(source)["entries"]
    owner, anchor = [next(e for e in entries if e["quote"].strip() == s) for s in ("Bottle P", "3")]
    def binding(value, ref, quote, relation):
        return {"value": value, "sources": [{"ref": ref, "quote": quote}], "relation": relation}
    fields = {"entity": binding("Bottle P", owner["ref"], "Bottle P", "row"),
              "value": binding("99", "neighbor_before-0", "99", "context"),
              "unit": binding("kg", "neighbor_before-0", "kg", "context")}
    record = {"record_id": "r", "record_type": "mass", "scope": "individual", "scope_sources": [],
              "anchors": [{"ref": anchor["ref"], "quote": "3"}], "name_handling": "as_reported", "fields": fields}
    class Replies(FixtureBackend):
        def generate(self, request):
            if not request.chunk_id.startswith("table-"):
                from niceextract.models import BackendResponse
                return BackendResponse(text='{"records":[]}')
            return super().generate(request)
    run = extract(source, prompt_description="List bottle mass.", field_list={"mass": "Mass of a bottle."},
                  dataset_options={"row_subject": "bottles"}, backend=Replies({"records": [record]}))
    obs, = run.dataset["observations"]
    assert obs["value"] == "99" and not obs["row_ids"]
    assert any(issue["code"] == "value_only_in_context" for issue in obs["blockers"])
