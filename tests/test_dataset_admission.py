"""Verify the candidate-inclusive CSV against the untouched source and strict query."""

import csv

import pytest

from niceextract import assemble_dataset, extract, query_dataset, save_dataset
from niceextract.analysis import _input_observations
from niceextract.backends import ReplayBackend
from niceextract.dataset import _has_value_source


def assemble(source, citation, names, entity, *, definition="length", property_name="length",
             match="supported", condition=None, subject_field="name", batches=()):
    values = {"entity": entity, "value": "30", "unit": "cm", "conditions": condition}
    record = {"record_type": "reading", "values": values,
              "quotes": {key: citation if value is not None else "" for key, value in values.items()}}
    fields = {subject_field: "Name of the specimen", "reading": definition}
    records = [record]
    if batches:
        fields["batch"] = "batch"
    for owner, batch in batches:
        claim = {"entity": owner, "value": batch, "unit": None, "conditions": None}
        sentence = f"{owner}: batch = {batch}."
        records.append({"record_type": "batch", "values": claim,
                        "quotes": {k: sentence if v is not None else "" for k, v in claim.items()}})
    result = extract(source, prompt_description="Report specimen observations.", field_list=fields,
                     backend=ReplayBackend([{"records": records}], output_protocol="flat-v1"))
    evidence = {}
    items, rejected = _input_observations(source, result, evidence)
    objects = []
    for index, (name, scope) in enumerate(names):
        start = source.index(scope if scope in source else name)
        quote = scope if scope in source else name
        key = f"mention-{index}"
        evidence[key] = {"evidence_id": key, "start": start, "end": start + len(quote), "quote": quote}
        objects.append({"object_id": f"o{index}", "name": name, "scope": scope, "kind": "instance",
                        "is_table_subject": True, "mention_evidence_ids": [key], "semantic_status": "model_proposed"})
    supported = entity and len(objects) == 1 and entity == objects[0]["name"]
    items[0].update(binding_status="supported" if supported else "unknown", subject_id="o0" if supported else None,
                    binding_evidence_ids=items[0]["evidence_ids"] if supported else [],
                    target_match=match, property_name=property_name)
    for item in items[1:]:
        obj = next(o for o in objects if o["name"] == item["entity"])
        item.update(binding_status="supported", subject_id=obj["object_id"], target_match="supported",
                    binding_evidence_ids=item["evidence_ids"], property_name="batch")
    snapshot = {"analysis_version": "0.1", "status": "complete", "semantic_status": "model_proposed",
                "source": result.source.model_dump(mode="json"), "source_format_status": "complete",
                "task": {"name": result.task.name, "description": result.task.description, "fields": fields},
                "table_subject": "named specimen", "objects": objects, "relations": [],
                "observations": items, "quarantined": rejected, "evidence": list(evidence.values())}
    return assemble_dataset(source, result.model_dump(mode="json"), snapshot, subject_label_field=subject_field,
                            identity_fields=[subject_field, "batch"] if batches else [])


@pytest.mark.parametrize("labels, statement, entity, expected, status", [
    ([("A1", "East"), ("A1", "West")], "A1 length = 30 cm.", "A1", "A1", "范围待定"),
    ([("A1", "East"), ("B2", "West")], "Either A1 or B2 length = 30 cm.", None, "A1 或 B2", "主体待定"),
])
def test_grounded_subject_uncertainty_exports_one_filterable_row(tmp_path, labels, statement, entity, expected, status):
    source = f"East: {labels[0][0]}. West: {labels[1][0]}. {statement}"
    data = assemble(source, statement, labels, entity, subject_field="记录状态")
    [row] = data["rows"]
    assert row == {"记录状态": expected, "reading": "30"}
    [obs] = data["observations"]
    assert obs["delivery"] == "provisional" and len(obs["row_ids"]) == 1
    assert {c["id"] for c in obs["delivery_candidates"]} == {"o0", "o1"}
    assert not query_dataset(data)
    assert query_dataset(data, labels[0][0], exploratory=True) == [obs]
    save_dataset(data, tmp_path / "data")
    with (tmp_path / "data/main.csv").open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == ["记录状态", "reading", "reading_min", "reading_max",
                                     "reading_avg", "reading_unit", "直接对象", "观测口径", "记录状态（抽取）"]
        assert status in next(reader)["记录状态（抽取）"]


def test_unproven_field_qualifier_is_printed_without_confirming_it():
    source = "S2: mean length = 30 cm."
    data = assemble(source, source, [("S2", "S2")], "S2", definition="Usable length of the specimen.",
                    property_name="mean length")
    assert data["rows"] == [{"name": "S2", "reading": "30"}]
    [row] = data["lineage"]["rows"]
    assert "mean length" in row["status"] and "Usable length" in row["status"]
    assert data["observations"][0]["delivery"] == "provisional" and not query_dataset(data)


def test_known_batch_is_retained_when_only_the_field_qualifier_is_pending():
    citation = "S2: mean length = 30 cm."
    source = "S2: batch = W9. " + citation
    data = assemble(source, citation, [("S2", "S2")], "S2", definition="Usable length of the specimen.",
                    property_name="mean length", batches=[("S2", "W9")])
    assert data["rows"] == [{"name": "S2", "batch": "W9", "reading": "30"}]
    [row] = data["lineage"]["rows"]
    [identity] = query_dataset(data, field="batch")
    assert row["subject_id"] == "o0" and "batch" not in row["status"]
    assert row["cells"]["batch"] == [identity["observation_id"]]
    assert row["row_id"] in identity["row_ids"] and not query_dataset(data, field="reading")


def test_provisional_row_does_not_inherit_batch_from_an_alternative_subject():
    citation = "Either S2 or T3 length = 30 cm."
    source = "S2: batch = W9. T3: batch = E4. " + citation
    data = assemble(source, citation, [("S2", "S2"), ("T3", "T3")], None, batches=[("S2", "W9"), ("T3", "E4")])
    assert data["rows"] == [{"name": "S2 或 T3", "batch": None, "reading": "30"}]
    [row] = data["lineage"]["rows"]
    assert not row["subject_id"] and "batch" not in row["cells"] and not query_dataset(data)


@pytest.mark.parametrize("source,names,owner,match", [
    ("At ambient temperature the length of an unidentified specimen was 30 cm.", [], None, "supported"),
    ("S2: length = 30 cm.", [("S2", "S2")], "S2", "unsupported"),
    ("S2: length never reached 30 cm.", [("S2", "S2")], "S2", "supported"),
    ("S2 and T1 were observed. One unidentified sample had length = 30 cm.", [("S2", "S2"), ("T1", "T1")], None, "supported"),
])
def test_unowned_and_wrong_properties_stay_in_searchable_details(source, names, owner, match):
    data = assemble(source, source, names, owner, match=match)
    assert not data["rows"] and not query_dataset(data)
    [obs] = query_dataset(data, exploratory=True)
    assert obs["delivery"] == "detail" and obs["value"] == "30"


def test_repeated_value_in_a_single_citation_needs_a_narrower_anchor():
    source = "A3 length = 30 cm. B4 length = 30 cm."
    citations = [{"fields": ["value"], "span": {"start": 0, "end": len(source)}}]
    item = {"value": "30", "source_evidence": citations}
    assert not _has_value_source(item, source)
    citations.append({"fields": ["value"], "span": {"start": 0, "end": source.index(".")}})
    assert _has_value_source(item, source)
