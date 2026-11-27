"""Analysis follows public automatic routes and preserves table-window warnings."""

import json

import httpx
import pytest

from niceextract import ExecutionOptions, extract


TASK = {"prompt_description": "Extract directly reported sample mass without changing units.",
        "field_list": {"mass": "Original mass and unit of each sample"}, "field_types": {"mass": {"kind": "numeric"}}}


def envelope(payload, *, finish="stop"):
    return httpx.Response(200, json={"model": "reported-synthetic-revision", "id": "synthetic-response",
        "choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})


def install_http(monkeypatch, tmp_path, handler):
    monkeypatch.setenv("NICEEXTRACT_MODEL_CACHE_DIR", str(tmp_path / "model-cache"))
    original = httpx.Client
    sent = []

    def dispatch(request):
        if request.method == "GET":
            assert "authorization" not in request.headers
            return httpx.Response(401)
        assert request.headers["authorization"] == "Bearer synthetic-analysis-secret"
        body = json.loads(request.content)
        assert body["model"] == "unregistered-science-model"
        sent.append(body)
        if body.get("response_format", {}).get("type") == "json_object" and not any(
            "json" in message["content"].casefold() for message in body["messages"]
        ):
            return httpx.Response(400, json={"error": {"type": "invalid_request_error",
                "message": "Prompt must contain the word 'json' in some form to use "
                           "'response_format' of type 'json_object'."}})
        return handler(body, json.loads(body["messages"][-1]["content"]))

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(dispatch)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    return sent


def run(text, directory, *, max_analysis_calls=2, **kwargs):
    return extract(text, TASK, base_url="https://unregistered-analysis.invalid/v1",
                   model="unregistered-science-model", api_key="synthetic-analysis-secret",
                   table_subject="an individual sample", max_analysis_calls=max_analysis_calls,
                   output_dir=directory, progress="quiet", **kwargs)


def read_initial_passage(payload):
    return {"action": "read", "question": "Check the complete source passage before assigning the owner.",
            "query": None, "search_offset": 0,
            "passage_ids": [payload["initial_context"]["passages"][0]["passage_id"]],
            "neighbors": 0, "objects": [], "relations": [], "bindings": []}


def quoted(payload, quote):
    passages = list(payload["initial_context"]["passages"])
    for frame in payload["lookup_history"]:
        passages.extend(frame["result"].get("passages", []))
    passage = next(item for item in passages if quote in item["text"])
    return {"passage_id": passage["passage_id"], "quote": quote, "occurrence": None}


def test_automatic_field_plan_negotiates_once_and_resumes_inside_total_budget(tmp_path, monkeypatch):
    def respond(body, payload):
        mode = body["response_format"]["type"]
        if mode == "json_schema":
            assert "fields" in payload
            return httpx.Response(400, json={"error": {"code": "unsupported_value", "param": "response_format",
                                                       "message": "json_schema is not supported"}})
        assert mode == "json_object"
        if "fields" in payload:
            return envelope({"field_types": {"mass": {"kind": "numeric"}}})
        assert payload["task"]["field_types"]["mass"]["source"] == "model"
        return envelope({"records": []})

    sent = install_http(monkeypatch, tmp_path, respond)
    options = dict(prompt_description=TASK["prompt_description"], field_list=TASK["field_list"],
                   model="unregistered-science-model", base_url="https://unregistered-analysis.invalid/v1",
                   api_key="synthetic-analysis-secret", execution=ExecutionOptions(max_model_calls=3),
                   progress="quiet")
    out = tmp_path / "original"
    extract("Sample A has mass 3 g.", output_dir=out, **options)
    assert len(sent) == 3
    for directory in (out, tmp_path / "resumed"):
        if directory != out:
            extract("Sample A has mass 3 g.", output_dir=directory, resume_from=out, **options)
        receipt = json.loads((directory/"diagnostics/field-preparation.json").read_bytes())
        assert [a["status"] for a in receipt["route_attempts"]] == ["failed", "returned"]
        assert receipt["calls_reserved"] == 2
        ledger = json.loads((directory/"diagnostics/execution/checkpoint.json").read_bytes())["state"]
        assert ledger["calls_used"] == ledger["max_model_calls"] == 1
    assert len(sent) == 3
    for entry in receipt["route_attempts"]:
        assert (out/f"diagnostics/requests/{entry['artifact']}.request.json").is_file()
    assert (out/"diagnostics/responses/task-field-types.route-2.txt").is_file()


def test_rejected_field_planning_prompt_cannot_resend_or_use_backup(tmp_path, monkeypatch):
    from niceextract.errors import BackendError

    def respond(body, payload):
        assert "fields" in payload
        return httpx.Response(400, json={"error": {"type": "invalid_request_error", "param": "response_format",
            "message": "Prompt must contain the word 'json' in some form to use 'response_format' of type 'json_object'."}})
    sent = install_http(monkeypatch, tmp_path, respond)
    out = tmp_path / "failed"
    with pytest.raises(BackendError) as exc:
        extract("Sample A has mass 3 g.", prompt_description=TASK["prompt_description"], field_list=TASK["field_list"],
                model="unregistered-science-model", base_url="https://unregistered-analysis.invalid/v1",
                model_options={"structured_output":"json_object"}, api_key="synthetic-analysis-secret",
                output_dir=out, progress="quiet")
    assert exc.value.code == "json_prompt_required" and len(sent) == 1
    receipt = json.loads((out/"diagnostics/field-preparation.json").read_bytes())
    assert receipt["calls_reserved"] == 1 and receipt["route_attempts"][0]["status"] == "failed"


def decide(objects, bindings):
    return {"action": "decide", "question": "The source supports the direct owners.",
            "query": None, "search_offset": 0, "passage_ids": [], "neighbors": 0,
            "objects": objects, "relations": [], "bindings": bindings}


@pytest.mark.parametrize("direct_object", [False, True], ids=["schema-to-object", "direct-object"])
def test_public_automatic_model_analysis_uses_successful_compatible_route_and_ledger(monkeypatch, tmp_path, direct_object):
    text = "Sample A has mass 3 g."
    assert "json" not in text.casefold() and "json" not in json.dumps(TASK).casefold()

    def respond(body, payload):
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": {"code": "unsupported_value", "param": "response_format",
                                                       "message": "json_schema is not supported"}})
        assert body["response_format"]["type"] == "json_object"
        if "source" in payload:
            return envelope({"records": [{"record_type": "mass", "values": {
                "entity": "A", "value": "3", "unit": "g", "conditions": None},
                "quotes": {"entity": text, "value": text, "unit": text, "conditions": ""}}]})
        if not payload["lookup_history"]:
            return envelope({"action": "search", "question": "Find the original sample statement.",
                             "query": "Sample A", "search_offset": 0, "passage_ids": [],
                             "neighbors": 0, "objects": [], "relations": [], "bindings": []})
        if payload["lookup_history"][-1]["action"] == "search":
            return envelope(read_initial_passage(payload))
        return envelope(decide(
            [{"object_id": "sample", "name": "A", "kind": "sample", "scope": "this experiment",
              "is_table_subject": True, "mentions": [quoted(payload, text)]}],
            [{"observation_id": payload["observations"][0]["observation_id"],
              "subject_id": "sample", "status": "supported", "evidence": [quoted(payload, text)],
              "reason": "Directly stated sample owner.", "target_match": "supported",
              "property_name": "mass", "statistic": None}]))

    sent = install_http(monkeypatch, tmp_path, respond)
    output = tmp_path / "run"
    options = {"model_options": {"structured_output": "json_object"}} if direct_object else {}
    result = run(text, output, max_analysis_calls=3, **options)
    assert result.schema_version == "0.5" and result.run.backend == "automatic"
    extraction_attempts = 1 if direct_object else 2
    assert [attempt.status for attempt in result.run.generation_attempts] == (
        ["completed"] if direct_object else ["failed", "completed"])
    route_id = result.run.chunk_provenance["chunk-0001"].route_id
    assert result.run.routes[route_id]["config"]["structured_output"] == "json_object"
    assert [body["response_format"]["type"] for body in sent] == (
        [] if direct_object else ["json_schema"]) + ["json_object"] * 4
    assert result.analysis["status"] == "complete"
    steps = [step for job in result.analysis["jobs"] for step in job["steps"]]
    assert {step["route_id"] for step in steps} == {route_id}
    assert all(job["status"] == "completed" and job["attempts"] == len(job["steps"])
               for job in result.analysis["jobs"])
    assert [step["action"] for step in steps] == ["search", "read", "decide"]
    assert all(step["status"] == "completed" and step["http_attempts"] == 1 for step in steps)
    assert all(step["reported_model"] == "reported-synthetic-revision" for step in steps)
    assert result.analysis["budget"]["reserved"] == 3
    assert result.analysis["budget"]["extraction_reserved"] == extraction_attempts
    assert result.analysis["budget"]["total_reserved"] == extraction_attempts + 3
    observation = result.analysis["observations"][0]
    assert observation["subject_id"] == result.analysis["objects"][0]["object_id"]
    assert observation["source_record"] == "/candidates/0" and observation["value"] == "3"
    # Resuming the successful route must neither renegotiate nor generate again.
    previous_calls = len(sent)
    old_files = {str(path.relative_to(output)): path.read_bytes()
                 for path in output.rglob("*") if path.is_file()}
    resumed = run(text, tmp_path / "resumed", max_analysis_calls=3, resume_from=output, **options)
    assert len(sent) == previous_calls and resumed.analysis["status"] == "complete"
    assert resumed.analysis["budget"]["reserved"] == 3
    assert resumed.analysis["observations"] == result.analysis["observations"]
    assert old_files == {str(path.relative_to(output)): path.read_bytes()
                         for path in output.rglob("*") if path.is_file()}
    for path in output.rglob("*"):
        if path.is_file():
            assert b"synthetic-analysis-secret" not in path.read_bytes()


