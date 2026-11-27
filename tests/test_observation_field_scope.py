"""Field evidence, local parameter axes and collective label/value pairs."""

import pytest

from niceextract import extract
from niceextract.backends import ReplayBackend
from niceextract.dataset_field_fit import checked_field_fit
from niceextract.dataset_resolution_table import TablePaths, reconcile_table_unit
from niceextract.structure import build_structure
from niceextract.table_routes import build_table_input
from test_dataset_resolution import _run, _table_fixture


@pytest.mark.parametrize("is_reference,text", [
    (True, "Based on Panel A width, the support load was set to 80 kg."),
    (False, "Based on the calibration, Panel A load was set to 80 kg."),
    (False, "Based on Panel A width, its load was set to 80 kg."),
])
def test_a_reference_for_a_setting_need_not_own_the_setting(is_reference, text):
    reading = {"entity": "Panel A", "value": "80", "unit": "kg", "conditions": None}
    record = {"record_type": "r", "name_handling": "as_reported", "values": reading,
              "quotes": {k: text if v else "" for k, v in reading.items()}}
    run = extract(text, prompt_description="Record loads by panel.",
                  field_list={"id": "Panel name", "r": "Panel load"},
                  table_subject="individual panels", dataset_options={"subject_label_field": "id"},
                  backend=ReplayBackend([{"records": [record]}], output_protocol="flat-v1"))
    item, = run.analysis["observations"]
    assert item["binding_status"] == "as_extracted" and item["target_match"] == "unknown"
    obs, = run.dataset["observations"]
    assert obs["entity"] == "Panel A" and obs["value"] == "80"
    if is_reference:
        assert run.dataset["rows"] == []
        assert obs["fit"]["reason"] == obs["direct"]["reason"] == "owner_only_in_setting_reference"
        assert obs["purpose"] == "derived_setting"
    else:
        assert obs["delivery"] == "confirmed"
        assert obs["fit"]["basis"] == "extraction_field_selection"


def test_extraction_owner_flag_cannot_define_unidentified_table_axis():
    source, rows, records = _table_fixture()
    # Leave only the reading; no identity candidate is registered for this column.
    reading = dict(rows[0], binding_status="as_extracted", name_handling="as_reported")
    resolved = _run(source, [reading], result=records, row_subject="individual stations")["mass"]
    assert resolved["direct"]["label"] == "A17"
    assert resolved["target"]["reason"] == "owner_axis_membership_unresolved"


@pytest.mark.parametrize("handling", ["as_reported", "related"])
@pytest.mark.parametrize("planned", [False, True])
def test_measurement_field_requires_the_value_to_have_the_right_purpose(handling, planned):
    text = ("Panel A cut from lot B has a design target of 80 MPa. No measurements have been made."
            if planned else "Panel A cut from lot B had a measured strength of 80 MPa.")
    values = {"entity": "Panel A", "value": "80", "unit": "MPa", "conditions": None}
    record = {"record_type": "s", "name_handling": handling, "values": values,
              "quotes": {k: text if v else "" for k, v in values.items()}}
    if handling == "related":
        record["row_relation"] = {"target": "lot B", "predicate": "sample_of", "field_applicability": "included",
                                  "evidence": [{"quote": text, "ref": None}]}
    run = extract(text, prompt_description="Record measurements by subject.",
                  field_list={"id": "Subject name", "s": "Measured strength; exclude design targets"},
                  table_subject="individual lots" if handling == "related" else "individual panels",
                  dataset_options={"subject_label_field": "id"}, row_relation_evidence=handling == "related",
                  backend=ReplayBackend([{"records": [record]}], output_protocol="flat-v1"))
    obs, = run.dataset["observations"]
    if planned:
        assert obs["fit"]["reason"] == "planned_value_in_measurement_field"
        assert obs["fit"]["status"] == "contradicted" and run.dataset["rows"] == []
    else:
        assert obs["fit"]["status"] != "contradicted"
        if handling == "as_reported":
            assert run.dataset["rows"] == [{"id": "Panel A", "s": "80"}]


@pytest.mark.parametrize("definition,claim,expected", [
    ("实测风速", "站点甲的设计目标为80 m/s，尚未测量。", "contradicted"),
    ("设计或实测风速", "站点甲的设计目标为80 m/s，尚未测量。", "supported"),
    ("Observed load", "The specified target was 80 kg. The observed load was 80 kg.", "supported"),
])
def test_explicit_target_is_compared_with_user_field_and_same_value_measurement(definition, claim, expected):
    fit = checked_field_fit(definition, {"value": "80"}, {"status": "supported", "reason": "source_linked_field_proposal"},
                            [{"quote": claim, "start": 0, "end": len(claim)}])
    assert fit["status"] == expected


