"""Shared process settings and panel boundaries, independent of user field names."""
import pytest

from niceextract.dataset_resolution import resolve_observations
from niceextract.dataset_resolution_table import TablePaths
from niceextract.structure import build_structure
from niceextract.table_routes import build_table_input
from test_dataset_resolution import _object, _observation, _span


def shared_case(*, caption="Operating settings", citation=True, field="temperature", definition="temperature",
                exception="", after_citation="", member_override=False, parent_conflict=False, transpose=False):
    assignment = "A1 and A2 use Process P"
    source = assignment + ". " + exception + ("Settings are in Table 1." if citation else "See Appendix A.") + after_citation
    rows = [["Process", "Temperature"], ["Process P", "80"], ["Process Q", "30"]]
    if transpose:
        rows = list(zip(*rows))
    source += "\n\nTable 1. " + caption + "\n<table>" + "".join(
        "<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in rows) + "</table>"
    cells = {c["quote"]: {**c, "selected_start": c["start"], "selected_end": c["end"],
                          "selected_quote": c["quote"]}
             for c in build_table_input(source)["entries"] if c["kind"] == "cell"}
    records = {"table_results": [{"table_id": "table-0001", "candidates": [
        {"scope": "individual", "resolved_fields": {"entity": [cells["Process P"]], "value": [cells["80"]]}}]}]}
    objects = [_object("run", "Run East", "description")]
    proof = {"evidence_id": "description", **_span(source, assignment)}
    relations, observations = [], []
    for member in ("A1", "A2"):
        objects.append(_object(member, "batch " + member, "description"))
        observations.append(_observation(source, member, assignment, member, member, field="specimen",
                                         binding_status="supported", subject_id=member, binding_evidence_ids=["description"]))
        parent = "run"
        if member == "A2" and parent_conflict:
            parent = "other"
            objects.append(_object(parent, "Run West", "description"))
        relations.append({"predicate": "member_of", "source_id": member, "target_id": parent,
                          "relation_id": "edge-" + member, "evidence_ids": ["description"]})
    observations.append(_observation(source, "assignment", assignment, "A1 and A2", "Process P", field="procedure"))
    if member_override:
        source += "\nA1 temperature was set to 75."
        observations.append(_observation(source, "own-setting", "A1 temperature was set to 75.", "A1", "75",
                                         field=field, subject_id="A1"))
    reading = _observation(source, "setting", "80", "Process P", "80", field=field,
                           source_kind="table", source_record="/table_results/0/candidates/0",
                           name_handling="as_reported", binding_status="as_extracted")
    observations.append(reading)
    snapshot = {"objects": objects, "evidence": [proof], "relations": relations, "observations": observations,
                "table_subject": "named batches", "task": {"fields": {"specimen": "batch name",
                    "procedure": "procedure used by each batch", field: definition}}}
    return resolve_observations(source, records, snapshot, identity_fields=["specimen"], row_subject="named batches")["setting"]


@pytest.mark.parametrize("transpose", [False, True])
def test_table_setting_filed_once_for_the_named_users_of_the_same_process(transpose):
    obs = shared_case(transpose=transpose, field="custom_signal")
    assert obs["target"]["label"] == "A1 and A2"
    assert {m["id"] for m in obs["target"]["members"]} == {"A1", "A2"}
    assert obs["direct"]["label"] == "Process P" and obs["measurement_use"] == "process_setting"
    assert {"Table 1", "A1 and A2 use Process P", "80"} <= {s["quote"] for s in obs["target"]["evidence"]}


@pytest.mark.parametrize("options", [
    {"citation": False}, {"caption": "Sensitivity input ranges"}, {"definition": "in situ temperature only"},
    {"caption": "Measured parameters in simulation"}, {"definition": "measured only; no simulation settings"},
    {"definition": "实测专用，不含设定值"},
    {"exception": "Except A1. "}, {"after_citation": " A1 instead uses 75 degrees."},
    {"after_citation": "\nThe setting excludes A1 during this run."},
    {"member_override": True}, {"parent_conflict": True},
])
def test_missing_or_conflicting_shared_setting_proof_stays_out_of_the_group(options):
    assert not shared_case(**options)["target"].get("members")


def test_qualified_simulation_caption_keeps_model_use_and_provenance():
    obs = shared_case(caption="Input parameters in simulation")
    assert obs["measurement_use"] == "model_input"
    assert obs["target"]["evidence"][2]["quote"] == "Table 1. Input parameters in simulation"


def test_unrelated_following_paragraph_does_not_change_group():
    obs = shared_case(after_citation="\n\nIn a separate experiment, A1 instead uses 75 degrees.")
    assert obs["target"]["label"] == "A1 and A2"


def test_repeated_axis_title_starts_new_panel_after_intervening_data():
    source = ("<table><tr><td>Batch</td><td colspan='2'>Inspection</td></tr>"
              "<tr><td></td><td>Observed</td><td>Count</td></tr>"
              "<tr><td>B1</td><td>12</td><td>6</td></tr>"
              "<tr><td>Batch</td><td>Heat</td><td>Load</td></tr>"
              "<tr><td>B2</td><td>80</td><td>5</td></tr></table>")
    cells = {c["quote"]: {**c, "selected_start": c["start"], "selected_end": c["end"],
                          "selected_quote": c["quote"]}
             for c in build_table_input(source)["entries"] if c["kind"] == "cell"}
    obs = {"entity": "B2", "record_type": "heat"}
    raw = {"scope": "individual", "resolved_fields": {"entity": [cells["B2"]], "value": [cells["80"]]}}
    path = TablePaths(source, build_structure(source)).path(obs, raw, "table-0001")
    assert [h["quote"] for h in path["headers"]] == ["Heat"]
    assert path["owner_role"]["start"] == source.rindex("Batch")
