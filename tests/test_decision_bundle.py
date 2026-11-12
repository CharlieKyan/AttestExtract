"""Synthetic successful traces, tampering and final-leaf decision coverage."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from attestextract import ExecutionOptions, __version__, extract
from attestextract.backends import ReplayBackend
from attestextract.decision_bundle import (
    DecisionBundle, decision_bundle_summary, load_decision_bundle,
    prepare_decision_bundle, save_decision_bundle,
)
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError
from attestextract.execution import digest
from attestextract.models import BackendResponse


TEXT = "Sensor A reads 11. Sensor B reads 22.\n\nNo values here."
TASK = {"name": "Synthetic sensor readings", "record_types": {"reading": {
    "fields": {"subject": {"type": "string", "required": True},
               "amount": {"type": "integer", "required": True}},
    "identity_fields": ["subject"],
}}}
RESPONSES = [{"records": [
    {"record_type": "reading", "values": {"subject": "Sensor A", "amount": 11},
     "quotes": {"subject": "Sensor A reads 11.", "amount": "Sensor A reads 11."}},
    {"record_type": "reading", "values": {"subject": "Sensor B", "amount": 22},
     "quotes": {"subject": "Sensor B reads 22.", "amount": "Absent quote"}},
]}, {"records": []}]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_run(directory, *, responses=RESPONSES, budget=8, resume=None, max_chars=40, backend=None):
    """A reusable two-candidate/one-empty synthetic fixture, with real checkpoints."""
    trace = RunTrace(directory / "diagnostics")
    result = extract(TEXT, TASK, backend=backend or ReplayBackend(responses), trace=trace,
                     max_chunk_chars=max_chars, source_name="synthetic.txt",
                     backend_identity="synthetic-replay-v1",
                     execution=ExecutionOptions(adaptive_chunking=False, min_chunk_chars=1,
                                                max_model_calls=budget), resume_from=resume)
    trace.finish(result.model_dump(mode="json"))
    return result


def hashes(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def update_checkpoint(directory, update):
    path = directory / "diagnostics/execution/checkpoint.json"
    payload = read(path)
    update(payload["state"])
    payload["sha256"] = digest(payload["state"])
    write(path, payload)


def test_all_candidates_and_empty_chunks_are_exported_without_changing_run(tmp_path):
    run = tmp_path / "run"
    result = make_run(run)
    # Unlisted sensitive artifacts must not be read or copied.
    (run / "diagnostics/backend.json").write_text("not JSON: private configuration", encoding="utf-8")
    (run / ".env").write_text("SYNTHETIC_SECRET=not-a-credential", encoding="utf-8")
    original = hashes(run)
    bundle = prepare_decision_bundle(run)
    assert bundle.extraction == result
    assert bundle.document.text == TEXT
    assert [batch.chunk_id for batch in bundle.batches] == ["chunk-0001", "chunk-0002"]
    assert len(bundle.batches[0].candidate_ids) == len(bundle.batches[0].task_ids) == 2
    assert bundle.batches[1].candidate_ids == []
    assert [task.kind for task in bundle.tasks] == ["candidate_review", "candidate_review", "empty_chunk_review"]
    assert bundle.tasks[-1].allowed_actions == ["review"]
    assert all(not task.evidence for task in bundle.tasks)
    summary = decision_bundle_summary(bundle)
    assert summary == {"bundle_id": bundle.bundle_id, "analysis_package_version": __version__,
                       "batch_count": 2, "candidate_count": 2,
                       "candidate_task_count": 2, "empty_chunk_count": 1, "task_count": 3,
                       "review_candidate_count": 1, "unflagged_candidate_count": 1,
                       "analysis_link_change_count": 0, "model_calls": 0}
    assert hashes(run) == original
    assert prepare_decision_bundle(run / "diagnostics") == bundle
    target = tmp_path / "decisions/bundle.json"
    save_decision_bundle(bundle, target)
    assert load_decision_bundle(target) == bundle
    with pytest.raises(FileExistsError):
        save_decision_bundle(bundle, target)
    serialized = target.read_text(encoding="utf-8")
    assert "SYNTHETIC_SECRET" not in serialized and "private configuration" not in serialized
    assert serialized.count('"text":') == 1


@pytest.mark.parametrize("relative", ["document.json", "task.json", "result.json", "chunk_plan.json",
                                      "execution/checkpoint.json", "chunks/chunk-0002.json"])
def test_missing_required_diagnostic_is_not_an_empty_result(tmp_path, relative):
    make_run(tmp_path / "run")
    (tmp_path / "run/diagnostics" / relative).unlink()
    with pytest.raises(ValueError, match="missing"):
        prepare_decision_bundle(tmp_path / "run")


@pytest.mark.parametrize("status", ["failed", "running", "checked"])
def test_non_successful_run_is_rejected(tmp_path, status):
    make_run(tmp_path / "run")
    path = tmp_path / "run/diagnostics/manifest.json"
    manifest = read(path)
    manifest["status"] = status
    write(path, manifest)
    with pytest.raises(ValueError, match="succeeded"):
        prepare_decision_bundle(tmp_path / "run")


@pytest.mark.parametrize("target,mutate", [
    ("document.json", lambda value: value.update(text=TEXT + "tampered")),
    ("task.json", lambda value: value.update(name="another task")),
    ("manifest.json", lambda value: value.update(trace_schema_version="99")),
    ("manifest.json", lambda value: value.update(artifacts=[{}])),
    ("execution/checkpoint.json", lambda value: value.update(sha256="0" * 64)),
    ("chunks/chunk-0001.json", lambda value: value.update(text="changed")),
    ("chunks/chunk-0001.json", lambda value: value["segments"][0].update(end=3)),
    ("result.json", lambda value: value["candidates"][0].update(chunk_ids=["chunk-9999"])),
    ("result.json", lambda value: value["candidates"][0].update(chunk_ids=["chunk-0002"])),
    ("result.json", lambda value: value["candidates"][0]["chunk_ids"].append("chunk-0002")),
    ("result.json", lambda value: value["candidates"][1]["chunk_ids"].append("chunk-0002")),
])
def test_source_tampering_is_rejected(tmp_path, target, mutate):
    run = tmp_path / "run"
    make_run(run)
    path = run / "diagnostics" / target
    payload = read(path)
    mutate(payload)
    write(path, payload)
    with pytest.raises(ValueError):
        prepare_decision_bundle(run)


@pytest.mark.parametrize("mutate", [
    lambda state: state["roots"].append(state["roots"][0]),
    lambda state: state["nodes"]["chunk-0002"].update(status="failed"),
    lambda state: state["nodes"]["chunk-0001"].update(children=["chunk-0002"]),
    lambda state: state["nodes"]["chunk-0001"].update(depth=3),
    lambda state: state.update(calls_used=100),
    lambda state: state["nodes"].update({"chunk-9999": state["nodes"]["chunk-0001"].copy()}),
])
def test_checksums_do_not_replace_tree_and_completion_validation(tmp_path, mutate):
    run = tmp_path / "run"
    make_run(run)
    update_checkpoint(run, mutate)
    with pytest.raises(ValueError):
        prepare_decision_bundle(run)


@pytest.mark.parametrize("mutate", [
    lambda value: value["tasks"].pop(),
    lambda value: value["tasks"][0].update(allowed_actions=["review"]),
    lambda value: value["tasks"][-1].update(allowed_actions=["select", "reject", "review"]),
    lambda value: value["batches"][0]["candidate_ids"].pop(),
    lambda value: value["batches"][0]["segments"][0].update(start=2),
    lambda value: value.update(decision_bundle_version="99"),
    lambda value: value.update(bundle_id="decision-bundle-wrong"),
])
def test_bundle_load_revalidates_coverage_permissions_and_content(tmp_path, mutate):
    run = tmp_path / "run"
    make_run(run)
    payload = prepare_decision_bundle(run).model_dump()
    mutate(payload)
    with pytest.raises(ValueError):
        DecisionBundle.model_validate(payload)


def test_mutated_model_instances_are_revalidated_before_save(tmp_path):
    make_run(tmp_path / "run")
    bundle = prepare_decision_bundle(tmp_path / "run")
    bundle.tasks[0].candidate_ids.clear()
    with pytest.raises(ValueError):
        save_decision_bundle(bundle, tmp_path / "must-not-exist.json")
    assert not (tmp_path / "must-not-exist.json").exists()


def test_legacy_links_are_reanalyzed_separately_without_rewriting_originals(tmp_path):
    run = tmp_path / "historical"
    trace = RunTrace(run / "diagnostics")
    task = json.loads(json.dumps(TASK))
    task["record_types"]["reading"]["comparison_fields"] = ["amount"]
    response = {"records": [
        {"record_type": "reading", "values": {"subject": f"Sensor {subject}", "amount": 11},
         "quotes": {"subject": f"Sensor {subject} reads 11.", "amount": f"Sensor {subject} reads 11."}}
        for subject in ("A", "B")
    ]}
    result = extract("Sensor A reads 11. Sensor B reads 11.", task,
                     backend=ReplayBackend([response]), backend_identity="synthetic-history",
                     trace=trace, execution=ExecutionOptions(adaptive_chunking=False))
    # Explicit historical fixture: the former classifier called different
    # identity names with equal measurements a possible conflict.
    result.links[0].kind = "possible_conflict"
    trace.finish(result.model_dump(mode="json"))
    original = hashes(run)
    bundle = prepare_decision_bundle(run)
    assert bundle.extraction.links[0].kind == "possible_conflict"
    assert bundle.analysis_links[0].kind == "insufficient_context"
    assert decision_bundle_summary(bundle)["analysis_link_change_count"] == 1
    assert hashes(run) == original


# Historical prose-protocol fixture; automatic table A is covered separately.
def test_shared_candidate_has_one_logical_task_and_references_in_each_batch(tmp_path):
    run = tmp_path / "table"
    trace = RunTrace(run / "diagnostics")
    text = "| Sensor A reads 11. | Amount |\n| --- | --- |\n| X | 1 |\n| Y | 2 |\n"
    response = {"records": [RESPONSES[0]["records"][0]]}
    result = extract(text, TASK, table_mode="text", backend=ReplayBackend([response, response]),
                     backend_identity="synthetic-shared-header", trace=trace,
                     table_rows_per_chunk=1, execution=ExecutionOptions(adaptive_chunking=False))
    trace.finish(result.model_dump(mode="json"))
    bundle = prepare_decision_bundle(run)
    assert len(bundle.extraction.candidates) == len(bundle.tasks) == 1
    assert len(bundle.batches) == 2
    assert bundle.batches[0].candidate_ids == bundle.batches[1].candidate_ids
    assert len(bundle.batches[0].task_ids) == 1 and bundle.batches[1].task_ids == []
    assert decision_bundle_summary(bundle)["empty_chunk_count"] == 0


def test_errors_do_not_echo_private_source_or_duplicate_json_values(tmp_path):
    run = tmp_path / "run"
    make_run(run)
    document = run / "diagnostics/document.json"
    payload = read(document)
    payload["text"] = "PRIVATE-SOURCE-MARKER"
    write(document, payload)
    with pytest.raises(ValueError) as error:
        prepare_decision_bundle(run)
    assert "PRIVATE-SOURCE-MARKER" not in str(error.value)
    with pytest.raises(ValueError, match="Duplicate JSON"):
        target = tmp_path / "duplicate.json"
        target.write_text('{"document":"PRIVATE-SOURCE-MARKER","document":{}}', encoding="utf-8")
        load_decision_bundle(target)


def test_resume_exports_self_contained_final_leaves_without_history_reads(tmp_path):
    old, resumed = tmp_path / "old", tmp_path / "resumed"
    with pytest.raises(BackendError):
        make_run(old, budget=1)
    result = make_run(resumed, budget=2, responses=[RESPONSES[-1]], resume=old)
    update_checkpoint(resumed, lambda state: state.update(history=[str(tmp_path / "does-not-exist")]))
    # A successful resumed run has no raw completion for its reused first leaf.
    assert not (resumed / "diagnostics/responses/chunk-0001.txt").exists()
    bundle = prepare_decision_bundle(resumed)
    assert bundle.extraction == result
    assert len(bundle.batches) == 2 and len(bundle.extraction.candidates) == 2
    assert str(tmp_path) not in bundle.model_dump_json()


def test_split_parents_are_not_exported_as_duplicate_material(tmp_path):
    class TruncatedThenReplay(ReplayBackend):
        def generate(self, request):
            if request.chunk_id == "chunk-0001":
                return BackendResponse(text="truncated", finish_reason="length", model="replay")
            return super().generate(request)

    # The first large block splits into three sentence-preserving children.
    responses = [{"records": [RESPONSES[0]["records"][0]]},
                 {"records": [RESPONSES[0]["records"][1]]}, RESPONSES[1]]
    make_run(tmp_path / "run", max_chars=1000, backend=TruncatedThenReplay(responses))
    bundle = prepare_decision_bundle(tmp_path / "run")
    assert [batch.chunk_id for batch in bundle.batches] == ["chunk-0001.1", "chunk-0001.2", "chunk-0001.3"]
    assert len(bundle.extraction.candidates) == 2
    assert decision_bundle_summary(bundle)["empty_chunk_count"] == 1


def test_symlink_cannot_redirect_a_whitelisted_artifact_outside_run(tmp_path):
    run = tmp_path / "run"
    make_run(run)
    target = run / "diagnostics/chunks/chunk-0001.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links")
    with pytest.raises(ValueError, match="outside"):
        prepare_decision_bundle(run)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junctions")
def test_windows_junction_cannot_redirect_chunk_directory_outside_run(tmp_path):
    import _winapi

    run = tmp_path / "run"
    make_run(run)
    chunks = run / "diagnostics/chunks"
    outside = tmp_path / "outside-chunks"
    # Rename only this test's known synthetic directory, then replace its path
    # with a junction: it requires no Windows symbolic-link privilege.
    chunks.rename(outside)
    try:
        _winapi.CreateJunction(str(outside), str(chunks))
    except OSError:
        pytest.skip("This Windows process cannot create directory junctions")
    try:
        with pytest.raises(ValueError, match="outside"):
            prepare_decision_bundle(run)
    finally:
        # Remove the junction itself, never recurse into its outside target.
        os.rmdir(chunks)
