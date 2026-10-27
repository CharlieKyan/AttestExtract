"""Compare all three synthetic D1 cases to independently authored final gold."""

from __future__ import annotations

from importlib.resources import files
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from attestextract.d1_examples import d1_example_names, load_d1_example
from attestextract.storage import load_archive, save_archive


def gold(name):
    resource = files("attestextract").joinpath("examples", "d1", name, "expected.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def record_projection(record):
    return {
        "record_type": record.record_type,
        "fields": {
            name: {
                "value": field.value,
                "quote": field.evidence.quote,
                "start": field.evidence.start,
                "end": field.evidence.end,
            }
            for name, field in record.fields.items()
        },
    }


def assert_expected(archive, expected):
    assert len(archive.extraction.candidates) == expected["candidate_count"]
    assert len(archive.records) == expected["record_count"]
    by_candidate = {item.candidate_id: item for item in archive.reviews}
    assert len(by_candidate) == len(archive.extraction.candidates)
    assert [by_candidate[candidate.candidate_id].status for candidate in archive.extraction.candidates] == expected["review_statuses"]
    assert [record_projection(record) for record in archive.records] == expected["records"]
    for record in archive.records:
        assert record.document_id == archive.document.document_id
        candidate = next(item for item in archive.extraction.candidates if item.candidate_id == record.source_candidate_id)
        assert candidate.fields == record.fields
        assert by_candidate[candidate.candidate_id].status == "accepted"
        for field in record.fields.values():
            evidence = field.evidence
            assert evidence.status == "matched"
            assert archive.document.text[evidence.start:evidence.end] == evidence.quote


def test_d1_examples_cover_all_three_required_record_families():
    assert set(d1_example_names()) == {"attribute", "observation", "relation"}


@pytest.mark.parametrize("name", ["attribute", "observation", "relation"])
def test_synthetic_cases_match_manual_gold_and_survive_archive_round_trip(name, tmp_path):
    expected = gold(name)
    assert expected["case_type"] == name
    archive = load_d1_example(name)
    assert archive.extraction.run.mode == "replay"
    assert_expected(archive, expected)
    assert all(item.origin == "synthetic_fixture" for item in archive.reviews if item.status == "accepted")

    path = tmp_path / f"{name}.archive.json"
    save_archive(archive, path)
    loaded = load_archive(path)
    assert loaded.model_dump(mode="json") == archive.model_dump(mode="json")
    assert_expected(loaded, expected)


def test_observation_keeps_conditions_in_final_records():
    archive = load_d1_example("observation")
    expected = gold("observation")
    assert_expected(archive, expected)
    assert archive.extraction.task.record_types
    for record in archive.records:
        record_spec = archive.extraction.task.record_types[record.record_type]
        assert record_spec.condition_fields
        for condition in record_spec.condition_fields:
            assert condition in record.fields
            assert record.fields[condition].value is not None


def run_cli(*args: str, cwd: Path):
    env = {key: value for key, value in os.environ.items() if not key.startswith("ATTESTEXTRACT_")}
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "attestextract", *args], cwd=cwd, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )


def test_all_d1_examples_run_without_model_configuration_outside_source_tree(tmp_path):
    process = run_cli("d1", "--example", "all", cwd=tmp_path)
    assert process.returncode == 0, process.stderr
    payload = json.loads(process.stdout)
    assert set(payload["examples"]) == {"attribute", "observation", "relation"}
    for name, archive in payload["examples"].items():
        expected = gold(name)
        assert archive["extraction"]["run"]["mode"] == "replay"
        assert len(archive["extraction"]["candidates"]) == expected["candidate_count"]
        assert len(archive["records"]) == expected["record_count"]


def test_single_d1_example_writes_a_loadable_archive(tmp_path):
    output = tmp_path / "relation.archive.json"
    process = run_cli("d1", "--example", "relation", "--output", str(output), cwd=tmp_path)
    assert process.returncode == 0, process.stderr
    assert_expected(load_archive(output), gold("relation"))
