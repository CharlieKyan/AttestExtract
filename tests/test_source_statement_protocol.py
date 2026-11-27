"""New live observations keep a stated use, inference status and a proposed filing target."""

import json

import pytest

from niceextract import cli, extract
from niceextract.backends import ReplayBackend
from niceextract.dataset_snapshot import checked_snapshot
from niceextract.errors import InvalidResponseError
from niceextract.models import BackendResponse
from niceextract.table_routes import build_table_input
from niceextract.task_input import resolve_task


TASK = {"prompt_description": "Collect settings and readings.", "field_list": {"load": "Load or boundary setting."}}
TEXT = "A load of 12 N was applied to the edge of S at the boundary."


class FixtureBackend:
    mode = "live"
    name = "source-protocol-fixture"
    output_protocol = "flat-v1"

    def __init__(self, response):
        self.response = response
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return BackendResponse(text=json.dumps(self.response))


def prose_record():
    return {"record_type": "load", "values": {"value": "12", "entity": "edge of S", "conditions": "at the boundary", "unit": "N"},
            "quotes": dict.fromkeys(("value", "entity", "conditions", "unit"), TEXT),
            "statement_basis": "reported", "reported_use": "boundary_condition", "source_property": "load"}


def test_default_live_prose_keeps_uncited_row_distinct_from_owner():
    item = prose_record()
    item.update(name_handling="related", row_relation={"target": "S", "predicate": "part_of",
                "field_applicability": "uncertain", "evidence": []})
    backend = FixtureBackend({"records": [item]})
    task = resolve_task(TASK).model_copy(update={"row_subject": "named specimens"})
    result = extract(TEXT, task, backend=backend)
    assert result.task.statement_protocol == "source-v2" and result.task.row_relation_protocol == "explanation-v1"
    assert "statement_basis" in json.dumps(backend.requests[0].output_schema)
    snapshot, _ = checked_snapshot(TEXT, result.model_dump(mode="json"), None)
    obs, = snapshot["observations"]
    assert (obs["value"], obs["unit"], obs["conditions"], obs["entity"]) == ("12", "N", "at the boundary", "edge of S")
    assert (obs["statement_basis"], obs["reported_use"]) == ("reported", "boundary_condition")
    assert obs["source_property"] == "load" and obs["source_property_evidence_ids"]
    receipt, = obs["row_relations"]
    assert (receipt["target"], receipt["status"], receipt["evidence"]) == ("S", "unresolved", [])


def test_reported_property_and_inferred_filing_keep_distinct_bases_on_their_citations():
    item = prose_record()
    item.update(name_handling="related", row_relation={"target": "S", "predicate": "part_of",
                "field_applicability": "included", "target_basis": "inferred", "evidence": [{"quote": "edge of S", "ref": None}]})
    task = resolve_task(TASK).model_copy(update={"row_subject": "named specimens"})
    result = extract(TEXT, task, backend=FixtureBackend({"records": [item]}))
    snapshot, _ = checked_snapshot(TEXT, result.model_dump(mode="json"), None)
    obs, = snapshot["observations"]
    link, = obs["row_relations"]
    citation, = link["evidence"]
    assert (obs["statement_basis"], link["target_basis"], link["status"]) == ("reported", "inferred", "located")
    assert TEXT[citation["start"]:citation["end"]] == "edge of S"
    legacy = {key: value for key, value in item.items() if key not in ("statement_basis", "source_property", "reported_use")}
    with pytest.raises(InvalidResponseError, match="target_basis"):
        extract(TEXT, task, backend=ReplayBackend([{"records": [legacy]}]), row_relation_evidence=True)


def test_source_protocol_without_row_subject_preserves_inference_instead_of_adding_an_owner():
    item = dict(prose_record(), statement_basis="inferred")
    result = extract(TEXT, TASK, backend=FixtureBackend({"records": [item]}))
    assert result.candidates[0].statement_basis == "inferred"
    assert result.task.row_relation_protocol is None
    with pytest.raises(InvalidResponseError, match="source_property|statement_basis"):
        extract(TEXT, TASK, backend=ReplayBackend([{"records": [item]}]))


def test_classifications_omitted_by_a_live_provider_preserve_an_unresolved_observation():
    item = prose_record()
    for key in ("statement_basis", "reported_use", "source_property"):
        del item[key]
    result = extract(TEXT, TASK, backend=FixtureBackend({"records": [item]}),
                     dataset_options={"row_subject": "named specimens"})
    assert result.task.row_relation_protocol == "explanation-v1"
    candidate, = result.candidates
    assert (candidate.statement_basis, candidate.reported_use, candidate.name_handling) == ("unknown", "unspecified", "uncertain")
    assert candidate.fields["value"].value == "12"


def test_live_table_and_resume_keep_original_semantics(tmp_path):
    source = "| Sample | Load |\n|---|---|\n| X | 12 |"
    table = build_table_input(source)
    cells = {e["quote"].strip(): {"ref": e["ref"], "quote": e["quote"]} for e in table["entries"]}
    item = {"record_id": "r1", "record_type": "load", "scope": "individual", "scope_sources": [],
            "anchors": [cells["12"]], "statement_basis": "reported", "reported_use": "model_input",
            "name_handling": "as_reported", "row_relation": None,
            "fields": {"entity": {"value": "X", "relation": "row", "sources": [cells["X"]]},
                       "value": {"value": "12", "relation": "anchor", "sources": [cells["12"]]}}}
    task = resolve_task(TASK).model_copy(update={"row_subject": "named samples"})
    run = tmp_path / "first"
    source_backend = FixtureBackend({"records": [item]})
    fresh = extract(source, task, backend=source_backend, backend_identity="fixture", output_dir=run)
    definitions = source_backend.requests[0].output_schema["$defs"]
    assert {"statement_basis", "reported_use"} <= set(definitions["TableRecord"]["required"])
    assert "row_relation" not in definitions["TableRecord"]["properties"]
    assert "row_relation" in definitions["RowLinkedRecord"]["required"]
    backend = FixtureBackend({"records": []})
    resumed = extract(source, task, backend=backend, backend_identity="fixture", resume_from=run, output_dir=tmp_path / "resumed")
    assert backend.requests == []
    for result in (fresh, resumed):
        snapshot, _ = checked_snapshot(source, result.model_dump(mode="json"), None)
        obs, = snapshot["observations"]
        assert (obs["entity"], obs["value"], obs["statement_basis"], obs["reported_use"]) == ("X", "12", "reported", "model_input")


def test_cli_check_uses_both_protocols_on_regular_command(tmp_path, monkeypatch):
    monkeypatch.setenv("NICEEXTRACT_API_KEY", "synthetic-local-only")
    source, task, output = tmp_path / "source.md", tmp_path / "task.json", tmp_path / "out"
    source.write_text(TEXT)
    task.write_text(json.dumps(TASK))
    assert cli.main(["extract", "--input", str(source), "--task", str(task), "--table-subject", "named specimens",
                     "--base-url", "https://test.invalid/v1", "--model", "test", "--structured-output", "json_object",
                     "--output-dir", str(output), "--check"]) == 0
    settings = json.loads((output / "diagnostics/task.json").read_bytes())
    assert settings["statement_protocol"] == "source-v2" and settings["row_relation_protocol"] == "explanation-v1"
