"""Automatic v4 ledger and provenance across synthetic table truncation recovery."""

from copy import deepcopy
import json

import httpx
import pytest

from attestextract import ExecutionOptions, extract
from attestextract.automatic_execution import AutomaticState, validate_automatic_state
from attestextract.errors import BackendError


SOURCE = ("<table><tr><th>Sample</th><th>Value</th></tr>"
          "<tr><td>A</td><td>11</td></tr><tr><td>B</td><td>22</td></tr>"
          "<tr><td>C</td><td>33</td></tr><tr><td>D</td><td>44</td></tr></table>")
TASK = {"name": "synthetic readings", "record_types": {"reading": {
    "fields": {"value": {"type": "string", "required": True}}}}}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def state(output):
    saved = read(output / "diagnostics/execution/checkpoint.json")["state"]
    validated = AutomaticState.model_validate(saved)
    validate_automatic_state(validated)
    return saved


def records(table, *, include_foreign=False):
    owned = set(table["window"]["owned_cell_refs"])
    result = []
    for entry in table["entries"]:
        if entry["kind"] != "cell" or entry["quote"] not in {"11", "22", "33", "44"}:
            continue
        if not include_foreign and entry["ref"] not in owned:
            continue
        selection = {"ref": entry["ref"], "quote": entry["quote"]}
        result.append({"record_id": entry["ref"], "record_type": "reading",
                       "fields": {"value": {"value": entry["quote"], "sources": [selection], "relation": "anchor"}},
                       "anchors": [selection], "scope": "individual", "scope_sources": []})
    return result


@pytest.fixture
def http(monkeypatch, tmp_path):
    monkeypatch.setenv("ATTESTEXTRACT_MODEL_CACHE_DIR", str(tmp_path / "model-cache"))
    original = httpx.Client
    calls, controls = [], {"foreign": False}

    def dispatch(request):
        if request.method == "GET":
            assert "authorization" not in request.headers
            return httpx.Response(401)
        body = json.loads(request.content)
        assert body["model"] == "unlisted-research-model"
        assert request.headers["authorization"] == "Bearer synthetic-secret"
        table = json.loads(body["messages"][-1]["content"])["table"]
        calls.append(deepcopy(table))
        content = (json.dumps({"records": records(table, include_foreign=controls["foreign"])})
                   if table.get("window") else '{"records":[')
        return httpx.Response(200, json={"model": "reported-research-revision",
            "choices": [{"message": {"content": content}, "finish_reason": "stop" if table.get("window") else "length"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17}})

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(dispatch)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    return calls, controls


def run(output, *, budget=3, resume=None):
    return extract(SOURCE, TASK, base_url="https://unregistered-table.invalid/v1",
                   model="unlisted-research-model", api_key="synthetic-secret",
                   output_dir=output, resume_from=resume,
                   execution=ExecutionOptions(max_model_calls=budget, adaptive_chunking=False))


def test_automatic_known_length_keeps_parent_attempt_and_child_provenance(http, tmp_path):
    calls, _ = http
    output = tmp_path / "complete"
    result = run(output)
    saved = state(output)
    assert result.format_status == "complete"
    assert len(calls) == saved["calls_used"] == saved["response_count"] == 3
    assert "window" not in calls[0]
    assert [item["window"]["chunk_id"] for item in calls[1:]] == ["table-0001.1", "table-0001.2"]
    assert saved["checkpoint_version"] == "4"
    assert saved["nodes"]["table-0001"]["status"] == "split"
    attempts = saved["generation_attempts"]
    assert [a["status"] for a in attempts] == ["failed", "completed", "completed"]
    assert attempts[0]["error_code"] == "truncated_response"
    assert attempts[0]["response_received"] and not attempts[0]["outcome_unknown"]
    assert set(saved["chunk_provenance"]) == {"table-0001.1", "table-0001.2"}
    assert {a["route_id"] for a in attempts} == {"primary-01"}
    for child in ("table-0001.1", "table-0001.2"):
        assert saved["chunk_routes"][child] == "primary-01"
        assert saved["chunk_provenance"][child]["attempt_id"] == f"{child}.primary-01.attempt-1"
        assert saved["chunk_provenance"][child]["reported_model"] == "reported-research-revision"
    assert sorted(c["values"]["value"] for t in result.table_results for c in t.candidates) == ["11", "22", "33", "44"]
    assert not (output / "diagnostics/execution/cache/table-0001.json").exists()
    assert (output / "diagnostics/responses/table-0001.primary-01.attempt-1.txt").read_text() == '{"records":['
    before = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    calls.clear()
    replay = run(tmp_path / "replay", resume=output)
    assert not calls and replay.format_status == "complete"
    assert state(tmp_path / "replay")["calls_used"] == 3
    assert before == {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()}


@pytest.mark.parametrize("budget", [1, 2])
def test_automatic_budget_stop_reuses_completed_windows_without_replaying_parent(http, tmp_path, budget):
    calls, _ = http
    first = tmp_path / "first"
    with pytest.raises(BackendError) as error:
        run(first, budget=budget)
    assert error.value.code == "call_budget_exhausted"
    saved = state(first)
    assert len(calls) == saved["calls_used"] == budget
    assert saved["nodes"]["table-0001"]["status"] == "split"
    assert len(saved["chunk_provenance"]) == budget - 1
    calls.clear()
    result = run(tmp_path / "continued", resume=first)
    assert result.format_status == "complete" and len(calls) == 3 - budget
    assert all("window" in table for table in calls)
    assert state(tmp_path / "continued")["calls_used"] == 3


def test_automatic_foreign_anchors_are_visible_rejections_not_duplicate_candidates(http, tmp_path):
    calls, controls = http
    controls["foreign"] = True
    output = tmp_path / "rejected"
    result = run(output)
    assert len(calls) == 3 and result.format_status == "partial_format_error"
    assert sorted(c["values"]["value"] for t in result.table_results for c in t.candidates) == ["11", "22", "33", "44"]
    assert sum(len(t.rejections) for t in result.table_results) == 4
    assert all(r["errors"][0]["type"] == "anchor_outside_window"
               for table in result.table_results for r in table.rejections)
    assert state(output)["calls_used"] == 3
    calls.clear()
    replay = run(tmp_path / "replay", resume=output)
    assert not calls and replay.format_status == "partial_format_error"
    assert sum(len(t.rejections) for t in replay.table_results) == 4
