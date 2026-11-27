"""Check what a reader can see, query and retain after a second opinion."""

import csv
import hashlib
import json
from copy import deepcopy

import pytest

from niceextract import export_observations, query_dataset, save_dataset
from niceextract.dataset_annotations import finalize_annotations
from niceextract.jev_packets import make_packets
from niceextract.jev_projection import final_dataset
from niceextract.jev_review import review_dataset
from test_jev_review import LocalScorer


SOURCE = "Site A contains sample X. Measurements are pending."


def _view():
    evidence = {"evidence_id": "s", "start": 0, "end": 25, "quote": SOURCE[:25]}
    direct = {"id": "x", "label": "sample X", "status": "supported", "evidence": []}
    target = {"id": "a", "label": "Site A", "status": "supported", "evidence": ["s"]}
    reading = {"observation_id": "o", "entity": "sample X", "field": "length", "value": "8", "unit": "mm",
               "statement_basis": "inferred", "citation_status": "unverified",
               "citation_reason": "value_not_located", "citation_evidence": [], "measurement_use": "model_input",
               "conditions": None, "statistic": None, "purpose": "unspecified", "target_match": "unknown",
               "source_kind": "prose", "source_evidence": [], "eligible": False, "fit": {"status": "unknown"},
               "direct": direct, "target": target, "filing_subjects": [target], "row_ids": ["r"],
               "delivery": "provisional", "associated_row": True}
    return {"dataset_version": "2", "schema": ["site", "length", "length_依据"], "status_column": "记录状态",
            "context_columns": {"direct": "直接对象", "statistic": "口径"}, "subject_label_field": "site",
            "field_policies": {}, "field_definitions": {"length": "sample length"}, "identity_fields": ["site"],
            "source_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(), "observations": [reading],
            "rows": [{"site": "Site A", "length": "8", "length_依据": None}],
            "lineage": {"evidence": [evidence], "objects": [], "rows": [{"row_id": "r", "subject_label": "Site A",
                        "subject_id": "a", "cells": {"site": ["o"], "length": ["o"]}, "observations": ["o"],
                        "context": [], "delivery": "provisional", "status": "引文未核实"}]}}


def _csv(data, dest):
    save_dataset(data, dest)
    with (dest / "main.csv").open(encoding="utf-8-sig", newline="") as stream:
        row, = csv.DictReader(stream)
    return row


def test_basis_is_independent_of_citation_and_attribution(tmp_path):
    data = _view()
    assert not query_dataset(data)  # old file's default
    finalize_annotations(data)
    row = _csv(data, tmp_path / "published")
    assert row["length"] == "8" and row["length_依据"] == ""
    assert row["length_依据（抽取）"] == "模型推断" and row["length_引文状态"] == "引文未核实"
    assert row["site_依据"] == "尚未判明" and row["site_引文状态"] == "已定位"
    subject, = data["lineage"]["rows"][0]["subject_annotations"]
    assert subject["evidence"] == data["lineage"]["evidence"]
    assert row["直接对象"] == "sample X"
    obs, = query_dataset(data, "Site A", statement_basis="inferred", citation_status="unverified")
    assert query_dataset(data, "sample X", measurement_use="model_input") == [obs]
    assert not query_dataset(data, include_pending=False) and not query_dataset(data, citation_status="located")
    assert query_dataset(data, "Site", subject_match="contains") == [obs]
    assert not query_dataset(data, "x", subject_match="contains") and query_dataset(data, "x") == [obs]
    with pytest.raises(ValueError, match="subject_match"):
        query_dataset(data, subject_match="contains")


