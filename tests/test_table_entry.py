"""The public entry routes source tables without changing model declarations."""
from copy import deepcopy
import hashlib
import json

import pytest

from attestextract import extract, ExtractionResult, ExecutionOptions, GenerationOptions, RunTrace
from attestextract.archive import create_archive
from attestextract.documents import Document
from attestextract.errors import BackendError, ConfigurationError, InvalidResponseError
from attestextract.models import BackendResponse
from attestextract.reporting import render_markdown_report


TASK = {"name": "Synthetic table observations", "record_types": {"reading": {
    "fields": {k: {"type": "string"} for k in ("object", "value", "unit")},
    "identity_fields": ["object"],
    "measurement_groups": {"reading": {"value_field": "value", "unit_field": "unit"}},
}}}
TABLE = "| Sample | Value/m |\n| --- | --- |\n| A | 3 |\n"


def record(table):
    def source(quote):
        entry = next(e for e in table["entries"] if e["quote"] == quote)
        return {"ref": entry["ref"], "quote": quote, "offset": None}
    return {"record_id": "same-id", "record_type": "reading", "scope": "individual", "scope_sources": [],
            "anchors": [source("3")], "fields": {
                "object": {"value": "A", "sources": [source("A")], "relation": "row"},
                "value": {"value": "3", "sources": [source("3")], "relation": "anchor"},
                "unit": {"value": "m", "sources": [source("Value/m")], "relation": "column"},
            }}


class Backend:
    name, mode, output_protocol = "synthetic-unified", "replay", "flat-v1"

    def __init__(self, transform=None, finish_reason="stop"):
        self.requests = []
        self.outputs = []
        self.transform = transform
        self.finish_reason = finish_reason

    def generate(self, request):
        self.requests.append(request)
        payload = json.loads(request.messages[-1]["content"])
        records = [record(payload["table"])] if "table" in payload else []
        output = self.transform(records) if self.transform and records else {"records": records}
        self.outputs.append(deepcopy(output))
        return BackendResponse(text=output if isinstance(output, str) else json.dumps(output),
                               finish_reason=self.finish_reason, model="synthetic")


