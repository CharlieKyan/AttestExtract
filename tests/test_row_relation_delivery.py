"""Offline tests through extraction, dataset assembly, both lookup paths and CSV."""

import csv

import pytest

from niceextract import assemble_dataset, extract, query_dataset, save_dataset
from niceextract.backends import ReplayBackend
from niceextract.dataset_snapshot import reconcile_presentation_checks
from niceextract.task_input import compile_task


def assemble(source, entity, statement, relation, *, mode="related", subject="Named panels",
             metric="Strength of the panel or its coupons", field="strength"):
    task = compile_task(prompt_description="Extract each reported measurement.",
                        field_list={"name": subject, field: metric}).model_copy(update={"row_subject": subject})
    record = {"record_type": field, "name_handling": mode,
              "values": {"entity": entity, "value": "12", "unit": None, "conditions": None},
              "quotes": {"entity": statement, "value": statement, "unit": "", "conditions": ""}, "row_relation": relation}
    result = extract(source, task, row_relation_evidence=True, table_mode="text",
                     backend=ReplayBackend([{"records": [record]}], output_protocol="flat-v1"))
    return assemble_dataset(source, result.model_dump(mode="json"), subject_label_field="name")


def link(target, statement, applicability="included", predicate="sample_of"):
    return {"target": target, "predicate": predicate, "field_applicability": applicability,
            "evidence": [{"quote": statement, "ref": None}]}


@pytest.mark.parametrize("entity,target,subject,field,predicate", [
    ("Coupon S3", "Panel B", "Named panels", "strength", "sample_of"),
    ("Liner L4", "Furnace F2", "Named furnaces", "temperature", "part_of"),
    ("Point P", "Station North", "Named stations", "salinity", "located_in"),
])
def test_same_observation_in_different_task_grains(entity, target, subject, field, predicate):
    source = f"{entity} from {target} had {field} 12."
    data = assemble(source, entity, source, link(target, source, predicate=predicate), subject=subject,
                    metric=f"Reported {field} of the subject or its associated object.", field=field)
    obs, = data["observations"]
    assert data["rows"] == [{"name": target, field: "12"}]
    assert query_dataset(data, entity) == query_dataset(data, target) == [obs]
    assert query_dataset(data, target, association_only=True) == [obs]
    assert obs["entity"] == entity and obs["value_located"] and obs["fit"]["basis"] == "model_proposed"
    direct_task = assemble(source, entity, source, None, mode="as_reported", subject="Named measurement objects",
                           metric=f"Reported {field}.", field=field)
    assert direct_task["rows"] == [{"name": entity, field: "12"}]
    assert direct_task["observations"][0]["entity"] == entity


def test_uncertain_association_export_and_default_count(tmp_path):
    source = "Coupon S3 from Panel B had strength 12."
    data = assemble(source, "Coupon S3", source, link("Panel B", source, "uncertain"))
    obs, = data["observations"]
    assert obs["delivery"] == "provisional" and not query_dataset(data)
    assert query_dataset(data, "Panel B", association_only=True) == [obs]
    folder = save_dataset(data, tmp_path / "out")
    with (folder / "main.csv").open(encoding="utf-8-sig", newline="") as handle:
        row, = csv.DictReader(handle)
    assert (row["name"], row["直接对象"], row["strength"]) == ("Panel B", "Coupon S3", "12")
    assert "待定" in row["记录状态"]
    with (folder / "observations.csv").open(encoding="utf-8-sig", newline="") as handle:
        detail, = csv.DictReader(handle)
    assert (detail["direct_subject_status"], detail["observation_id"]) == ("source_linked", obs["observation_id"])


def test_source_alternatives_create_one_provisional_row():
    source = "Coupon S3 from either Panel A or Panel B had strength 12."
    relations = {"alternatives": [link(label, source) for label in ("Panel A", "Panel B")]}
    data = assemble(source, "Coupon S3", source, relations, mode="uncertain")
    obs, = data["observations"]
    assert obs["delivery"] == "provisional" and len(obs["row_ids"]) == 1 and not query_dataset(data)
    assert data["rows"] == [{"name": "Panel A 或 Panel B", "strength": "12"}]
    assert query_dataset(data, "Panel A", exploratory=True) == query_dataset(data, "Panel B", exploratory=True) == [obs]


@pytest.mark.parametrize("why", ["excluded", "other_batch", "different_identifier", "no_target"])
def test_false_or_missing_parent_keeps_original_observation(why):
    earlier = "In the previous batch Coupon S3 came from Panel B."
    statement = "Coupon S3 from Panel B1 had strength 12."
    row_relation = {"excluded": link("Panel B1", statement, "excluded"),
                    "other_batch": link("Panel B", earlier), "different_identifier": link("Panel B", statement),
                    "no_target": None}[why]
    data = assemble(earlier + "\n\n" + statement, "Coupon S3", statement, row_relation)
    obs, = data["observations"]
    assert not data["rows"] and obs["delivery"] == "detail"
    assert (obs["entity"], obs["value"], obs["value_located"]) == ("Coupon S3", "12", True)


def test_repair_only_original_markdown_escape_and_uniquely_bound_field():
    source = r"Coil A ranged 10\~12"
    span = {"start": 0, "end": len(source)}
    entry = {"fields": ["entity"], "location_status": "normalized", "span": span, "quote": "Coil A ranged 10~12",
             "source_matches": [{"span": span, "source_quote": source}]}
    warning = {"field": "entity", "code": "evidence_normalized"}
    semantic = {"field": "entity", "code": "entity_conflict"}
    item = {"source_evidence": [entry], "entity": "Coil A", "blockers": [warning, semantic]}
    new = reconcile_presentation_checks(source, item)
    assert new["blockers"] == [semantic] and new["archived_blockers"] == item["blockers"] == [warning, semantic]
    assert reconcile_presentation_checks(source, {**item, "source_evidence": [entry, entry]})["blockers"] == item["blockers"]