def test_prompt_requirement_rejection_does_not_downgrade_analysis_or_resend(monkeypatch, tmp_path):
    text = "Sample A has mass 3 g."

    def respond(body, payload):
        if "source" in payload:
            return envelope({"records": [{"record_type": "mass", "values": {
                "entity": "A", "value": "3", "unit": "g", "conditions": None},
                "quotes": {"entity": text, "value": text, "unit": text, "conditions": ""}}]})
        # A service rejection must remain visible, even if our local prompt is valid.
        return httpx.Response(400, json={"error": {"type": "invalid_request_error",
            "message": "Prompt must contain the word 'json' in some form to use "
                       "'response_format' of type 'json_object'."}})

    sent = install_http(monkeypatch, tmp_path, respond)
    result = run(text, tmp_path / "rejected", model_options={"structured_output": "json_object"})
    assert len(sent) == 2 and all(body["response_format"]["type"] == "json_object" for body in sent)
    assert len(result.candidates) == 1 and result.analysis["status"] == "failed"
    assert result.analysis["budget"]["reserved"] == 1
    assert result.analysis["budget"]["total_reserved"] == 2
    assert result.analysis["jobs"][0]["steps"][0]["http_attempts"] == 1


TABLE = ("<table><tr><td>A</td><td>11</td><td>g</td></tr>"
         "<tr><td>B</td><td>22</td><td>g</td></tr></table>")


