"""Source ownership, related-row scope and export under field names supplied by the task."""

import csv

import pytest

from niceextract import assemble_dataset, extract, query_dataset, save_dataset
from niceextract.analysis import _input_observations
from niceextract.backends import ReplayBackend
from niceextract.jev_packets import make_packets
from niceextract.jev_projection import final_dataset
from test_dataset_admission import assemble


def test_extraction_assignment_survives_separate_owner_and_reading_citations():
    first, second = "Station Q5 was installed in June.", "It recorded 30 cm at noon."
    values = {"entity": "Station Q5", "value": "30", "unit": "cm", "conditions": "at noon"}
    record = {"record_type": "h", "name_handling": "as_reported", "values": values,
              "quotes": {"entity": first, "value": second, "unit": "cm", "conditions": "at noon"}}
    run = extract(first + " " + second, prompt_description="Read the reported height.",
                  field_list={"n": "Station name", "h": "Recorded height"}, table_subject="Named stations",
                  dataset_options={"subject_label_field": "n"},
                  backend=ReplayBackend([{"records": [record]}], output_protocol="flat-v1"))
    assert run.analysis["objects"] == [] and run.analysis["observations"][0]["binding_status"] == "as_extracted"
    assert run.dataset["rows"] == [{"n": "Station Q5", "h": "30"}]
    obs, = query_dataset(run.dataset, "Station Q5")
    assert obs["direct"]["evidence"][0]["quote"] == first and obs["fit"]["evidence"][0]["quote"] == second


@pytest.mark.parametrize("entity,join,reading", [
    ("Station Q9", " ", "It recorded 30 cm."),
    ("Station Q5", "\n\n", "A station in a different region recorded 30 cm."),
    ("Station Q5", " ", "Another instrument recorded 30 cm."),
])
def test_wrong_name_or_change_of_referent_keeps_value_in_detail(entity, join, reading):
    first = "Station Q5 was installed in June."
    values = {"entity": entity, "value": "30", "unit": "cm", "conditions": None}
    record = {"record_type": "h", "name_handling": "as_reported", "values": values,
              "quotes": {"entity": first, "value": reading, "unit": "cm", "conditions": ""}}
    run = extract(first + join + reading, prompt_description="Read the reported height.",
                  field_list={"n": "Station name", "h": "Recorded height"}, table_subject="Named stations",
                  dataset_options={"subject_label_field": "n"},
                  backend=ReplayBackend([{"records": [record]}], output_protocol="flat-v1"))
    obs, = run.dataset["observations"]
    assert not run.dataset["rows"] and obs["value"] == "30" and obs["entity"] == entity
    assert obs["delivery"] == "detail"


@pytest.mark.parametrize("reading,expected", [("M7 station reported 30 cm.", True),
                                             ("M7 station and Tower B jointly reported 30 cm.", False),
                                             ("M7 station insert reported 30 cm.", False)])
def test_local_assignment_distinguishes_a_task_type_suffix_from_another_owner(reading, expected):
    from niceextract.dataset_extraction_owner import ExtractionOwners

    snapshot = {"table_subject": "Named stations", "evidence": [], "objects": [], "relations": [],
                "observations": [{"entity": label, "source_evidence": []}
                                 for label in ("M7", "M7 station", "Tower B", "M7 station insert")]}
    owners = ExtractionOwners(reading, snapshot, True)
    entry = {"fields": ["entity", "value"], "quote": reading, "span": {"start": 0, "end": len(reading)}}
    assignment = {"entity": "M7", "value": "30", "unit": "cm", "source_evidence": [entry]}
    assert bool(owners._extracted_proof(assignment, "M7")) == expected


