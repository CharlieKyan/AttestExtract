import json

import httpx
import pytest

from attestextract import ExecutionOptions, extract
from attestextract.errors import BackendError
from attestextract.models import BackendResponse
from attestextract.reporting import render_markdown_report


SOURCE = "| Site | Mass/kg | Code |\n| --- | --- | --- |\n| East | 12 | 135 |\n| West | 13 | 501 |\n\nEnd."


class Reader:
    name, mode, output_protocol = "synthetic-coverage", "replay", "flat-v1"

    def __init__(self, *, primary=("12",), reread=("13",)):
        self.primary = primary
        self.reread = reread
        self.calls = []

    def generate(self, request):
        self.calls.append(request.chunk_id)
        payload = json.loads(request.messages[-1]["content"])
        records = []
        if table := payload.get("table"):
            values = self.reread if "coverage_targets" in table else self.primary
            if values is None:
                raise BackendError("unknown delivery", code="timeout", attempts=1)
            for value in values:
                raw = next(entry for entry in table["entries"] if entry["quote"] == value)
                loc = {"ref": raw["ref"], "quote": value}
                records.append({"record_id": value, "record_type": "mass", "scope": "individual",
                                "anchors": [loc], "scope_sources": [], "fields": {
                                    "entity": {"value": None, "sources": []},
                                    "value": {"value": value, "sources": [loc], "relation": "anchor"},
                                    "unit": {"value": None, "sources": []},
                                    "conditions": {"value": None, "sources": []}}})
        return BackendResponse(text=json.dumps({"records": records}), model="synthetic")


def run(reader, *, source=SOURCE, budget=4, **kwargs):
    return extract(source, prompt_description="Measure the mass at each named site.",
                   field_list={"mass": "Mass reported for this site"}, backend=reader,
                   backend_identity="synthetic-coverage", execution=ExecutionOptions(max_model_calls=budget),
                   progress="quiet", **kwargs)


def test_only_unreported_values_reread_after_all_primary_blocks_and_cached(tmp_path):
    reader = Reader(reread=("12", "13"))
    result = run(reader, output_dir=tmp_path / "first")
    first, reread = result.table_results
    assert reader.calls == ["table-0001", "prose-0001", "table-0001.coverage"]
    target = next(e for e in first.evidence["table_input"]["entries"] if e["quote"] == "13")
    header = next(e for e in first.evidence["table_input"]["entries"] if e["quote"] == "Mass/kg")
    assert first.evidence["coverage_reread"]["targets"] == {"mass": {target["ref"]: header["ref"]}}
    assert first.evidence["coverage_reread"]["remaining"] == {}
    assert [r["record_id"] for t in result.table_results for r in t.candidates] == ["12", "13"]
    assert result.format_status == "complete" and not reread.rejections
    assert first.evidence["coverage_reread"]["ignored_repeats"] == [{"index": 0, "first_index": 0}]
    assert first.evidence["coverage_reread"]["ignored_repeat_count"] == 1
    assert json.loads(reread.response.text)["records"][0]["record_id"] == "12"
    assert SOURCE[target["start"]:target["end"]] == "13"
    replay = Reader()
    resumed = run(replay, resume_from=tmp_path / "first", output_dir=tmp_path / "second")
    assert not replay.calls and resumed.table_results == result.table_results
    assert resumed.run.request_attempts == 3


def test_changed_repeat_and_foreign_cell_stay_quarantined():
    class ConflictingReader(Reader):
        def generate(self, request):
            response = super().generate(request)
            if request.chunk_id.endswith(".coverage"):
                raw = json.loads(response.text)
                raw["records"][0]["fields"]["value"]["value"] = "120"
                response = response.model_copy(update={"text": json.dumps(raw)})
            return response
    result = run(ConflictingReader(reread=("12", "135", "13")))
    first, second = result.table_results
    assert result.format_status == "partial_format_error"
    assert first.evidence["coverage_reread"]["ignored_repeat_count"] == 0
    assert [r["raw"]["record_id"] for r in second.rejections] == ["12", "135"]
    assert [r["record_id"] for r in second.candidates] == ["13"]