def test_table_window_candidates_keep_pointer_local_ids_and_blockers_after_supported_binding(monkeypatch, tmp_path):
    def respond(body, payload):
        if "table" in payload:
            table = payload["table"]
            if "window" not in table:
                return envelope({"records": []}, finish="length")
            owned = set(table["window"]["owned_cell_refs"])
            records = []
            for anchor in table["entries"]:
                if anchor["quote"] not in {"11", "22"} or anchor["ref"] not in owned:
                    continue
                entity = next(entry for entry in table["entries"]
                              if entry["kind"] == "cell" and entry["row"] == anchor["row"] and entry["column"] == 0)
                unit = next(entry for entry in table["entries"]
                            if entry["quote"] == "g" and entry["row"] == anchor["row"])

                def select(entry):
                    return {"ref": entry["ref"], "quote": entry["quote"]}

                records.append({"record_id": "reading", "record_type": "mass", "scope": "individual", "scope_sources": [],
                    "anchors": [select(anchor)], "fields": {
                        "entity": {"value": entity["quote"], "sources": [select(entity)], "relation": "row"},
                        "value": {"value": anchor["quote"], "sources": [select(anchor)], "relation": "anchor"},
                        # The unit is in the same row, not the same column.
                        # Identity review must not erase this geometry blocker.
                        "unit": {"value": "g", "sources": [select(unit)], "relation": "column"},
                        "conditions": {"value": None, "sources": []},
                    }})
            return envelope({"records": records})
        if not payload["lookup_history"]:
            return envelope(read_initial_passage(payload))
        objects = [{"object_id": name, "name": name, "kind": "sample", "scope": "row " + name,
                    "is_table_subject": True, "mentions": [quoted(payload, "<td>" + name + "</td>")]}
                   for name in ("A", "B")]
        bindings = [{"observation_id": item["observation_id"], "subject_id": item["entity"],
                     "status": "supported", "evidence": [quoted(payload, TABLE)],
                     "reason": "Row identifies the sample; unit still needs independent correction.",
                     "target_match": "supported", "property_name": "mass", "statistic": None}
                    for item in payload["observations"]]
        return envelope(decide(objects, bindings))

    sent = install_http(monkeypatch, tmp_path, respond)
    result = run(TABLE, tmp_path / "table", execution=ExecutionOptions(adaptive_chunking=False, max_model_calls=3))
    assert len(sent) == 5 and result.schema_version == "0.6" and result.analysis["status"] == "complete"
    assert [table.chunk_id for table in result.table_results] == ["table-0001.1", "table-0001.2"]
    assert result.format_status == "complete"
    observations = result.analysis["observations"]
    assert len(observations) == 2 and len({item["candidate_id"] for item in observations}) == 2
    assert {item["candidate_id"] for item in observations} == {"table-0001.1:reading", "table-0001.2:reading"}
    assert {item["source_record"] for item in observations} == {
        "/table_results/0/candidates/0", "/table_results/1/candidates/0"}
    for index, item in enumerate(observations):
        assert item["source_kind"] == "table" and item["binding_status"] == "supported"
        assert item["unit"] == "g" and item["value"] in {"11", "22"}
        original = result.table_results[index].candidates[0]
        assert original["record_id"] == "reading" and original["issues"]
        assert item["blockers"] == [issue for issue in original["issues"] if issue.get("severity") != "info"]
        assert item["source_evidence"] and item["evidence_ids"] and item["binding_evidence_ids"]
        assert item["semantic_status"] == "model_proposed"
    assert len(result.analysis["review"]) == 2
    assert {step["route_id"] for job in result.analysis["jobs"] for step in job["steps"]} == {"primary-01"}
    assert result.analysis["budget"]["extraction_reserved"] == 3
    assert result.analysis["budget"]["total_reserved"] == 5
