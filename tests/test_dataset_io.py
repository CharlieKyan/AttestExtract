"""On-disk contract at the normal and offline assembly entry points."""

import csv
import json

import pytest

from niceextract import assemble_from_files, cli, extract, save_dataset
from niceextract.models import BackendResponse


def test_bundle_is_atomic_and_preserves_schema_unicode_and_provenance(tmp_path):
    data = {
        "schema": ["label", "signal"],
        "rows": [{"label": "甲,乙", "signal": "3.70"}],
        "observations": [{"observation_id": "obs-3", "entity": "α", "field": "signal",
            "value": "3.70", "conditions": "steady\r\nstate",
            "direct": {"label": "原对象"}, "target": {"label": "甲,乙"}}],
        "lineage": {"rows": [{"row_id": "r1", "cells": {"signal": ["obs-3"]}}]},
    }
    target = save_dataset(data, tmp_path / "output")
    assert json.loads((target / "dataset.json").read_bytes()) == data
    assert json.loads((target / "lineage.json").read_bytes()) == data["lineage"]
    with (target / "main.csv").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == data["schema"]
        assert list(reader) == data["rows"]
    with (target / "observations.csv").open(encoding="utf-8-sig", newline="") as stream:
        row, = csv.DictReader(stream)
    assert (row["direct_subject"], row["target_subject"]) == ("原对象", "甲,乙")
    assert row["conditions"] == "steady\r\nstate"
    with pytest.raises(FileExistsError):
        save_dataset(data, target)
    broken = {**data, "rows": [{"foreign": 12}]}
    with pytest.raises(ValueError, match="foreign"):
        save_dataset(broken, tmp_path / "broken")
    assert list(tmp_path.iterdir()) == [target]


def test_frozen_status_only_view_reexports_with_its_own_column_contract(tmp_path):
    old = {"schema": ["trial", "reading"], "subject_label_field": "trial", "status_column": "记录状态",
           "rows": [{"trial": "E1", "reading": "8"}],
           "lineage": {"rows": [{"status": "已确认", "context": [], "subject_label": "E1"}]},
           "observations": []}
    target = save_dataset(old, tmp_path / "legacy")
    with (target / "main.csv").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == ["trial", "reading", "记录状态"]
        assert next(reader) == {"trial": "E1", "reading": "8", "记录状态": "已确认"}


class _StationBackend:
    name, mode, output_protocol, max_attempts = "synthetic-io", "replay", "flat-v1", 1

    def __init__(self):
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        if request.chunk_id.startswith("verify-"):
            payload = json.loads(request.messages[-1]["content"])
            observation, = payload["observations"]
            response = {"action": "decide", "question": "Which station?", "query": None,
                "search_offset": 0, "passage_ids": [], "neighbors": 0, "objects": [],
                "relations": [], "bindings": [{"observation_id": observation["observation_id"],
                "subject_id": None, "status": "unknown", "evidence": [], "reason": "Ambiguous.",
                "target_match": "unknown", "property_name": None, "statistic": None}]}
        else:
            sentence = "Station α recorded 3.70 m."
            response = {"records": [{"record_type": "signal", "values": {"entity": "Station α",
                "value": "3.70", "unit": "m", "conditions": None},
                "quotes": {"entity": sentence, "value": sentence, "unit": sentence, "conditions": ""}}]}
        return BackendResponse(text=json.dumps(response), model=self.name)


def test_new_run_rebuilds_from_disk_offline_preserving_crlf(tmp_path, monkeypatch, capsys):
    import httpx

    def forbid_network(*args, **kwargs):
        pytest.fail("Dataset assembly attempted an HTTP request")

    monkeypatch.setattr(httpx.Client, "send", forbid_network)
    source = tmp_path / "source.txt"
    text = "Observations\r\n\r\nStation α recorded 3.70 m."
    source.write_bytes(text.encode("utf-8"))
    backend = _StationBackend()
    run = tmp_path / "run"
    result = extract(text, prompt_description="Extract station readings", field_list={"station": "Station name",
        "signal": "Reported signal height"}, source_name=source.name, backend=backend,
        backend_identity="synthetic-io-v1", table_subject="stations", dataset_options={"subject_label_field": "station"},
        output_dir=run, progress="quiet")
    assert backend.calls == 2
    assert "dataset" not in result.model_dump(mode="json")
    obs, = result.dataset["observations"]
    assert (obs["entity"], obs["value"]) == ("Station α", "3.70")
    config = tmp_path / "config.json"
    config.write_bytes(b'{"subject_label_field":"station"}')
    destination = tmp_path / "rebuilt"
    assert cli.main(["assemble-dataset", "--input", str(source), "--run-dir", str(run),
                     "--dataset-config", str(config), "--output-dir", str(destination)]) == 0
    assert json.loads((destination / "dataset.json").read_bytes()) == result.dataset
    for path in (run / "dataset").iterdir():
        assert path.read_bytes() == (destination / path.name).read_bytes()
    capsys.readouterr()
    assert cli.main(["supplement-dataset", "--input", str(source), "--run-dir", str(run),
                     "--dataset-config", str(config), "--ledger", str(tmp_path / "review.sqlite"),
                     "--max-calls", "0"]) == 0
    assert json.loads(capsys.readouterr().out)["attempts_used"] == 0
    source.write_bytes(text.replace("\r\n", "\n").encode())
    with pytest.raises(ValueError):
        assemble_from_files(source, run, output_dir=tmp_path / "mismatched")
    assert not (tmp_path / "mismatched").exists()
    assert cli.main(["assemble-dataset", "--input", str(source), "--run-dir", str(tmp_path / "absent"),
                     "--output-dir", str(tmp_path / "unused")]) == 1
    assert "result.json" in capsys.readouterr().err


def test_raw_dataset_options_add_no_analysis_generation(tmp_path):
    backend = _StationBackend()
    source = tmp_path / "raw.txt"
    text = "Station α recorded 3.70 m."
    source.write_bytes(text.encode())
    result = extract(text, prompt_description="Extract readings",
        field_list={"signal": "Reported signal height"}, backend=backend, output_dir=tmp_path / "raw",
        backend_identity="synthetic-io-v1", dataset_options={"row_subject": "station"}, progress="quiet")
    assert backend.calls == 1 and result.analysis is None
    assert result.dataset["observations"][0]["value"] == "3.70"
    assert (tmp_path / "raw/dataset/main.csv").is_file()
    assert assemble_from_files(source, tmp_path / "raw", raw_only=True,
                               row_subject="station") == result.dataset
    options = tmp_path / "options.json"
    options.write_text('{"row_subject":"station"}', encoding="utf-8")
    rebuilt = tmp_path / "raw-rebuilt"
    args = ["assemble-dataset", "--input", str(source), "--run-dir", str(tmp_path / "raw"),
            "--dataset-config", str(options), "--output-dir", str(rebuilt)]
    assert cli.main(args) == 1 and not rebuilt.exists()
    assert cli.main([*args, "--raw-only"]) == 0
    assert json.loads((rebuilt / "dataset.json").read_bytes()) == result.dataset
    with pytest.raises(FileNotFoundError):
        assemble_from_files(source, tmp_path / "raw")
    with pytest.raises(ValueError, match="identity_fields"):
        extract("Station α recorded 3.70 m.", prompt_description="Extract readings",
                field_list={"signal": "Reported signal height"}, backend=backend,
                dataset_options={"identity_fields": ["not_a_field"]})
    assert backend.calls == 1