@pytest.mark.parametrize("same_occurrence,expected", [(False, True), (True, False)])
def test_only_one_source_bound_registry_identity_may_explain_a_longer_name(same_occurrence, expected):
    from niceextract.dataset_extraction_owner import ExtractionOwners

    clause = "Q5 Device-02 reported 30 cm."
    text = clause + "\n" + clause
    citations, objects, observations = [], [], []
    for key, offset in [("north", 0), ("south", 0 if same_occurrence else len(clause) + 1)]:
        citations.append({"evidence_id": key, "start": offset, "end": offset + len(clause), "quote": clause})
        objects.append({"object_id": key, "name": "Q5 Device-02", "scope": key,
                        "mention_evidence_ids": [key], "is_table_subject": True})
        observations.append({"entity": "Q5", "subject_id": key, "binding_status": "supported", "record_type": "name",
                             "evidence_ids": [key], "source_evidence": []})
    snapshot = {"table_subject": "Named instruments", "evidence": citations, "objects": objects,
                "relations": [], "observations": observations}
    owners = ExtractionOwners(text, snapshot, True, "name")
    assert (not owners._competes("Q5", clause, 0)) == expected


@pytest.mark.parametrize("statement,values,confirmed", [
    ("Line A ran smoothly, Line B vibrated sharply.", ["ran smoothly", "vibrated sharply"], 2),
    ("Line A and Line B vibrated sharply.", ["vibrated sharply", "vibrated sharply"], 0),
    ("Line A, Line B vibrated sharply.", ["vibrated sharply", "vibrated sharply"], 0),
    ("Line A never ran smoothly, Line B vibrated sharply.", ["ran smoothly", "vibrated sharply"], 0),
    ("Line A yielded 6, Line B yielded 8.", ["6", "8"], 0),
])
def test_self_contained_parallel_descriptions_are_bound_to_their_own_source_clauses(statement, values, confirmed):
    records = []
    for name, value in zip(["Line A", "Line B"], values, strict=True):
        expression = name + " " + value
        record = {"record_type": "reading", "name_handling": "as_reported",
                  "values": {"entity": name, "value": value, "unit": None, "conditions": None},
                  "quotes": {"entity": name, "value": expression if expression in statement else value,
                             "unit": "", "conditions": ""}}
        records.append(record)
    run = extract(statement, prompt_description="Record operation by line.", field_list={"id": "Line name",
                  "reading": "Reported condition"}, table_subject="named lines", dataset_options={"subject_label_field": "id"},
                  backend=ReplayBackend([{"records": records}], output_protocol="flat-v1"))
    assert len(run.dataset["rows"]) == confirmed
    if confirmed:
        assert [(r["id"], r["reading"]) for r in run.dataset["rows"]] == list(zip(["Line A", "Line B"], values))
    assert len(run.dataset["observations"]) == 2


def test_local_owner_without_a_resolved_registry_keeps_reading_and_literal_qualifiers(tmp_path):
    text = "Q5 yielded a final length of 30 cm at noon."
    data = assemble(text, text, [], "Q5", condition="at noon")
    obs, = data["observations"]
    assert obs["direct"]["label"] == "Q5" and obs["direct"]["evidence"][0]["quote"] == text
    assert obs["delivery"] == "provisional" and not query_dataset(data)
    save_dataset(data, tmp_path / "dataset")
    with (tmp_path / "dataset/main.csv").open(encoding="utf-8-sig", newline="") as stream:
        row, = csv.DictReader(stream)
    assert (row["name"], row["直接对象"], row["reading"], row["观测口径"]) == ("Q5", "Q5", "30", "at noon")
    assert "身份待定" in row["记录状态"]


