"""One explicit regeneration validates fresh model output; it never repairs JSON."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from attestextract import cli, extract, load_task
from attestextract.backends import OpenAICompatibleBackend
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError, ConfigurationError, InvalidResponseError, TaskValidationError
from attestextract.models import BackendResponse, ExtractionRequest


TEXT = "前言🙂\r\n\r\nSample A amount 12.\r\n"
QUOTE = "Sample A amount 12."
TASK = {
    "name": "Synthetic format regeneration",
    "record_types": {"measurement": {
        "fields": {
            "subject": {"type": "string", "required": True},
            "amount": {"type": "number", "required": True},
            "condition": {"type": "string"},
        },
        "identity_fields": ["subject"],
    }},
}


def record(subject="Sample A", amount=12, quote=QUOTE):
    return {
        "record_type": "measurement",
        "fields": {
            "subject": {"value": subject, "quote": quote},
            "amount": {"value": amount, "quote": quote},
        },
    }


def encoded(*records):
    return json.dumps({"records": list(records)}, ensure_ascii=False)


def duplicate_amount(second_value):
    return (
        '{"records":[{"record_type":"measurement","fields":{'
        '"subject":{"value":"Sample A","quote":"Sample A amount 12."},'
        '"amount":{"value":12,"quote":"Sample A amount 12.",'
        f'"value":{second_value}'
        '}}}]}'
    )


class RecordingBackend:
    """Record actual invocations and return only explicitly supplied fixtures."""

    name = "synthetic-format-test"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def generate(self, request):
        self.requests.append(request.model_copy(deep=True))
        if not self.responses:
            raise AssertionError("Unexpected additional model invocation")
        response = self.responses.pop(0)
        return response if isinstance(response, BackendResponse) else BackendResponse(text=response)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def trace_events(directory):
    return [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("invalid", [True, -1, 2, 1.0, "1"])
def test_format_retry_setting_is_strict_and_checked_before_model_calls(invalid):
    backend = RecordingBackend(encoded(record()))
    with pytest.raises((ConfigurationError, ValueError, TypeError)):
        extract(TEXT, TASK, backend=backend, format_retries=invalid)
    assert backend.requests == []


@pytest.mark.parametrize("bad", [
    pytest.param('{"records":[ invalid-private-output', id="json-syntax"),
    pytest.param(json.dumps({"records": [{"record_type": "measurement", "fields": {"subject": "invalid-private-output"}}]}), id="records-contract"),
    pytest.param(encoded(record(amount="invalid-private-output")), id="task-schema"),
    pytest.param(encoded(record()).replace('"value": 12', '"value": NaN'), id="non-finite-number"),
])
def test_one_regeneration_preserves_source_task_schema_and_counts_all_replies(bad):
    backend = RecordingBackend(
        BackendResponse(text=bad, attempts=2, usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}),
        BackendResponse(text=encoded(record()), attempts=1, usage={"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}),
    )
    result = extract(TEXT, TASK, backend=backend, format_retries=1)

    first, second = backend.requests
    assert first.chunk_id == second.chunk_id == "chunk-0001"
    assert (first.format_attempt, second.format_attempt) == (1, 2)
    assert first.artifact_stem == "chunk-0001"
    assert second.artifact_stem == "chunk-0001.format-2"
    assert first.messages[-1] == second.messages[-1]
    assert json.loads(second.messages[-1]["content"])["source"] == TEXT
    assert json.loads(second.messages[-1]["content"])["task"] == result.task.model_dump(mode="json")
    assert first.output_schema == second.output_schema
    assert first.output_schema is not None
    assert second.messages[0]["content"] != first.messages[0]["content"]
    assert first.messages[0]["content"] in second.messages[0]["content"]
    assert "invalid-private-output" not in second.messages[0]["content"]
    assert bad not in second.messages[0]["content"]
    assert len(result.candidates) == 1 and not result.candidates[0].needs_review
    assert [(item.field, item.severity) for item in result.review] == [("condition", "info")]
    assert result.candidates[0].chunk_ids == ["chunk-0001"]
    assert result.run.chunk_count == 1
    assert result.run.response_count == 2
    assert result.run.request_attempts == 3
    assert result.run.usage == {"prompt_tokens": 14, "completion_tokens": 6, "total_tokens": 20}
    assert result.source.sha256 == hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
    evidence = result.candidates[0].fields["amount"].evidence
    assert TEXT[evidence.start:evidence.end] == QUOTE


def test_default_stays_fail_fast_without_a_second_model_call():
    backend = RecordingBackend("not valid JSON", encoded(record()))
    with pytest.raises(InvalidResponseError):
        extract(TEXT, TASK, backend=backend)
    assert len(backend.requests) == 1
    assert backend.requests[0].format_attempt == 1


@pytest.mark.parametrize("wrapped", [True, False], ids=["unknown-task-field", "unknown-bare-field"])
def test_unknown_output_keys_and_values_are_not_recycled_into_retry_instructions(wrapped):
    unknown_key = "UNKNOWN-MODEL-PRIVATE-FIELD-MARKER"
    private_value = "untrusted-output-only-value"
    bad_record = record()
    bad_record["fields"][unknown_key] = (
        {"value": private_value, "quote": QUOTE} if wrapped else private_value
    )
    backend = RecordingBackend(encoded(bad_record), encoded(record()))
    result = extract(TEXT, TASK, backend=backend, format_retries=1)

    assert len(backend.requests) == 2
    system = backend.requests[1].messages[0]["content"]
    assert unknown_key not in system
    assert private_value not in system
    assert backend.requests[1].messages[-1] == backend.requests[0].messages[-1]
    assert len(result.candidates) == 1
    assert set(result.candidates[0].fields) == {"subject", "amount"}
    assert result.candidates[0].fields["amount"].value == 12
    assert [(item.field, item.severity) for item in result.review] == [("condition", "info")]


def test_task_loader_still_rejects_duplicate_configuration_keys(tmp_path):
    task = tmp_path / "duplicate-task.json"
    task.write_text(
        '{"name":"Synthetic duplicate task","record_types":{"measurement":{'
        '"fields":{"amount":{"type":"number","type":"string"}}}}}',
        encoding="utf-8",
    )
    with pytest.raises(TaskValidationError):
        load_task(task)


@pytest.mark.parametrize("second_value", [12, 99], ids=["same-value", "conflicting-value"])
def test_duplicate_keys_are_rejected_on_both_attempts_without_a_third_call(tmp_path, second_value):
    directory = tmp_path / "duplicate"
    trace = RunTrace(directory)
    first_bad = duplicate_amount(12)
    second_bad = duplicate_amount(second_value)
    backend = RecordingBackend(first_bad, second_bad, encoded(record()))
    with pytest.raises(InvalidResponseError) as caught:
        extract(TEXT, TASK, backend=backend, format_retries=1, trace=trace)

    assert len(backend.requests) == 2
    assert caught.value.validation_details["phase"] == "json_syntax"
    assert read_json(directory / "manifest.json")["status"] == "failed"
    assert not (directory / "result.json").exists()
    assert (directory / "responses/chunk-0001.txt").read_bytes().decode("utf-8") == first_bad
    assert (directory / "responses/chunk-0001.format-2.txt").read_bytes().decode("utf-8") == second_bad
    assert (directory / "validation/chunk-0001.errors.json").is_file()
    assert (directory / "validation/chunk-0001.format-2.errors.json").is_file()
    events = [event for event in trace_events(directory) if event["stage"] == "format_retry"]
    assert {event["event"] for event in events} >= {"retry", "failed"}
    assert all(event["chunk_id"] == "chunk-0001" and event["format_attempt"] == 2 for event in events)


def test_only_the_failed_chunk_is_regenerated():
    first = "Sample A amount 12."
    second = "Sample B amount 21."
    text = first + "\n\n" + second
    backend = RecordingBackend(encoded(record(quote=first)), "not JSON", encoded(record("Sample B", 21, second)))
    result = extract(text, TASK, backend=backend, format_retries=1, max_chunk_chars=23)

    assert [(item.chunk_id, item.format_attempt) for item in backend.requests] == [
        ("chunk-0001", 1), ("chunk-0002", 1), ("chunk-0002", 2),
    ]
    assert result.run.chunk_count == 2 and result.run.response_count == 3
    assert [candidate.chunk_ids for candidate in result.candidates] == [["chunk-0001"], ["chunk-0002"]]
    assert [candidate.fields["amount"].value for candidate in result.candidates] == [12, 21]


def test_regeneration_preserves_same_values_at_different_source_positions():
    warm = "Sample A amount 12 when warm."
    cold = "Sample A amount 12 when cold."
    text = warm + " " + cold
    backend = RecordingBackend("not JSON", encoded(record(quote=warm), record(quote=cold)))
    result = extract(text, TASK, backend=backend, format_retries=1)

    assert len(result.candidates) == 2
    assert [candidate.fields["amount"].value for candidate in result.candidates] == [12, 12]
    assert len({candidate.candidate_id for candidate in result.candidates}) == 2
    assert [candidate.fields["amount"].evidence.start for candidate in result.candidates] == [0, len(warm) + 1]
    assert all(candidate.chunk_ids == ["chunk-0001"] for candidate in result.candidates)


@pytest.mark.parametrize("regenerate", [False, True], ids=["first-response", "regenerated-response"])
@pytest.mark.parametrize("quote,status", [(None, "missing"), ("12", "ambiguous"), ("absent synthetic quote", "unmatched")])
def test_evidence_problems_stay_reviewable_without_format_regeneration(regenerate, quote, status):
    response = record()
    if quote is None:
        del response["fields"]["amount"]["quote"]
    else:
        response["fields"]["amount"]["quote"] = quote
    replies = (["not JSON"] if regenerate else []) + [encoded(response), encoded(record())]
    backend = RecordingBackend(*replies)
    result = extract(QUOTE + " Another reading is 12.", TASK, backend=backend, format_retries=1)

    assert len(backend.requests) == (2 if regenerate else 1)
    candidate = result.candidates[0]
    assert candidate.needs_review
    assert candidate.fields["amount"].evidence.status == status
    assert any(item.code == f"evidence_{status}" and item.field == "amount" for item in result.review)


def test_trace_preserves_both_attempts_and_masks_secrets_without_changing_offsets(tmp_path):
    secret = "synthetic-format-secret"
    text = "前言🙂\r\n" + secret + "\r\n" + QUOTE
    bad = "invalid-private-output " + secret
    good = encoded(record(quote=QUOTE))
    directory = tmp_path / "trace"
    trace = RunTrace(directory, secrets=[secret])
    backend = RecordingBackend(bad, good)
    result = extract(text, TASK, backend=backend, format_retries=1, trace=trace)
    trace.finish(result.model_dump(mode="json"))

    manifest = read_json(directory / "manifest.json")
    assert manifest["trace_schema_version"] == "0.3"
    assert manifest["status"] == "succeeded"
    for stem in ("chunk-0001", "chunk-0001.format-2"):
        assert (directory / "requests" / f"{stem}.request.json").is_file()
        assert (directory / "responses" / f"{stem}.meta.json").is_file()
        assert (directory / "prompts" / f"{stem}.system.txt").is_file()
        assert (directory / "prompts" / f"{stem}.source.txt").is_file()
    assert (directory / "responses/chunk-0001.txt").read_bytes().decode("utf-8") == bad.replace(secret, "[REDACTED]")
    assert (directory / "responses/chunk-0001.format-2.txt").read_bytes().decode("utf-8") == good
    assert (directory / "validation/chunk-0001.errors.json").is_file()
    assert not (directory / "validation/chunk-0001.format-2.errors.json").exists()
    assert not (directory / "error.json").exists()
    combined = "\n".join(path.read_text(encoding="utf-8") for path in directory.rglob("*") if path.is_file())
    assert secret not in combined
    assert "invalid-private-output" not in backend.requests[1].messages[0]["content"]
    assert json.loads(backend.requests[1].messages[-1]["content"])["source"] == text
    evidence = result.candidates[0].fields["amount"].evidence
    assert evidence.start == text.index(QUOTE)
    assert text[evidence.start:evidence.end] == QUOTE
    events = [event for event in trace_events(directory) if event["stage"] == "format_retry"]
    assert {event["event"] for event in events} >= {"retry", "succeeded"}
    assert all(event["chunk_id"] == "chunk-0001" and event["format_attempt"] == 2 for event in events)


@pytest.mark.parametrize("status,payload", [
    (400, {"error": {"code": "unsupported_response_format"}}),
    (401, {"error": {"code": "invalid_api_key"}}),
    (200, {"choices": []}),
    (200, {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}),
    (200, {"choices": [{"finish_reason": "stop", "message": {"content": "{}", "refusal": "Synthetic refusal"}}]}),
], ids=["http400", "http401", "malformed-envelope", "truncated", "refusal"])
def test_backend_failures_never_trigger_format_regeneration(status, payload):
    received = []

    def endpoint(request):
        received.append(request)
        return httpx.Response(status, json=payload)

    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "synthetic-model", structured_output="json_schema",
        transport=httpx.MockTransport(endpoint), sleep_fn=lambda _: None,
    )
    with pytest.raises(BackendError):
        extract(TEXT, TASK, backend=backend, format_retries=1)
    assert len(received) == 1


def test_http_attempts_and_format_attempts_remain_separate_with_schema_and_logs(tmp_path):
    directory = tmp_path / "http-trace"
    trace = RunTrace(directory)
    received = []

    def endpoint(request):
        received.append(json.loads(request.content))
        count = len(received)
        if count in {1, 3}:
            return httpx.Response(429 if count == 1 else 503, json={"error": {"code": "synthetic-retryable"}})
        content = "not JSON" if count == 2 else encoded(record())
        return httpx.Response(200, json={"usage": {"total_tokens": count}, "choices": [{
            "finish_reason": "stop", "message": {"content": content},
        }]})

    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "synthetic-model", structured_output="json_schema",
        max_attempts=2, transport=httpx.MockTransport(endpoint), sleep_fn=lambda _: None, trace=trace,
    )
    result = extract(TEXT, TASK, backend=backend, format_retries=1, trace=trace)
    trace.finish(result.model_dump(mode="json"))

    assert len(received) == result.run.request_attempts == 4
    assert result.run.response_count == 2
    assert result.run.usage == {"total_tokens": 6}
    assert all(body["response_format"] == received[0]["response_format"] for body in received)
    assert all(body["messages"][-1] == received[0]["messages"][-1] for body in received)
    first_error = directory / "http/chunk-0001.attempt-1.error.json"
    regenerated_error = directory / "http/chunk-0001.format-2.attempt-1.error.json"
    assert read_json(first_error)["status_code"] == 429
    assert read_json(regenerated_error)["status_code"] == 503
    assert read_json(first_error)["attempt"] == read_json(regenerated_error)["attempt"] == 1
    assert len(list((directory / "http").glob("*.error.json"))) == 2
    for stem, expected in (("chunk-0001", received[0]), ("chunk-0001.format-2", received[2])):
        assert read_json(directory / "requests" / f"{stem}.request.json") == expected
    assert result.candidates[0].chunk_ids == ["chunk-0001"]


def test_http_failure_during_regeneration_records_its_failure_without_a_third_call(tmp_path):
    directory = tmp_path / "regeneration-http-failure"
    trace = RunTrace(directory)
    received = []

    def endpoint(request):
        received.append(request)
        if len(received) == 1:
            return httpx.Response(200, json={"choices": [{
                "finish_reason": "stop", "message": {"content": "not JSON"},
            }]})
        return httpx.Response(401, json={"error": {"code": "synthetic_auth_failure"}})

    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "synthetic-model", trace=trace,
        transport=httpx.MockTransport(endpoint), sleep_fn=lambda _: None,
    )
    with pytest.raises(BackendError) as caught:
        extract(TEXT, TASK, backend=backend, format_retries=1, trace=trace)

    assert len(received) == 2
    assert caught.value.status_code == 401
    assert read_json(directory / "manifest.json")["status"] == "failed"
    assert read_json(directory / "error.json")["stage"] == "http"
    assert read_json(directory / "http/chunk-0001.format-2.attempt-1.error.json")["status_code"] == 401
    assert not (directory / "result.json").exists()
    events = [event for event in trace_events(directory) if event["stage"] == "format_retry"]
    assert {event["event"] for event in events} >= {"retry", "failed"}


@pytest.mark.parametrize("option,status,calls", [(None, 1, 1), ("1", 0, 2)], ids=["default-off", "explicit-on"])
def test_cli_controls_pipeline_regeneration_without_requiring_live_api(tmp_path, monkeypatch, option, status, calls):
    source = tmp_path / "source.txt"
    task = tmp_path / "task.json"
    output = tmp_path / "result.json"
    source.write_bytes(TEXT.encode("utf-8"))
    task.write_text(json.dumps(TASK), encoding="utf-8")
    backend = RecordingBackend("not JSON", encoded(record()))
    constructor_options = []

    def configured_backend(**kwargs):
        constructor_options.append(kwargs)
        return backend

    monkeypatch.setattr("attestextract.backends.OpenAICompatibleBackend", configured_backend)
    monkeypatch.delenv("ATTESTEXTRACT_API_KEY", raising=False)
    args = [
        "extract", "--input", str(source), "--task", str(task), "--output", str(output),
        "--base-url", "https://models.example/v1", "--model", "synthetic-model",
    ]
    if option is not None:
        args.extend(["--format-retries", option])
    assert cli.main(args) == status
    assert len(backend.requests) == calls
    assert "format_retries" not in constructor_options[0]
    assert output.is_file() is (status == 0)


def test_request_artifact_stems_keep_chunk_identity_and_default_contract():
    first = ExtractionRequest(chunk_id="chunk-0001", messages=[])
    second = ExtractionRequest(chunk_id="chunk-0001", messages=[], format_attempt=2)
    assert first.format_attempt == 1 and first.artifact_stem == "chunk-0001"
    assert second.chunk_id == first.chunk_id
    assert second.artifact_stem == "chunk-0001.format-2"
