"""A repeated physical quantity does not independently prove two different field meanings."""

import pytest

from niceextract.dataset_field_fit import check_competing_cell_fields
from niceextract.dataset_extraction_owner import ExtractionOwners


@pytest.mark.parametrize("definition,heading,winner", [
    ("Test width w and height h.", "w/mm", "width"),
    ("Test width w and height h.", "Mode code", "kind"),
    ("The table lists two values.", "q", None),
])
def test_same_cell_requires_distinctive_field_evidence_and_allows_numeric_codes(definition, heading, winner):
    source = f"{definition} <table><tr><td>{heading}</td></tr><tr><td>10</td></tr></table>"
    def span(ref, text):
        i = source.index(">" + text + "<") + 1
        return {"start": i, "end": i + len(text), "quote": text, "ref": ref}
    cell, title = span("t-r1-c0", "10"), span("t-r0-c0", heading)
    obs = [{"field": f, "table_context": {"value": cell, "headers": [title]}, "value": "10",
            "fit": {"status": "supported"}, "fit_blockers": [], "eligible": True} for f in ("kind", "width")]
    check_competing_cell_fields(source, obs, {"width": "width", "kind": "Mode code"}, [])
    assert {o["field"] for o in obs if o["eligible"]} == ({winner} if winner else set())
    assert all(o["fit"]["reason"] == "competing_fields_for_source_cell" for o in obs if not o["eligible"])


@pytest.mark.parametrize("prefix,expected", [
    ("The types of carrier (Copper and Glass) were modelled.\n", True),
    ("The types of carrier (Copper and Glass) appear in Table 4.\n\n# Results\n\nTable 4 readings\n", True),
    ("The types of carrier (Copper and Glass) appear in Table 8.\n\n# Results\n\nTable 4 readings\n", False),
])
def test_one_locally_defined_type_list_cannot_relabel_distant_named_instances(prefix, expected):
    source = prefix + "<table>Copper | Glass</table>"
    fields = []
    for row, name in enumerate(("Copper", "Glass"), 1):
        start = source.rindex(name)
        owner = {"start": start, "end": start + len(name), "ref": f"table-0001-r{row:04}-c0000", "quote": name}
        fields.append({"entity": name, "source_evidence": [{"span": owner, "ref": owner["ref"],
                       "quote": name, "fields": ["entity"]}], "table_context": {"owner": owner}})
    snapshot = {"table_subject": "individual named units", "observations": fields, "evidence": [],
                "objects": [], "relations": []}
    owners = ExtractionOwners(source, snapshot, True)
    assert (owners._row_conflict(fields[0], "Copper") == "source_type_on_named_row_axis") is expected
    snapshot["table_subject"] = "named types of carrier"
    assert ExtractionOwners(source, snapshot, True)._row_conflict(fields[0], "Copper") is None


def test_relative_component_name_depends_on_the_requested_row_kind():
    snapshot = {"table_subject": "named stations", "observations": [], "evidence": [], "objects": [], "relations": []}
    obs = {"source_evidence": []}
    assert ExtractionOwners("", snapshot, True)._row_conflict(obs, "rightmost grid cell") == "positional_object_row_unresolved"
    snapshot["table_subject"] = "individual named grid cells"
    assert ExtractionOwners("", snapshot, True)._row_conflict(obs, "rightmost grid cell") is None
