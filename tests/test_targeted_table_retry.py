"""Synthetic complete responses can get one explicit table retry, not a new budget."""
import hashlib
import json

import httpx
import pytest

import attestextract
from attestextract import ExecutionOptions, GenerationOptions, RunTrace, extract
from attestextract.backends import OpenAICompatibleBackend
from attestextract.errors import BackendError, ConfigurationError
from attestextract.execution import prepare_execution
from attestextract.models import TaskSpec
from attestextract import document_routing, table_routes


TEXT = "Sample B length is 5 m.\n\n| Sample | Value/m |\n| --- | --- |\n| A | 3 |\n\nAnother table.\n\n| Sample | Value/m |\n| --- | --- |\n| A | 3 |\n"
TASK = {"name": "Synthetic lengths", "record_types": {"reading": {
    "fields": {key: {"type": "string"} for key in ("object", "value", "unit")}}}}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def state(path):
    return read(path / "diagnostics/execution/checkpoint.json")["state"]


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def table_record(table):
    def selection(quote):
        cell = next(entry for entry in table["entries"] if entry["quote"] == quote)
        return {"ref": cell["ref"], "quote": quote}
    return {"record_id": "one", "record_type": "reading", "scope": "individual", "scope_sources": [],
            "anchors": [selection("3")], "fields": {
                "object": {"value": "A", "sources": [selection("A")], "relation": "row"},
                "value": {"value": "3", "sources": [selection("3")], "relation": "anchor"},
                "unit": {"value": "m", "sources": [selection("Value/m")], "relation": "column"}}}