@pytest.mark.parametrize("transpose", [False, True])
def test_relation_cell_needs_its_own_header_within_one_simple_panel(transpose):
    def path(grid, *, include_role=True, two_targets=False):
        if transpose:
            grid = list(zip(*grid))
        source = "<table>" + "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>"
                                     for row in grid) + "</table>"
        doc = build_structure(source)
        paths = TablePaths(source, doc)
        cells = {e["quote"]: {**e, "selected_start": e["start"], "selected_end": e["end"],
                              "selected_quote": e["quote"]} for e in build_table_input(source)["entries"]}
        sources = [cells["R9"], cells["B"]]
        if include_role:
            sources.append(cells["From"])
        receipt = {"status": "located", "target": "B", "evidence": sources}
        obs = {"entity": "R9", "name_handling": "related", "row_relations": [receipt] * (2 if two_targets else 1)}
        rec = {"scope": "individual", "resolved_fields": {"entity": [cells["R9"]], "value": [cells["12"]]}}
        return paths.path(obs, rec, doc.tables[0].table_id)

    simple = [["Coupon", "From", "Strain"], ["R9", "B", "12"]]
    assert {e["quote"] for e in path(simple)["row_relation_cells"]} == {"B", "From"}
    assert path(simple, include_role=False).get("gap")
    assert path(simple, two_targets=True).get("gap")
    panels = [["Station", "Source", "Rate", *simple[0]], ["S1", "C", "14", *simple[1]]]
    assert "row_relation_cells" not in path(panels)


@pytest.mark.parametrize("transpose", [False, True])
@pytest.mark.parametrize("target", ["panel B", "panel C"])
def test_experimental_table_projection_only_uses_the_sources_of_this_observation(target, transpose):
    grid = [["Coupon", "From", "Strain"], ["R9", "panel B", "12"], ["D7", "panel C", "14"]]
    if transpose:
        grid = list(zip(*grid))
    grid.insert(1, ["---"] * len(grid[0]))
    source = "\n".join("| " + " | ".join(row) + " |" for row in grid)
    cells = {e["quote"].strip(): e["ref"] for e in build_table_input(source)["entries"]}

    def select(label):
        return {"quote": label, "ref": cells[label]}

    record = {"record_id": "1", "record_type": "strain", "scope": "individual", "scope_sources": [],
              "anchors": [select("12")], "name_handling": "related",
              "row_relation": {"target": target, "predicate": "sample_of", "field_applicability": "included",
                               "evidence": [select("R9"), select("From"), select(target)]},
              "fields": {"entity": {"value": "R9", "relation": "column" if transpose else "row", "sources": [select("R9")]},
                         "value": {"value": "12", "relation": "anchor", "sources": [select("12")]},
                         "conditions": {"value": None, "sources": []}, "unit": {"value": None, "sources": []}}}
    run = extract(source, prompt_description="Record strain by panel.",
                  field_list={"id": "Panel name", "strain": "Strain of the panel or its coupons."},
                  table_subject="named panels", dataset_options={"subject_label_field": "id"}, row_relation_evidence=True,
                  backend=ReplayBackend([{"records": [record]}, {"records": []}], output_protocol="flat-v1"))
    obs, = run.dataset["observations"]
    assert obs["row_relations"][0]["status"] == "located" and obs["value"] == "12"
    if target == "panel B":
        assert obs["delivery"] == "confirmed" and obs["direct"]["label"] == "R9"
        assert run.dataset["rows"] == [{"id": "panel B", "strain": "12"}]
    else:
        assert obs["delivery"] == "detail" and run.dataset["rows"] == []


@pytest.mark.parametrize("transpose", [False, True])
def test_adjacent_pairs_use_a_verified_collective_once(transpose):
    grid = [["Key", "Value", "Key", "Value"], ["Group", "A+B", "Width/mm", "65.4"]]
    if transpose:
        grid = list(zip(*grid))
    source = "<table>" + "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>"
                                 for row in grid) + "</table>"
    structure = build_structure(source)
    paths = TablePaths(source, structure)
    cells = {e["quote"]: {**e, "selected_start": e["start"], "selected_end": e["end"],
                          "selected_quote": e["quote"]} for e in build_table_input(source)["entries"]}
    owner, prop, value = [cells[k] for k in ("A+B", "Width/mm", "65.4")]
    record = {"scope": "group", "resolved_scope_sources": [owner], "resolved_fields": {
        "entity": [owner], "conditions": [prop], "unit": [prop], "value": [value]}}
    blocked = {"code": "relation_geometry_mismatch", "field": "unit", "source": {"ref": prop["ref"]}}
    unrelated = {"code": "unlocated_value", "field": "value"}
    obs = {"entity": "A+B", "unit": "mm", "property_name": "Width", "binding_status": "supported",
           "target_match": "supported", "blockers": [blocked, unrelated]}
    table = structure.tables[0].table_id
    evidence = [owner, {"start": prop["start"], "end": value["end"]}]
    path = paths.path(obs, record, table, {owner["ref"]}, evidence)
    assert path["scope"] == "group" and path["owner"]["quote"] == "A+B"
    view = reconcile_table_unit(obs, path)
    assert view["blockers"] == [unrelated] and view["archived_blockers"] == [blocked, unrelated]
    assert paths.path(obs, record, table, {owner["ref"]}, evidence[:1])["gap"]
    assert paths.path(obs, record, table, (), evidence)["gap"]