def test_budget_preserves_first_pass_and_empty_reread_is_not_retried():
    reader = Reader(primary=(), reread=())
    limited = run(reader, budget=2)
    assert reader.calls == ["table-0001", "prose-0001"]
    assert limited.table_results[0].evidence["coverage_reread"]["status"] == "budget_exhausted"
    assert r"budget\_exhausted" in render_markdown_report(limited)
    reader.calls.clear()
    result = run(reader)
    check = result.table_results[0].evidence["coverage_reread"]
    assert check["status"] == "unresolved" and len(check["remaining"]["mass"]) == 2
    assert len(reader.calls) == 3


def test_transposed_table_and_irrelevant_columns():
    reader = Reader(primary=(), reread=("12", "13"))
    r = run(reader, source="| Site | East | West |\n|---|---|---|\n| Mass/kg | 12 | 13 |\n| Code | 151 | 173 |")
    assert len(reader.calls) == 2 and r.table_results[0].evidence["coverage_reread"]["remaining"] == {}
    reader.calls.clear()
    only_code = run(reader, source="| Site | Code |\n|---|---|\n| East | 50 |")
    assert len(reader.calls) == 1 and "coverage_reread" not in only_code.table_results[0].evidence


def test_interrupted_reread_preserves_reservation_on_resume(tmp_path):
    reader = Reader(reread=None)
    with pytest.raises(BackendError, match="unknown delivery"):
        run(reader, output_dir=tmp_path / "first")
    next_reader = Reader()
    result = run(next_reader, resume_from=tmp_path / "first", output_dir=tmp_path / "resume")
    assert next_reader.calls == []
    assert result.table_results[0].evidence["coverage_reread"]["status"] == "timeout"
    assert result.run.request_attempts == 3


def test_resuming_frozen_verification_does_not_add_new_observations(tmp_path):
    reader = Reader(primary=())
    first = run(reader, table_subject="Named sites", budget=2, output_dir=tmp_path / "first")
    assert first.table_results[0].evidence["coverage_reread"]["status"] == "budget_exhausted"
    assert (tmp_path / "first/diagnostics/analysis/checkpoint.json").is_file()
    reader.calls.clear()
    result = run(reader, table_subject="Named sites", resume_from=tmp_path / "first",
                 output_dir=tmp_path / "resume")
    assert not reader.calls
    assert result.table_results[0].evidence["coverage_reread"]["status"] == "retained_for_verification_resume"


@pytest.mark.parametrize("finish", ["stop", "length"])
def test_automatic_route_receipt_counts_completed_and_truncated_rereads(tmp_path, monkeypatch, finish):
    monkeypatch.setenv("ATTESTEXTRACT_MODEL_CACHE_DIR", str(tmp_path / "model-cache"))
    original = httpx.Client
    calls = []

    def dispatch(request):
        if request.method == "GET":
            return httpx.Response(401)
        content = json.loads(request.content)
        table = json.loads(content["messages"][-1]["content"])["table"]
        calls.append("coverage_targets" in table)
        return httpx.Response(200, json={"model": "synthetic",
            "choices": [{"message": {"content": '{"records":[]}'}, "finish_reason": finish if calls[-1] else "stop"}],
            "usage": {"total_tokens": 10}})

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(dispatch)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    def execute(out, resume=None):
        return extract(SOURCE.split("\n\n")[0], prompt_description="Mass of samples",
                       field_list={"mass": "Reported mass"}, model="coverage-test",
                       base_url="https://synthetic.invalid/v1", api_key="offline-placeholder-token",
                       output_dir=out, resume_from=resume, progress="quiet",
                       execution=ExecutionOptions(max_model_calls=2))
    result = execute(tmp_path / "first")
    assert calls == [False, True]
    status = "unresolved" if finish == "stop" else "truncated_response"
    assert result.table_results[0].evidence["coverage_reread"]["status"] == status
    assert result.run.usage["total_tokens"] == 20
    calls.clear()
    resumed = execute(tmp_path / "resume", tmp_path / "first")
    assert not calls and resumed.table_results == result.table_results