def test_public_default_mixed_document_preserves_table_records_and_global_sources():
    source = "# Synthetic study\n\nProse before.\n\n" + TABLE + "\nBetween tables.\n\n" + TABLE + "\nProse after."
    backend = Backend()
    result = extract(source, TASK, backend=backend)
    assert result.schema_version == "0.6"
    assert result.format_status == "complete"
    assert len(result.table_results) == 2
    assert not result.candidates
    assert result.run.candidate_count == 2
    assert result.run.response_count == len(backend.requests)
    table_requests = [r for r in backend.requests if r.chunk_id.startswith("table-")]
    assert len(table_requests) == 2
    for table, request in zip(result.table_results, table_requests):
        data = json.loads(request.messages[-1]["content"])["table"]
        raw = json.loads(table.response.text)["records"][0]
        assert {key: table.candidates[0][key] for key in raw} == raw
        assert table.candidates[0]["record_id"] == "same-id"
        assert table.evidence["table_input"] == data
        for entry in data["entries"]:
            assert source[entry["start"]:entry["end"]] == entry["quote"]
        assert data["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
        assert table.semantic_verification == "not_run"
    for request in backend.requests:
        if not request.chunk_id.startswith("table-"):
            assert "| A | 3 |" not in request.messages[-1]["content"]
    assert ExtractionResult.model_validate_json(result.model_dump_json()) == result
    report = render_markdown_report(result)
    assert "表格记录：2" in report and "局部位置规则" in report and "未验证该声明" in report
    assert "没有生成候选" not in report


def test_one_invalid_record_is_isolated_without_mutating_valid_unknown_fields(tmp_path):
    def output(records):
        records[0]["fields"]["extra"] = {"value": "extra", "sources": [], "relation": "context"}
        return {"records": [records[0], {"record_id": "bad", "fields": {}}]}
    trace = RunTrace(tmp_path / "trace")
    result = extract(TABLE, TASK, backend=Backend(output), trace=trace, backend_identity="synthetic")
    table = result.table_results[0]
    assert result.format_status == table.status == "partial_format_error"
    assert table.rejections[0]["index"] == 1
    assert table.rejections[0]["raw"] == {"record_id": "bad", "fields": {}}
    assert table.candidates[0]["values"]["extra"] == "extra"
    assert any(i["code"] == "unknown_field" for i in table.issues)
    assert (tmp_path / "trace/tables/table-0001/parsed.json").is_file()
    assert (tmp_path / "trace/tables/table-0001/results.json").is_file()


def test_duplicate_ids_are_all_isolated_and_all_bad_is_not_empty_success():
    backend = Backend(lambda records: {"records": records + deepcopy(records)})
    result = extract(TABLE, TASK, backend=backend)
    assert result.format_status == "format_error"
    assert result.table_results[0].status == "format_error"
    assert not result.table_results[0].candidates
    assert [x["index"] for x in result.table_results[0].rejections] == [0, 1]


@pytest.mark.parametrize("text", ['{"records":[],"records":[]}', '{"records":[', 'not json'])
def test_whole_response_failure_does_not_salvage_or_retry(text, tmp_path):
    backend = Backend(lambda _: text)
    with pytest.raises(InvalidResponseError):
        extract(TABLE, TASK, backend=backend, format_retries=1, trace=RunTrace(tmp_path / "trace"),
                backend_identity="synthetic", execution=ExecutionOptions(max_model_calls=5))
    assert len(backend.requests) == 1
    assert (tmp_path / "trace/responses/table-0001.txt").read_text() == text
    retry = Backend()
    with pytest.raises(InvalidResponseError, match="will not resend"):
        extract(TABLE, TASK, backend=retry, format_retries=1, trace=RunTrace(tmp_path / "resume"),
                backend_identity="synthetic", resume_from=tmp_path / "trace",
                execution=ExecutionOptions(max_model_calls=5))
    assert retry.requests == []


def test_table_truncation_never_splits_even_on_resume(tmp_path):
    backend = Backend(finish_reason="length")
    with pytest.raises(InvalidResponseError):
        extract(TABLE, TASK, backend=backend, trace=RunTrace(tmp_path / "trace"), backend_identity="synthetic",
                execution=ExecutionOptions(min_chunk_chars=1, max_model_calls=9))
    assert len(backend.requests) == 1
    state = json.loads((tmp_path / "trace/execution/checkpoint.json").read_text())["state"]
    assert list(state["nodes"]) == ["table-0001"]
    retry = Backend()
    with pytest.raises(InvalidResponseError, match="will not resend"):
        extract(TABLE, TASK, backend=retry, trace=RunTrace(tmp_path / "resume"), backend_identity="synthetic",
                execution=ExecutionOptions(min_chunk_chars=1, max_model_calls=9), resume_from=tmp_path / "trace")
    assert not retry.requests


def test_shared_budget_resume_reuses_first_table_and_original_files_unchanged(tmp_path):
    text = TABLE + "\n" + TABLE
    first = Backend()
    with pytest.raises(BackendError, match="budget exhausted"):
        extract(text, TASK, backend=first, trace=RunTrace(tmp_path / "first"), backend_identity="synthetic",
                execution=ExecutionOptions(max_model_calls=1))
    original = {p: p.read_bytes() for p in (tmp_path / "first").rglob("*") if p.is_file()}
    second = Backend()
    result = extract(text, TASK, backend=second, trace=RunTrace(tmp_path / "second"), backend_identity="synthetic",
                     execution=ExecutionOptions(max_model_calls=2), resume_from=tmp_path / "first")
    assert len(second.requests) == 1 and second.requests[0].chunk_id == "table-0002"
    assert len(result.table_results) == 2 and result.run.response_count == 2
    assert all(p.read_bytes() == data for p, data in original.items())
    third = Backend()
    replay = extract(text, TASK, backend=third, trace=RunTrace(tmp_path / "third"), backend_identity="synthetic",
                     execution=ExecutionOptions(max_model_calls=2), resume_from=tmp_path / "second")
    assert not third.requests
    assert [t.model_dump() for t in replay.table_results] == [t.model_dump() for t in result.table_results]


def test_pure_prose_keeps_old_schema_and_explicit_text_mode_uses_prose_protocol():
    for text, kwargs in [("A length is 3 m.", {}), (TABLE, {"table_mode": "text"})]:
        backend = Backend()
        result = extract(text, TASK, backend=backend, **kwargs)
        assert result.schema_version == "0.4"
        assert "table_results" not in result.model_dump()
        assert all("table" not in json.loads(r.messages[-1]["content"]) for r in backend.requests)


def test_table_result_cannot_be_silently_accepted_by_prose_archive():
    document = Document.from_text(TABLE)
    result = extract(document, TASK, backend=Backend())
    with pytest.raises(ValueError, match="observe-only"):
        create_archive(document, result)


def test_generation_controls_are_honored_by_table_request():
    backend = Backend()
    extract(TABLE, TASK, backend=backend, generation=GenerationOptions(temperature=0.2, max_output_tokens=512))
    assert backend.requests[0].generation.max_output_tokens == 512
    assert backend.requests[0].generation.temperature == 0.2


@pytest.mark.parametrize("argument", ["max_chunk_chars", "table_rows_per_chunk"])
@pytest.mark.parametrize("value", [0, -1, True, 1.2])
def test_invalid_limits_fail_before_a_table_call(argument, value):
    backend = Backend()
    with pytest.raises(ConfigurationError, match="positive integers"):
        extract(TABLE, TASK, backend=backend, **{argument: value})
    assert not backend.requests


def test_partial_trace_is_not_marked_succeeded(tmp_path):
    backend = Backend(lambda records: {"records": records + [{}]})
    trace = RunTrace(tmp_path / "partial")
    result = extract(TABLE, TASK, backend=backend, trace=trace, backend_identity="synthetic")
    trace.finish(result.model_dump(mode="json"))
    manifest = json.loads((tmp_path / "partial/manifest.json").read_text())
    assert manifest["status"] == "partial_format_error"


def test_prose_decision_bundle_rejects_table_result_explicitly(tmp_path):
    from attestextract.decision_bundle import prepare_decision_bundle
    trace = RunTrace(tmp_path / "trace")
    result = extract(TABLE, TASK, backend=Backend(), trace=trace, backend_identity="synthetic")
    trace.finish(result.model_dump(mode="json"))
    with pytest.raises(ValueError, match="table results are not supported"):
        prepare_decision_bundle(tmp_path / "trace")