def backend(calls, *, trace=None, bad=True, timeout=False, timeout_at=None, model="synthetic", max_attempts=1):
    def endpoint(request):
        payload = json.loads(request.content)
        message = json.loads(payload["messages"][-1]["content"])
        table = message.get("table")
        calls.append(table["table_id"] if table else "prose")
        if timeout or len(calls) == timeout_at:
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if table:
            record = table_record(table)
            if bad:
                for field in record["fields"].values():
                    field.pop("relation")
            result = {"records": [record]}
        elif "Sample B" in message["source"]:
            result = {"records": [{"record_type": "reading", "values": {"object": "B", "value": "5", "unit": "m"},
                      "quotes": {key: "Sample B length is 5 m." for key in ("object", "value", "unit")}}]}
        else:
            result = {"records": []}
        return httpx.Response(200, json={"model": model, "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(result)}}], "usage": {"total_tokens": 20}})
    return OpenAICompatibleBackend("https://synthetic.invalid/v1", model, api_key="synthetic-test-key",
        trace=trace, structured_output="json_object", max_attempts=max_attempts, transport=httpx.MockTransport(endpoint))


def run(path, calls, *, resume=None, retry=False, budget=10, **backend_options):
    trace = RunTrace(path / "diagnostics")
    b = backend(calls, trace=trace, **backend_options)
    result = extract(TEXT, TASK, backend=b, source_name="synthetic.md", trace=trace,
        execution=ExecutionOptions(max_model_calls=budget), resume_from=resume, retry_tables=retry)
    trace.finish(result.model_dump(mode="json"))
    return result


def old_run(monkeypatch, path):
    with monkeypatch.context() as patch:
        patch.setattr(attestextract, "__version__", "0.1.0.dev30")
        patch.setattr(document_routing, "ROUTING_REVISION", "dev29-table-a-2")
        patch.setattr(table_routes, "build_route_request", lambda stage, task, table, **kwargs:
                      table_routes._build_legacy_route_request(task, table, **kwargs))
        calls = []
        result = run(path, calls)
    assert result.format_status == "partial_format_error"
    return calls


def test_dev30_retry_only_two_tables_preserves_prose_budget_and_history(tmp_path, monkeypatch):
    old = tmp_path / "old"
    initial = old_run(monkeypatch, old)
    before = hashes(old)
    calls = []
    result = run(tmp_path / "new", calls, resume=old, retry=True, budget=len(initial) + 2, bad=False)
    assert calls == ["table-0001", "table-0002"]
    assert result.format_status == "complete"
    assert len(result.candidates) == 1 and len(result.table_results) == 2
    saved = state(tmp_path / "new")
    assert saved["calls_used"] == len(initial) + 2
    assert saved["migrations"][0]["reason"] == "explicit_table_protocol_retry"
    assert hashes(old) == before
    for key in calls:
        assert saved["nodes"][key]["table_retries_reserved"] == 1
        assert (tmp_path / f"new/diagnostics/execution/table-original/{key}.json").is_file()
    replay_calls = []
    replay = run(tmp_path / "replay", replay_calls, resume=tmp_path / "new", budget=len(initial) + 2)
    assert not replay_calls and replay.format_status == "complete"


def test_offline_plan_keeps_used_budget_and_two_pending(tmp_path, monkeypatch):
    old = tmp_path / "old"
    initial = old_run(monkeypatch, old)
    calls = []
    session = prepare_execution(TEXT, TaskSpec.model_validate(TASK), backend(calls), GenerationOptions(),
        ExecutionOptions(max_model_calls=len(initial)+2), source_name="synthetic.md", resume_from=old,
        trace=RunTrace(tmp_path / "preview/diagnostics"), table_mode="auto", retry_tables=True)
    assert not calls
    assert session.summary()["calls_reserved"] == len(initial)
    assert session.summary()["pending_chunks"] == 2
    assert session.summary()["completed_chunks"] == len(initial)-2
    assert session.summary()["table_retry_chunks"] == ["table-0001", "table-0002"]
    resumed = []
    run(tmp_path / "actual", resumed, resume=tmp_path / "preview", retry=True, budget=len(initial)+2, bad=False)
    assert resumed == ["table-0001", "table-0002"]


def test_timeout_reserves_once_and_cannot_be_reset(tmp_path):
    initial = []
    run(tmp_path / "old", initial)
    calls = []
    with pytest.raises(BackendError):
        run(tmp_path / "failed", calls, resume=tmp_path / "old", retry=True, budget=10, timeout=True)
    assert calls == ["table-0001"]
    saved = state(tmp_path / "failed")
    assert saved["calls_used"] == len(initial)+1
    assert saved["nodes"]["table-0001"]["table_retries_reserved"] == 1
    later = []
    with pytest.raises(ConfigurationError, match="outcome-unknown"):
        run(tmp_path / "again", later, resume=tmp_path / "failed", retry=True, budget=20, bad=False)
    assert later == []


def test_preview_without_retry_flag_remains_recoverable(tmp_path, monkeypatch):
    old = tmp_path / "old"
    initial = old_run(monkeypatch, old)
    prepare_execution(TEXT, TaskSpec.model_validate(TASK), backend([]), GenerationOptions(),
        ExecutionOptions(max_model_calls=len(initial)+2), source_name="synthetic.md", resume_from=old,
        trace=RunTrace(tmp_path / "preview/diagnostics"), table_mode="auto", retry_tables=True)
    before = hashes(tmp_path / "preview")
    calls = []
    with pytest.raises(ConfigurationError, match="planned table retries"):
        run(tmp_path / "missing-flag", calls, resume=tmp_path / "preview", budget=len(initial)+2)
    assert not calls
    assert hashes(tmp_path / "preview") == before
    saved = state(tmp_path / "missing-flag")
    assert saved["calls_used"] == len(initial)
    for key in ("table-0001", "table-0002"):
        assert saved["nodes"][key]["status"] == "pending"
        assert saved["nodes"][key]["table_retry_pending"]
        assert saved["nodes"][key]["table_retries_reserved"] == 0
    run(tmp_path / "actual", calls, resume=tmp_path / "missing-flag", retry=True,
        budget=len(initial)+2, bad=False)
    assert calls == ["table-0001", "table-0002"]


def test_table_retry_cannot_also_generate_unfinished_prose(tmp_path):
    initial = []
    with pytest.raises(BackendError):
        run(tmp_path / "old", initial, timeout_at=3)
    assert initial == ["prose", "table-0001", "prose"]
    calls = []
    with pytest.raises(ConfigurationError, match="completed caches for all other chunks"):
        run(tmp_path / "retry", calls, resume=tmp_path / "old", retry=True)
    assert not calls


def test_second_rejection_is_not_renewed(tmp_path):
    run(tmp_path / "old", [])
    second = run(tmp_path / "second", [], resume=tmp_path / "old", retry=True)
    assert second.format_status == "partial_format_error"
    calls = []
    with pytest.raises(ConfigurationError, match="unused retry allowance"):
        run(tmp_path / "third", calls, resume=tmp_path / "second", retry=True, budget=100)
    assert not calls


@pytest.mark.parametrize("damage", ["cache", "request", "source", "model", "manifest"])
def test_old_migration_rejects_damage_and_identity_change_before_calls(tmp_path, monkeypatch, damage):
    old_run(monkeypatch, tmp_path / "old")
    if damage in {"cache", "request"}:
        relative = "execution/cache/table-0001.json" if damage == "cache" else "requests/table-0001.request.json"
        (tmp_path / "old/diagnostics" / relative).write_text("{}")
    if damage == "source":
        document = tmp_path / "old/diagnostics/document.json"
        data = read(document)
        data["text"] += "Changed"
        document.write_text(json.dumps(data))
    if damage == "manifest":
        (tmp_path / "old/diagnostics/manifest.json").write_text("[]")
    calls = []
    with pytest.raises(ConfigurationError):
        run(tmp_path / "new", calls, resume=tmp_path / "old", retry=True,
            **({"model": "different"} if damage == "model" else {}))
    assert not calls


def test_explicit_budget_and_one_http_attempt_required(tmp_path):
    run(tmp_path / "old", [])
    for index, kwargs in enumerate([{"budget": None}, {"budget": 1}, {"max_attempts": 2}]):
        with pytest.raises(ConfigurationError):
            run(tmp_path / f"check-{index}", [], resume=tmp_path / "old", retry=True, **kwargs)


def test_old_table_run_needs_explicit_migration_flag(tmp_path, monkeypatch):
    old_run(monkeypatch, tmp_path / "old")
    calls = []
    with pytest.raises(ConfigurationError, match="identity mismatch"):
        run(tmp_path / "new", calls, resume=tmp_path / "old", bad=False)
    assert not calls