def _related_run(definition, owner="Unit A lining specimen"):
    source = f"Unit A is under study. {owner} returned a width of 12–18 mm."
    claim = source.split(". ", 1)[1]
    values = {"entity": owner, "value": "12–18", "unit": "mm", "conditions": "range"}
    record = {"record_type": "w", "values": values, "quotes": dict.fromkeys(values, claim)}
    fields = {"n": "Unit name", "w": definition}
    result = extract(source, prompt_description="Record the reported properties.", field_list=fields,
                     backend=ReplayBackend([{"records": [record]}], output_protocol="flat-v1"))
    evidence = {}
    readings, quarantined = _input_observations(source, result, evidence)
    owner_proof = next(iter(evidence))
    evidence["parent"] = {"start": 0, "end": 6, "quote": "Unit A", "evidence_id": "parent"}
    objects = [{"object_id": key, "name": name, "kind": "instance", "scope": "current experiment",
                "is_table_subject": target, "mention_evidence_ids": [proof], "semantic_status": "model_proposed"}
               for key, name, proof, target in [("child", owner + " (specimens)", owner_proof, False),
                                                 ("unit", "Unit A (workshop)", "parent", True)]]
    readings[0].update(binding_status="supported", subject_id="child", target_match="supported", property_name="width",
                       statistic="range", binding_evidence_ids=[owner_proof])
    edge = {"source_id": "child", "target_id": "unit", "predicate": "part_of", "relation_id": "r",
            "evidence_ids": [owner_proof], "semantic_status": "model_proposed"}
    snapshot = {"analysis_version": "0.1", "status": "complete", "source_format_status": "complete",
                "semantic_status": "model_proposed", "source": result.source.model_dump(mode="json"),
                "task": {"name": result.task.name, "description": result.task.description, "fields": fields},
                "table_subject": "named units", "objects": objects, "relations": [edge], "observations": readings,
                "quarantined": quarantined, "evidence": list(evidence.values())}
    return source, result.model_dump(mode="json"), snapshot


@pytest.mark.parametrize("definition,admit", [
    ("Width of the unit or associated specimens.", True),
    ("Width of the unit itself.", False),
    ("Width of the unit. Exclude related specimens.", False),
    ("Width of the unit or associated control cabinets.", False),
])
def test_direct_and_target_subject_are_both_preserved_with_field_specific_scope(tmp_path, definition, admit):
    data = assemble_dataset(*_related_run(definition), subject_label_field="n")
    obs, = data["observations"]
    assert obs["direct"]["label"] == "Unit A lining specimen"
    assert obs["target"]["label"] == "Unit A" and obs["direct"]["id"] != obs["target"]["id"]
    assert query_dataset(data, "Unit A") == ([obs] if admit else [])
    assert query_dataset(data, "Unit A", association_only=True) == [obs]
    if admit:
        assert query_dataset(data, "Unit A lining specimen") == [obs]
        paths = save_dataset(data, tmp_path / "dataset")
        with (paths / "main.csv").open(encoding="utf-8-sig", newline="") as f:
            row, = csv.DictReader(f)
        assert [row[c] for c in ("n", "w", "直接对象", "观测口径")] == ["Unit A", "12–18", "Unit A lining specimen", "range"]
        assert "关联" in row["记录状态"]
    else:
        assert data["rows"] == []


def test_registered_type_with_no_original_type_label_keeps_its_qualification():
    data = assemble_dataset(*_related_run("Width of the unit or associated specimens.", owner="Unit A lining"),
                            subject_label_field="n")
    row, = data["rows"]
    obs, = data["observations"]
    assert (row["n"], row["w"], obs["direct"]["label"]) == ("Unit A", "12–18", "Unit A lining")
    assert obs["delivery"] == "provisional" and not query_dataset(data)
    assert "类别待定" in data["lineage"]["rows"][0]["status"]


def test_similar_names_and_unrelated_citation_cannot_ground_an_archived_directed_relation():
    source, result, snapshot = _related_run("Width of the unit or associated specimens.")
    snapshot["relations"][0]["evidence_ids"] = ["parent"]
    data = assemble_dataset(source, result, snapshot, subject_label_field="n")
    assert not data["rows"] and not query_dataset(data, "Unit A", association_only=True)


def test_jev_can_identify_the_same_local_name_without_changing_its_owner():
    text = "Q5 yielded a final length of 30 cm at noon."
    source = "R4 was inspected. " + text
    data = assemble(source, text, [("Q5", "Q5"), ("R4", "R4")], "Q5",
                    condition="at noon", match="unknown")
    obs, = data["observations"]
    direct = obs["direct"]["id"]
    assert direct == "mention-" + obs["observation_id"]
    packet, = make_packets(data, source)
    choice, = packet["options"].values()
    assert choice["label"] == "Q5" and choice["id"] == "o0" and choice["local_subject_id"] == direct
    decided = final_dataset(data, [packet], {obs["observation_id"]: {"choice": "bind_1", "confidence": 0.9}})
    result, = query_dataset(decided, "Q5")
    assert result["direct"]["original_subject_id"] == direct and result["direct"]["id"] == "o0"