def test_grouped_readings_annotate_the_corresponding_statistic(tmp_path):
    data = _view()
    avg = deepcopy(data["observations"][0])
    avg.update(observation_id="avg", value="4", statement_basis="reported", citation_status="located")
    data["observations"].append(avg)
    data["rows"][0]["length"] = "8（最大）；4（平均）"
    proof = data["lineage"]["rows"][0]
    proof["cells"]["length"].append("avg")
    proof["observations"].append("avg")
    data["statistic_columns"] = {"length": {"max": "length_max", "avg": "length_avg"}}
    proof["statistics"] = {"length_max": {"value": 8, "observations": ["o"]},
                           "length_avg": {"value": 4, "observations": ["avg"]}}
    finalize_annotations(data)
    row = _csv(data, tmp_path / "stats")
    assert row["length_max"] == "8" and row["length_max_依据"] == "模型推断"
    assert row["length_avg"] == "4" and row["length_avg_引文状态"] == "已定位"
    assert row["length_依据（抽取）"] == "8（模型推断）｜4（原文明示）"


@pytest.mark.parametrize("choice,state", [("reject", "disagreed"), ("bind_1", "filing_suggested")])
def test_reader_keeps_original_value_and_owner_after_jev_opinion(tmp_path, choice, state):
    data = _view()
    finalize_annotations(data)
    packet, = make_packets(data, SOURCE)
    assert packet["options"]["bind_1"]["role"] == "filing_subject"
    output = final_dataset(data, [packet], {"o": {"choice": choice, "confidence": 0.9}})
    obs, = query_dataset(output, "a")
    assert obs["direct"]["id"] == "x" and obs["target"]["id"] == "a" and obs["jev_review"]["state"] == state
    row = _csv(output, tmp_path / "jev")
    assert row["length"] == "8" and row["length_引文状态"] == "引文未核实" and "Jev" in row["记录状态"]
    assert output["rows"] == data["rows"] and "jev_review" not in data["observations"][0]


def test_ordinary_filed_inference_is_not_queued_for_jev():
    data = _view()
    data["observations"][0]["citation_status"] = "located"
    finalize_annotations(data)
    assert make_packets(data, SOURCE) == []


def test_filtered_csv_contains_one_reading_with_undecided_subjects_and_never_overwrites(tmp_path):
    data = _view()
    obs, = data["observations"]
    obs["filing_subjects"].append({**obs["target"], "id": "b", "label": "Site B", "scope": "east"})
    data["observations"].append(dict(obs, observation_id="second-source", corroborates="o", row_ids=[]))
    data["observations"].append(dict(obs, observation_id="other", statement_basis="reported", measurement_use="measured"))
    finalize_annotations(data)
    path = export_observations(data, tmp_path / "filtered.csv", "sample X", statement_basis="inferred",
                               citation_status="unverified", measurement_use="model_input")
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        row, = csv.DictReader(stream)
    assert (row["observation_id"], row["direct_subject"], row["target_subject"], row["value"], row["unit"]) == (
        "o", "sample X", "", "8", "mm")
    assert (row["statement_basis"], row["citation_status"], row["measurement_use"]) == ("inferred", "unverified", "model_input")
    assert "Site A" in row["target_candidates"] and "Site B（east）" in row["target_candidates"]
    with pytest.raises(FileExistsError):
        export_observations(data, path)
    assert path.read_bytes() == raw


def test_review_entry_accounts_for_corroboration_of_a_published_pending_value(tmp_path):
    data = _view()
    primary, = data["observations"]
    secondary = dict(deepcopy(primary), observation_id="second", row_ids=[], delivery="detail", corroborates="o")
    primary["corroborating_observations"] = ["second"]
    data["observations"].append(secondary)
    finalize_annotations(data)
    save_dataset(data, tmp_path / "input")
    source = tmp_path / "source.md"
    source.write_bytes(SOURCE.encode())
    engine = LocalScorer()
    output, report = review_dataset(source, tmp_path / "input", tmp_path / "reviewed", engine)
    reviewed = json.loads((output / "dataset.json").read_bytes())
    assert report["queued"] == engine.calls == 1 and report["errors"] == 0
    assert [obs["observation_id"] for obs in query_dataset(reviewed)] == ["o"]
    assert reviewed["observations"][1]["corroborates"] == "o"
