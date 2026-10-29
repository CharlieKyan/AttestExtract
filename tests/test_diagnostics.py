"""Local diagnostics are complete, opt-in, and safe to inspect offline."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from attestextract import cli, extract
from attestextract.backends import OpenAICompatibleBackend, ReplayBackend
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError, ConfigurationError, InvalidResponseError
from attestextract.models import BackendResponse


TEXT = "采购记录🙂\r\n\r\n编号青禾的金额为 120 元。\r\n"
TASK = {
    "name": "Synthetic diagnostic extraction",
    "record_types": {"purchase": {"fields": {"amount": {"type": "number", "required": True}}}},
}
RECORD = {"record_type": "purchase", "fields": {"amount": {"value": 120, "quote": "120"}}}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def events(directory):
    return [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]


def all_artifact_text(directory):
    return "\n".join(path.read_text(encoding="utf-8") for path in directory.rglob("*") if path.is_file())


def test_offline_trace_keeps_all_pipeline_artifacts_and_original_unicode_offsets(tmp_path):
    raw = " \n" + json.dumps({"records": [RECORD, RECORD]}, ensure_ascii=False) + "\n "
    plain = extract(TEXT, TASK, backend=ReplayBackend([raw]), source_name="采购.txt")
    directory = tmp_path / "diagnostics"
    trace = RunTrace(directory)
    result = extract(TEXT, TASK, backend=ReplayBackend([raw]), source_name="采购.txt", trace=trace)
    trace.finish(result.model_dump(mode="json"))

    assert result.candidates == plain.candidates
    assert result.source == plain.source
    assert result.review == plain.review
    evidence = result.candidates[0].fields["amount"].evidence
    assert evidence.start == TEXT.index("120")
    assert TEXT[evidence.start:evidence.end] == evidence.quote
    assert result.source.sha256 == hashlib.sha256(TEXT.encode("utf-8")).hexdigest()

    manifest = read_json(directory / "manifest.json")
    assert manifest["trace_schema_version"] == "0.3"
    assert manifest["status"] == "succeeded"
    assert read_json(directory / "result.json") == result.model_dump(mode="json")
    assert read_json(directory / "document.json")["text"] == TEXT
    assert read_json(directory / "task.json") == result.task.model_dump(mode="json")
    assert not (directory / "error.json").exists()
    stages = {item["stage"] for item in events(directory)}
    assert {"document", "chunking", "model_request", "model_response", "validation", "grounding", "deduplication", "complete"} <= stages

    chunks = list((directory / "chunks").glob("*.json"))
    assert len(chunks) == result.run.chunk_count == 1
    chunk = read_json(chunks[0])
    chunk_id = chunk["chunk_id"]
    assert chunk["text"] == TEXT
    request = read_json(directory / "requests" / f"{chunk_id}.request.json")
    assert json.loads(request["messages"][-1]["content"])["source"] == TEXT
    assert set(request) == {"messages", "chunk_id", "output_schema", "format_attempt"}
    assert manifest["artifact_roles"][f"requests/{chunk_id}.request.json"] == "extraction_request"
    assert any(item.get("kind") == "extraction_request" for item in events(directory))
    assert read_json(directory / "responses" / f"{chunk_id}.meta.json")["attempts"] == 1
    assert (directory / "responses" / f"{chunk_id}.txt").read_bytes().decode("utf-8") == raw
    assert (directory / "prompts" / f"{chunk_id}.system.txt").read_text(encoding="utf-8") == request["messages"][0]["content"]
    assert read_json(directory / "prompts" / f"{chunk_id}.task.json") == result.task.model_dump(mode="json")
    assert (directory / "prompts" / f"{chunk_id}.source.txt").read_bytes().decode("utf-8") == TEXT
    candidates = read_json(directory / "candidates" / f"{chunk_id}.json")
    assert candidates["candidates"] == [candidate.model_dump(mode="json") for candidate in result.candidates]
    assert candidates["review"] == [item.model_dump(mode="json") for item in result.review]


def test_invalid_json_keeps_raw_completion_and_identifies_validation_failure(tmp_path):
    raw = "Not JSON: a synthetic unfinished {\n"
    directory = tmp_path / "invalid-output"
    trace = RunTrace(directory)
    with pytest.raises(InvalidResponseError):
        extract(TEXT, TASK, backend=ReplayBackend([raw]), trace=trace)

    responses = list((directory / "responses").glob("*.txt"))
    assert len(responses) == 1
    assert responses[0].read_bytes().decode("utf-8") == raw
    error = read_json(directory / "error.json")
    assert error["stage"] == "validation"
    assert error["error_type"] == "InvalidResponseError"
    assert read_json(directory / "manifest.json")["status"] == "failed"
    assert not (directory / "result.json").exists()
    assert events(directory)[-1]["event"] == "failed"


def test_trace_redacts_secrets_recursively_in_json_raw_text_events_and_progress(tmp_path):
    secret = 'synthetic-token-"with\\escapes'
    later_secret = "synthetic-added-token"
    directory = tmp_path / "redacted"
    progress = []
    trace = RunTrace(directory, secrets=[secret], progress=progress.append)
    trace.add_secrets(later_secret)
    trace.write_json("nested.json", {
        "nested": [{"value": f"before {secret} after", secret: later_secret}],
    })
    trace.write_text("raw.txt", f"Raw {secret}; encoded {json.dumps(secret)[1:-1]}; {later_secret}")
    trace.event("configuration", "loaded", chunk_id=secret, config={"api_key": later_secret})
    trace.finish({"run": {"model": secret}, "package_version": "test"})

    nested = read_json(directory / "nested.json")
    assert nested == {"nested": [{"value": "before [REDACTED] after", "[REDACTED]": "[REDACTED]"}]}
    assert read_json(directory / "result.json")["run"]["model"] == "[REDACTED]"
    assert "[REDACTED]" in (directory / "raw.txt").read_text(encoding="utf-8")
    combined = all_artifact_text(directory) + "\n".join(progress)
    assert secret not in combined
    assert json.dumps(secret)[1:-1] not in combined
    assert later_secret not in combined


def test_pipeline_redacts_secrets_in_source_request_response_and_candidate_logs(tmp_path):
    secret = "synthetic-private-source-token"
    text = f"前言🙂 {secret}\n记录包含 {secret}。"
    task = {"name": "Synthetic note", "record_types": {"note": {"fields": {"text": {"type": "string"}}}}}
    raw = json.dumps({"records": [{
        "record_type": "note", "fields": {"text": {"value": secret, "quote": f"记录包含 {secret}。"}},
    }]})
    directory = tmp_path / "redacted-pipeline"
    trace = RunTrace(directory, secrets=[secret])
    result = extract(text, task, backend=ReplayBackend([raw]), trace=trace)
    trace.finish(result.model_dump(mode="json"))

    field = result.candidates[0].fields["text"]
    assert field.value == secret
    assert text[field.evidence.start:field.evidence.end] == field.evidence.quote
    assert result.source.sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert secret not in all_artifact_text(directory)
    for folder, suffix in (("chunks", "json"), ("requests", "json"), ("responses", "txt"), ("candidates", "json")):
        artifacts = list((directory / folder).glob(f"*.{suffix}"))
        assert artifacts
        assert any("[REDACTED]" in path.read_text(encoding="utf-8") for path in artifacts)


def test_failed_trace_masks_sensitive_exception_message_and_keeps_first_failure(tmp_path):
    secret = "synthetic-error-secret"
    directory = tmp_path / "error-redaction"
    trace = RunTrace(directory, secrets=[secret])
    trace.fail(BackendError(f"Authentication failed: {secret}", status_code=401), stage="model_request")
    original = (directory / "error.json").read_bytes()
    trace.fail(ValueError("later failure"), stage="output")
    assert (directory / "error.json").read_bytes() == original
    assert read_json(directory / "error.json")["status_code"] == 401
    assert secret not in all_artifact_text(directory)


def test_trace_refuses_nonempty_directory_without_overwriting_existing_run(tmp_path):
    directory = tmp_path / "existing"
    directory.mkdir()
    marker = directory / "manifest.json"
    marker.write_text('{"existing":"keep this run"}', encoding="utf-8")
    original = marker.read_bytes()
    with pytest.raises(ConfigurationError):
        RunTrace(directory)
    assert marker.read_bytes() == original
    assert list(directory.iterdir()) == [marker]


def test_trace_artifacts_cannot_escape_run_directory(tmp_path):
    directory = tmp_path / "trace"
    trace = RunTrace(directory)
    with pytest.raises(ValueError):
        trace.write_text("../outside.txt", "outside")
    assert not (tmp_path / "outside.txt").exists()


def cli_inputs(tmp_path):
    source = tmp_path / "source.txt"
    task = tmp_path / "task.json"
    env_file = tmp_path / "model.env"
    source.write_bytes(TEXT.encode("utf-8"))
    task.write_text(json.dumps(TASK, ensure_ascii=False), encoding="utf-8")
    env_file.write_text(
        "ATTESTEXTRACT_BASE_URL=https://example.invalid/v1\n"
        "ATTESTEXTRACT_MODEL=synthetic-diagnostics-model\n"
        "ATTESTEXTRACT_API_KEY=synthetic-cli-api-token\n",
        encoding="utf-8",
    )
    return source, task, env_file


def clean_configuration(monkeypatch):
    for key in ("ATTESTEXTRACT_BASE_URL", "ATTESTEXTRACT_MODEL", "ATTESTEXTRACT_API_KEY"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("api_key_env", ["ATTESTEXTRACT_API_KEY", "CUSTOM_DIAGNOSTIC_API_KEY"])
def test_cli_env_file_and_log_directory_produce_result_without_live_api(tmp_path, monkeypatch, capsys, api_key_env):
    clean_configuration(monkeypatch)
    monkeypatch.delenv(api_key_env, raising=False)
    source, task, env_file = cli_inputs(tmp_path)
    if api_key_env != "ATTESTEXTRACT_API_KEY":
        env_file.write_text(env_file.read_text(encoding="utf-8").replace("ATTESTEXTRACT_API_KEY=", f"{api_key_env}="), encoding="utf-8")
    output = tmp_path / "result.json"
    directory = tmp_path / "cli-log"
    constructors = []

    class OfflineBackend:
        name = "diagnostics-offline-test"

        def __init__(self, **kwargs):
            constructors.append(kwargs)

        def generate(self, request):
            assert json.loads(request.messages[-1]["content"])["source"] == TEXT
            return BackendResponse(text=json.dumps({"records": [RECORD]}))

    monkeypatch.setattr(cli, "OpenAICompatibleBackend", OfflineBackend)
    status = cli.main([
        "extract", "--input", str(source), "--task", str(task), "--output", str(output),
        "--env-file", str(env_file), "--log-dir", str(directory), "--api-key-env", api_key_env,
    ])

    assert status == 0
    assert constructors[0]["base_url"] == "https://example.invalid/v1"
    assert constructors[0]["model"] == "synthetic-diagnostics-model"
    assert constructors[0]["api_key"] == "synthetic-cli-api-token"
    payload = read_json(output)
    assert read_json(directory / "result.json") == payload
    assert payload["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert read_json(directory / "manifest.json")["status"] == "succeeded"
    assert {"configuration", "output"} <= {item["stage"] for item in events(directory)}
    captured = capsys.readouterr()
    assert "synthetic-cli-api-token" not in all_artifact_text(directory) + captured.err + captured.out


def test_cli_auth_failure_logs_phase_and_status_without_credentials_or_http_body(tmp_path, monkeypatch, capsys):
    clean_configuration(monkeypatch)
    source, task, env_file = cli_inputs(tmp_path)
    output = tmp_path / "result.json"
    directory = tmp_path / "auth-failure"
    secret = "synthetic-cli-api-token"
    response_body_marker = "SENSITIVE-SYNTHETIC-HTTP-BODY"
    received = []

    def endpoint(request):
        received.append(request)
        assert request.headers["Authorization"] == f"Bearer {secret}"
        return httpx.Response(401, json={"error": {
            "message": f"Invalid credentials: {secret}", "debug": response_body_marker,
        }})

    def mocked_backend(**kwargs):
        return OpenAICompatibleBackend(**kwargs, transport=httpx.MockTransport(endpoint), sleep_fn=lambda _: None)

    monkeypatch.setattr(cli, "OpenAICompatibleBackend", mocked_backend)
    status = cli.main([
        "extract", "--input", str(source), "--task", str(task), "--output", str(output),
        "--env-file", str(env_file), "--log-dir", str(directory),
    ])

    assert status == 1
    assert len(received) == 1
    assert not output.exists()
    assert not (directory / "result.json").exists()
    error = read_json(directory / "error.json")
    assert error["error_type"] == "BackendError"
    assert error["status_code"] == 401
    assert error["attempts"] == 1
    assert error["http_diagnostics"]["provider_error"]["message"] == "Invalid credentials: [REDACTED]"
    assert error["stage"] not in {"configuration", "output", "complete"}
    assert read_json(directory / "manifest.json")["status"] == "failed"
    captured = capsys.readouterr()
    diagnostic_text = all_artifact_text(directory) + captured.err + captured.out
    assert secret not in diagnostic_text
    assert response_body_marker not in diagnostic_text
    assert "Authorization" not in diagnostic_text
    assert "https://example.invalid/v1" not in diagnostic_text


def test_http_trace_records_rate_limit_retry_without_recording_error_body(tmp_path):
    directory = tmp_path / "retry"
    trace = RunTrace(directory)
    received = []
    private_body = "SYNTHETIC-RATE-LIMIT-RESPONSE-BODY"

    def endpoint(request):
        received.append(request)
        if len(received) == 1:
            return httpx.Response(429, json={"error": {"message": "Synthetic rate limit.", "debug": private_body}})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps({"records": [RECORD]})}, "finish_reason": "stop"}],
        })

    backend = OpenAICompatibleBackend(
        base_url="https://example.invalid/v1", model="synthetic-retry-model",
        api_key="synthetic-retry-token", transport=httpx.MockTransport(endpoint),
        sleep_fn=lambda _: None, trace=trace, structured_output="json_schema",
    )
    result = extract(TEXT, TASK, backend=backend, trace=trace)
    trace.finish(result.model_dump(mode="json"))

    assert result.run.request_attempts == 2
    http_events = [item for item in events(directory) if item["stage"] == "http"]
    assert [item["attempt"] for item in http_events if item["event"] == "started"] == [1, 2]
    assert [item["status_code"] for item in http_events if item["event"] == "completed"] == [429, 200]
    retries = [item for item in http_events if item["event"] == "retry"]
    assert len(retries) == 1 and retries[0]["status_code"] == 429
    assert read_json(directory / "manifest.json")["status"] == "succeeded"
    request_files = list((directory / "requests").glob("*.request.json"))
    assert len(request_files) == 1
    payload = read_json(request_files[0])
    assert payload == json.loads(received[0].content) == json.loads(received[1].content)
    assert set(payload) == {"model", "messages", "response_format"}
    assert payload["response_format"]["type"] == "json_schema"
    manifest = read_json(directory / "manifest.json")
    assert manifest["artifact_roles"][f"requests/{request_files[0].name}"] == "http_json_body"
    failure = read_json(next((directory / "http").glob("*.error.json")))
    assert failure["provider_error"]["message"] == "Synthetic rate limit."
    diagnostic_text = all_artifact_text(directory)
    assert private_body not in diagnostic_text
    assert "synthetic-retry-token" not in diagnostic_text


def test_truncated_http_completion_is_preserved_and_redacted_before_failure(tmp_path, monkeypatch):
    clean_configuration(monkeypatch)
    source, task, env_file = cli_inputs(tmp_path)
    output = tmp_path / "result.json"
    directory = tmp_path / "truncated"
    secret = "synthetic-cli-api-token"
    raw = f"truncated synthetic text {secret}"

    def endpoint(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": raw}, "finish_reason": "length"}],
        })

    def mocked_backend(**kwargs):
        return OpenAICompatibleBackend(**kwargs, transport=httpx.MockTransport(endpoint), sleep_fn=lambda _: None)

    monkeypatch.setattr(cli, "OpenAICompatibleBackend", mocked_backend)
    status = cli.main([
        "extract", "--input", str(source), "--task", str(task), "--output", str(output),
        "--env-file", str(env_file), "--log-dir", str(directory),
    ])

    assert status == 1
    responses = list((directory / "responses").glob("*.txt"))
    assert len(responses) == 1
    assert responses[0].read_text(encoding="utf-8") == raw.replace(secret, "[REDACTED]")
    assert read_json(directory / "error.json")["stage"] == "model_response"
    assert read_json(directory / "manifest.json")["status"] == "failed"
    assert not output.exists()
    assert not (directory / "result.json").exists()
    assert secret not in all_artifact_text(directory)


@pytest.mark.parametrize("relative", ["manifest.json", "events.jsonl", "nested/arbitrary.json"])
def test_cli_rejects_outputs_inside_log_directory_before_creating_backend(tmp_path, monkeypatch, capsys, relative):
    clean_configuration(monkeypatch)
    source, task, env_file = cli_inputs(tmp_path)
    directory = tmp_path / "protected-log"
    constructors = []

    def forbidden_backend(**kwargs):
        constructors.append(kwargs)
        raise AssertionError("Output conflict must be rejected before backend construction")

    monkeypatch.setattr(cli, "OpenAICompatibleBackend", forbidden_backend)
    status = cli.main([
        "extract", "--input", str(source), "--task", str(task), "--output", str(directory / relative),
        "--env-file", str(env_file), "--log-dir", str(directory),
    ])

    assert status == 1
    assert not constructors
    assert "Output must be outside --log-dir" in capsys.readouterr().err
    assert not (directory / relative).exists()


@pytest.mark.parametrize("secret", ["null", "true", "1"])
def test_json_redaction_preserves_nonstring_literals_that_look_like_secrets(tmp_path, secret):
    directory = tmp_path / "literal-redaction"
    trace = RunTrace(directory, secrets=[secret])
    trace.write_json("payload.json", {"none": None, "boolean": True, "number": 1, "value": secret})

    payload = read_json(directory / "payload.json")
    assert payload["none"] is None
    assert payload["boolean"] is True
    assert type(payload["number"]) is int and payload["number"] == 1
    assert payload["value"] == "[REDACTED]"
