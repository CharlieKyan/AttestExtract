"""Recover known table truncation without replaying a parent or completed prose.

The frozen fixture was produced by unmodified dev32, using synthetic HTTP only.
It is deliberately not a checkpoint produced by the implementation under test.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import httpx
import pytest

from attestextract import ExecutionOptions, GenerationOptions, RunTrace, extract
from attestextract.backends import OpenAICompatibleBackend
from attestextract.errors import BackendError, ConfigurationError, InvalidResponseError
from attestextract.execution import digest


FIXTURE = Path(__file__).parent / "fixtures/dev32_table_truncated.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def saved_state(path):
    return read(path / "diagnostics/execution/checkpoint.json")["state"]


def hashes(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob("*") if p.is_file()}


@pytest.fixture
def old_run(tmp_path):
    frozen = read(FIXTURE)
    assert frozen["provenance"]["source_commit"] == "47f6b1b9fefe166a9f22b5e145b54bc440c2d1bb"
    root = tmp_path / "dev32/diagnostics"
    for relative, content in frozen["files"].items():
        assert hashlib.sha256(content.encode()).hexdigest() == frozen["provenance"]["file_sha256"][relative]
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode())
    state = read(root / "execution/checkpoint.json")["state"]
    assert state["package_version"] == "0.1.0.dev32" and state["calls_used"] == 6
    assert sum(node["status"] == "completed" for node in state["nodes"].values()) == 5
    return root.parent


def table_records(table):
    cells = {entry["ref"]: entry for entry in table["entries"] if entry["kind"] == "cell"}
    owned = set(table.get("window", {}).get("owned_cell_refs", cells))
    records = []
    header = next(cell for cell in cells.values() if cell["quote"] == "Value/m")

    def selection(entry):
        return {"ref": entry["ref"], "quote": entry["quote"]}

    for anchor in cells.values():
        if anchor["ref"] not in owned or anchor["quote"] not in {"6", "7", "8", "9"}:
            continue
        subject = next(cell for cell in cells.values()
                       if cell["row"] == anchor["row"] and cell["column"] == 0)
        records.append({"record_id": anchor["ref"], "record_type": "reading",
            "scope": "individual", "scope_sources": [], "anchors": [selection(anchor)],
            "fields": {
                "object": {"value": subject["quote"], "sources": [selection(subject)], "relation": "row"},
                "value": {"value": anchor["quote"], "sources": [selection(anchor)], "relation": "anchor"},
                "unit": {"value": "m", "sources": [selection(header)], "relation": "column"},
            }})
    return records


def run(output, calls, *, resume=None, budget=20, text=None, model="synthetic", table_failure=None):
    frozen = read(FIXTURE)["files"]
    document = json.loads(frozen["document.json"])
    task = json.loads(frozen["task.json"])
    trace = RunTrace(output / "diagnostics")

    def endpoint(request):
        message = json.loads(json.loads(request.content)["messages"][-1]["content"])
        calls.append(deepcopy(message))
        table = message.get("table")
        finish = "stop"
        if table and table_failure == "timeout":
            raise httpx.ReadTimeout("synthetic unknown outcome", request=request)
        if table and not table.get("window") and table_failure == "length":
            content, finish = '{"records":[', "length"
        elif table:
            content = json.dumps({"records": table_records(table)})
        else:
            records = []
            for match in re.finditer(r"Sample ([A-Z]) length is (\d+) m\.", message["source"]):
                records.append({"record_type": "reading", "values": {
                    "object": match[1], "value": match[2], "unit": "m"},
                    "quotes": {key: match[0] for key in ("object", "value", "unit")}})
            content = json.dumps({"records": records})
        return httpx.Response(200, json={"model": model, "choices": [
            {"finish_reason": finish, "message": {"content": content}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})

    backend = OpenAICompatibleBackend("https://synthetic.invalid/v1", model, api_key="synthetic-key",
        trace=trace, structured_output="json_object", max_attempts=1,
        transport=httpx.MockTransport(endpoint))
    result = extract(document["text"] if text is None else text, task, backend=backend,
        source_name="synthetic.md", trace=trace, max_chunk_chars=24,
        generation=GenerationOptions(max_output_tokens=512),
        execution=ExecutionOptions(adaptive_chunking=False, min_chunk_chars=1,
                                   max_split_depth=3, max_model_calls=budget), resume_from=resume)
    trace.finish(result.model_dump(mode="json"))
    return result


def test_real_dev32_checkpoint_reuses_five_prose_chunks_then_splits_table(old_run, tmp_path):
    before = hashes(old_run)
    calls = []
    result = run(tmp_path / "new", calls, resume=old_run, budget=9)
    tables = [message["table"] for message in calls if "table" in message]
    assert len(calls) == 3 and len(tables) == 2
    assert {table["window"]["chunk_id"] for table in tables} == {"table-0001.1", "table-0001.2"}
    assert all("window" in table for table in tables)  # Never regenerate the failed whole table.
    assert [candidate.fields["value"].value for candidate in result.candidates] == ["1", "2", "3", "4", "5", "10"]
    assert sorted(candidate["values"]["value"] for table in result.table_results
                  for candidate in table.candidates) == ["6", "7", "8", "9"]
    state = saved_state(tmp_path / "new")
    assert state["calls_used"] == state["response_count"] == 9
    assert state["nodes"]["table-0001"]["calls"] == 1
    assert state["nodes"]["table-0001"]["status"] == "split"
    assert not state["nodes"]["table-0001"]["cache_hash"]
    assert state["migrations"]
    assert read(tmp_path / "new/diagnostics/execution/summary.json")["reused_chunks"] == 5
    assert hashes(old_run) == before
    later = []
    replay = run(tmp_path / "replay", later, resume=tmp_path / "new", budget=9)
    assert not later and replay.format_status == "complete"
    assert saved_state(tmp_path / "replay")["calls_used"] == 9


@pytest.mark.parametrize("damage", ["cache", "request", "schema", "document", "manifest", "ledger", "model", "source",
                                   "failure_reason", "missing_failure_metadata", "missing_failure_text",
                                   "document_type", "failure_metadata_type"])
def test_dev32_migration_rejects_damage_before_any_new_request(old_run, tmp_path, damage):
    directory = old_run / "diagnostics"
    if damage in {"cache", "request", "schema"}:
        paths = {"cache": "execution/cache/prose-0005.json", "request": "requests/table-0001.request.json",
                 "schema": "schema/table-0001.request.json"}
        (directory / paths[damage]).write_text("{}")
    elif damage in {"document", "manifest"}:
        (directory / f"{damage}.json").write_text("{}")
    elif damage == "ledger":
        path = directory / "execution/checkpoint.json"
        envelope = read(path)
        envelope["state"]["calls_used"] -= 1
        envelope["sha256"] = digest(envelope["state"])
        path.write_text(json.dumps(envelope))
    elif damage == "failure_reason":
        path = directory / "responses/table-0001.meta.json"
        metadata = read(path)
        metadata["finish_reason"] = "stop"
        path.write_text(json.dumps(metadata))
    elif damage in {"missing_failure_metadata", "missing_failure_text"}:
        suffix = "meta.json" if damage == "missing_failure_metadata" else "txt"
        (directory / f"responses/table-0001.{suffix}").unlink()
    elif damage in {"document_type", "failure_metadata_type"}:
        relative = "document.json" if damage == "document_type" else "responses/table-0001.meta.json"
        (directory / relative).write_text("[]")
    kwargs = {"model": "changed"} if damage == "model" else {"text": "Changed source."} if damage == "source" else {}
    calls = []
    with pytest.raises(ConfigurationError):
        run(tmp_path / "new", calls, resume=old_run, **kwargs)
    assert not calls


def test_budget_stop_between_windows_preserves_child_cache_and_parent_cost(old_run, tmp_path):
    calls = []
    with pytest.raises(BackendError) as error:
        run(tmp_path / "limited", calls, resume=old_run, budget=7)
    assert error.value.code == "call_budget_exhausted" and len(calls) == 1
    assert saved_state(tmp_path / "limited")["calls_used"] == 7
    before = hashes(tmp_path / "limited")
    more = []
    result = run(tmp_path / "continued", more, resume=tmp_path / "limited", budget=9)
    assert result.format_status == "complete" and len(more) == 2
    assert saved_state(tmp_path / "continued")["calls_used"] == 9
    assert hashes(tmp_path / "limited") == before


@pytest.mark.parametrize("damage", ["child_range", "children", "split_reason"])
def test_window_tree_tampering_is_rejected_before_calls(old_run, tmp_path, damage):
    with pytest.raises(BackendError):
        run(tmp_path / "limited", [], resume=old_run, budget=7)
    path = tmp_path / "limited/diagnostics/execution/checkpoint.json"
    envelope = read(path)
    nodes = envelope["state"]["nodes"]
    if damage == "child_range":
        nodes["table-0001.2"]["body_segments"][0]["start"] += 1
    elif damage == "children":
        nodes["table-0001"]["children"].reverse()
    else:
        nodes["table-0001"]["error_code"] = "transport_error"
    envelope["sha256"] = digest(envelope["state"])
    path.write_text(json.dumps(envelope))
    calls = []
    with pytest.raises(ConfigurationError):
        run(tmp_path / "rejected", calls, resume=tmp_path / "limited", budget=9)
    assert not calls


def test_new_run_known_length_splits_but_timeout_does_not(tmp_path):
    calls = []
    result = run(tmp_path / "length", calls, table_failure="length")
    assert result.format_status == "complete" and len(calls) == 9
    assert (tmp_path / "length/diagnostics/responses/table-0001.txt").read_text() == '{"records":['
    assert not (tmp_path / "length/diagnostics/execution/cache/table-0001.json").exists()
    failed = []
    with pytest.raises(BackendError):
        run(tmp_path / "timeout", failed, table_failure="timeout")
    state = saved_state(tmp_path / "timeout")
    assert len(failed) == state["calls_used"] == 6
    assert not state["nodes"]["table-0001"]["children"]
    resumed = []
    with pytest.raises(InvalidResponseError, match="will not resend"):
        run(tmp_path / "timeout-resume", resumed, resume=tmp_path / "timeout")
    assert not resumed


def test_automatic_table_windows_preserve_route_provenance_and_budget(monkeypatch, tmp_path):
    from attestextract.automatic_provenance import validate_automatic_provenance

    monkeypatch.setenv("ATTESTEXTRACT_MODEL_CACHE_DIR", str(tmp_path / "model-cache"))
    original_client = httpx.Client
    calls = []

    def dispatch(request):
        if request.method == "GET":
            return httpx.Response(401)
        table = json.loads(json.loads(request.content)["messages"][-1]["content"])["table"]
        calls.append(table)
        window = table.get("window")
        content = json.dumps({"records": table_records(table)}) if window else '{"records":['
        return httpx.Response(200, json={"model": "reported-synthetic", "choices": [{
            "message": {"content": content}, "finish_reason": "stop" if window else "length"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(dispatch)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    task = json.loads(read(FIXTURE)["files"]["task.json"])
    source = "| Sample | Value/m |\n| --- | --- |\n| F | 6 |\n| G | 7 |\n| H | 8 |\n| I | 9 |\n"

    def automatic(output, budget, resume=None):
        return extract(source, task, model="unregistered-synthetic", base_url="https://unregistered.invalid/v1",
            api_key="synthetic-key", trace=RunTrace(output / "diagnostics"), resume_from=resume,
            generation=GenerationOptions(max_output_tokens=512), execution=ExecutionOptions(max_model_calls=budget))

    with pytest.raises(BackendError) as error:
        automatic(tmp_path / "first", 2)
    assert error.value.code == "call_budget_exhausted" and len(calls) == 2
    before = hashes(tmp_path / "first")
    result = automatic(tmp_path / "second", 3, tmp_path / "first")
    assert len(calls) == 3 and result.format_status == "complete"
    assert len(result.run.generation_attempts) == 3
    assert len({attempt.route_id for attempt in result.run.generation_attempts}) == 1
    assert set(result.run.chunk_provenance) == {"table-0001.1", "table-0001.2"}
    validate_automatic_provenance(result)
    assert hashes(tmp_path / "first") == before
    assert saved_state(tmp_path / "second")["calls_used"] == 3


def test_cli_check_reports_saved_windows_and_reuse_without_network(monkeypatch, tmp_path, capsys):
    from attestextract import cli

    monkeypatch.setenv("ATTESTEXTRACT_API_KEY", "synthetic-key")
    source = tmp_path / "synthetic.md"
    source.write_text("Sample A length is 1 m.\n\n| Sample | Value/m |\n| --- | --- |\n| F | 6 |\n| G | 7 |\n")
    task = tmp_path / "task.json"
    task.write_text(read(FIXTURE)["files"]["task.json"])
    original_client = httpx.Client
    calls = []

    def dispatch(request):
        if request.method == "GET":
            return httpx.Response(401)
        payload = json.loads(json.loads(request.content)["messages"][-1]["content"])
        calls.append(payload)
        table = payload.get("table")
        return httpx.Response(200, json={"model": "synthetic", "choices": [{
            "message": {"content": '{"records":[' if table else '{"records":[]}'},
            "finish_reason": "length" if table else "stop"}]})

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(dispatch)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    args = ["extract", "--input", str(source), "--task", str(task),
            "--adapter", "openai-compatible", "--model", "synthetic",
            "--base-url", "https://synthetic.invalid/v1", "--structured-output", "json_object",
            "--max-output-tokens", "512", "--format-retries", "0", "--chunking", "fixed",
            "--max-chunk-chars", "24", "--progress", "quiet"]
    first = tmp_path / "first"
    first.mkdir()
    assert cli.main([*args, "--max-model-calls", "2", "--output", str(first / "result.json"),
                     "--log-dir", str(first / "diagnostics")]) == 1
    captured = capsys.readouterr()
    assert len(calls) == 2, captured.err
    before = hashes(first)

    def no_network(*args, **kwargs):
        raise AssertionError("CLI --check must not create an HTTP client")

    monkeypatch.setattr(httpx, "Client", no_network)
    preview = tmp_path / "preview"
    preview.mkdir()
    assert cli.main([*args, "--check", "--resume", str(first), "--max-model-calls", "4",
                     "--output", str(preview / "result.json"),
                     "--log-dir", str(preview / "diagnostics")]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["network_calls"] == 0 and len(calls) == 2
    assert plan["resume"] == {"requested": True, "reusable_chunks": 1,
                              "remaining_chunks": 2, "remaining_call_budget": 2}
    assert plan["table_routing"]["table_count"] == 1
    assert plan["table_routing"]["table_chunks"] == ["table-0001.1", "table-0001.2"]
    assert plan["execution"]["calls_reserved"] == 2
    assert not plan["coverage"]["uncovered"] and plan["coverage"]["repeated_body_characters"] == 0
    assert hashes(first) == before
