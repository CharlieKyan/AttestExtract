"""Local diagnostics are complete, opt-in, and safe to inspect offline."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from niceextract import cli, extract
from niceextract.backends import OpenAICompatibleBackend, ReplayBackend
from niceextract.diagnostics import RunTrace
from niceextract.errors import BackendError, ConfigurationError, InvalidResponseError, NiceExtractError, NICEExtractError
from niceextract.models import BackendResponse


TEXT = "采购记录🙂\r\n\r\n编号青禾的金额为 120 元。\r\n"
TASK = {
    "name": "Synthetic diagnostic extraction",
    "record_types": {"purchase": {"fields": {"amount": {"type": "number", "required": True}}}},
}
RECORD = {"record_type": "purchase", "fields": {"amount": {"value": 120, "quote": "120"}}}
FLAT_RECORD = {"record_type": "purchase", "values": {"amount": 120}, "quotes": {"amount": "120"}}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def events(directory):
    return [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]


def all_artifact_text(directory):
    return "\n".join(path.read_text(encoding="utf-8") for path in directory.rglob("*") if path.is_file())


def test_offline_trace_keeps_all_pipeline_artifacts_and_original_unicode_offsets(tmp_path):
    raw = " \n" + json.dumps({"records": [RECORD, RECORD]}, ensure_ascii=False) + "\n "
    plain = extract(TEXT, TASK, backend=ReplayBackend([raw], output_protocol="native-v1"), source_name="采购.txt")
    directory = tmp_path / "diagnostics"
    trace = RunTrace(directory)
    result = extract(TEXT, TASK, backend=ReplayBackend([raw], output_protocol="native-v1"), source_name="采购.txt", trace=trace)
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
    assert set(request) == {"messages", "chunk_id", "output_schema", "format_attempt", "generation", "output_protocol"}
    assert request["output_protocol"] == "native-v1"
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
        extract(TEXT, TASK, backend=ReplayBackend([raw], output_protocol="native-v1"), trace=trace)

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
    result = extract(text, task, backend=ReplayBackend([raw], output_protocol="native-v1"), trace=trace)
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


def test_public_base_error_spelling_preserves_catch_and_diagnostic_type(tmp_path):
    assert NiceExtractError is NICEExtractError
    with pytest.raises(NiceExtractError):
        raise ConfigurationError("synthetic configuration error")

    directory = tmp_path / "base-error"
    RunTrace(directory).fail(NiceExtractError("synthetic failure"), stage="configuration")
    assert read_json(directory / "error.json")["error_type"] == "NICEExtractError"


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
        "NICEEXTRACT_BASE_URL=https://example.invalid/v1\n"
        "NICEEXTRACT_MODEL=synthetic-diagnostics-model\n"
        "NICEEXTRACT_API_KEY=synthetic-cli-api-token\n",
        encoding="utf-8",
    )
    return source, task, env_file


def clean_configuration(monkeypatch):
    for prefix in ("NICEEXTRACT", "ATTESTEXTRACT"):
        for suffix in ("BASE_URL", "MODEL", "API_KEY"):
            monkeypatch.delenv(f"{prefix}_{suffix}", raising=False)


@pytest.mark.parametrize("route", ["dotenv", "profile", "fallback"])
def test_cli_trace_masks_all_configured_keys_before_saving_the_task(tmp_path, monkeypatch, capsys, route):
    clean_configuration(monkeypatch)
    source, task, env_file = cli_inputs(tmp_path)
    directory = tmp_path / "run"
    current_key = "synthetic-dotenv-current-key"
    legacy_key = "synthetic-inherited-legacy-key"
    keys = [current_key, legacy_key]
    monkeypatch.setenv("ATTESTEXTRACT_BASE_URL", "https://old.example/v1")
    monkeypatch.setenv("ATTESTEXTRACT_MODEL", "old-synthetic-model")
    monkeypatch.setenv("ATTESTEXTRACT_API_KEY", legacy_key)
    env = {"NICEEXTRACT_BASE_URL": "https://current.example/v1", "NICEEXTRACT_MODEL": "current-model",
           "NICEEXTRACT_API_KEY": current_key}
    args = ["--env-file", str(env_file), "--output-dir", str(directory), "--check"]
    if route == "profile":
        env["FIXTURE_PROFILE_KEY"] = "synthetic-profile-key"
        keys.append(env["FIXTURE_PROFILE_KEY"])
        profile = tmp_path / "profile.toml"
        profile.write_text('[profiles.sample]\nbase_url="https://profile.example/v1"\nmodel="profile-model"\n'
                           'api_key_env="FIXTURE_PROFILE_KEY"\n', encoding="utf-8")
        args += ["--config", str(profile), "--profile", "sample"]
    if route == "fallback":
        env.update(FIXTURE_PRIMARY_KEY="synthetic-primary-key", FIXTURE_BACKUP_KEY="synthetic-fallback-key")
        keys += [env["FIXTURE_PRIMARY_KEY"], env["FIXTURE_BACKUP_KEY"]]
        args += ["--base-url", "https://primary.example/v1", "--model", "primary-model",
                 "--api-key-env", "FIXTURE_PRIMARY_KEY", "--fallback-base-url", "https://backup.example/v1",
                 "--fallback-model", "backup-model", "--fallback-api-key-env", "FIXTURE_BACKUP_KEY"]
        # Fail after configuration, while retaining the diagnostic trail.
        source = source.with_suffix(".pdf")
        source.write_bytes(b"Synthetic unsupported input.")
    env_file.write_text("".join(f"{name}={value}\n" for name, value in env.items()), encoding="utf-8")
    task.write_text(json.dumps({**TASK, "description": " ".join(keys)}), encoding="utf-8")
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **kw: pytest.fail("Local check attempted HTTP"))
    status = cli.main(["extract", "--input", str(source), "--task", str(task), *args])
    diagnostics = directory / "diagnostics"
    expected_status = "failed" if route == "fallback" else "checked"
    assert read_json(diagnostics / "manifest.json")["status"] == expected_status
    assert (status != 0) == (route == "fallback")
    if route == "fallback":
        assert read_json(diagnostics / "error.json")["error_type"] == "ValueError"
    else:
        assert read_json(diagnostics / "task.json")["description"] == " ".join(["[REDACTED]"] * len(keys))
    captured = capsys.readouterr()
    transcript = all_artifact_text(diagnostics) + captured.out + captured.err
    assert all(key not in transcript for key in keys)


def test_explicit_cli_report_masks_keys_and_preserves_scientific_json(tmp_path, monkeypatch, capsys):
    clean_configuration(monkeypatch)
    current_key, legacy_key = "synthetic-key-violet", "synthetic-key-amber"
    monkeypatch.setenv("ATTESTEXTRACT_API_KEY", legacy_key)
    source, task, env_file = cli_inputs(tmp_path)
    clause = f"Sample Q recorded {current_key} and {legacy_key}."
    original = f"{clause} Stable count: 120."
    source.write_text(original, encoding="utf-8")
    task.write_text(json.dumps({"name": "Synthetic sample properties", "record_types": {
        "reading": {"fields": {"label": {"type": "string"}, "count": {"type": "integer"}}},
    }}), encoding="utf-8")
    env_file.write_text("NICEEXTRACT_BASE_URL=https://current.example/v1\nNICEEXTRACT_MODEL=current-model\n"
                        f"NICEEXTRACT_API_KEY={current_key}\n", encoding="utf-8")

    def endpoint(request):
        assert request.headers["Authorization"] == f"Bearer {current_key}"
        record = {"record_type": "reading", "values": {"label": clause, "count": 120},
                  "quotes": {"label": clause, "count": "120"}}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"records": [record]})},
                                                       "finish_reason": "stop"}]})

    monkeypatch.setattr("niceextract.backends.OpenAICompatibleBackend",
                        lambda **kw: OpenAICompatibleBackend(**kw, transport=httpx.MockTransport(endpoint)))
    output, report = tmp_path / "paper.json", tmp_path / "report.md"
    assert cli.main(["extract", "--input", str(source), "--task", str(task), "--env-file", str(env_file),
                     "--output", str(output), "--report", str(report)]) == 0
    saved = read_json(output)
    fields = saved["candidates"][0]["fields"]
    label_evidence = fields["label"]["evidence"]
    assert original[label_evidence["start"]:label_evidence["end"]] == label_evidence["quote"] == clause
    assert fields["label"]["value"] == clause
    assert fields["count"]["value"] == 120
    evidence = fields["count"]["evidence"]
    assert original[evidence["start"]:evidence["end"]] == evidence["quote"] == "120"
    assert saved["source"]["sha256"] == hashlib.sha256(original.encode()).hexdigest()
    rendered = report.read_text(encoding="utf-8")
    assert r"Sample Q recorded \[REDACTED\] and \[REDACTED\]\." in rendered
    assert "120" in rendered
    captured = capsys.readouterr()
    for secret in (current_key, legacy_key):
        assert secret not in rendered + captured.out + captured.err


def test_python_managed_auth_failure_masks_inactive_legacy_key(tmp_path, monkeypatch, capsys):
    clean_configuration(monkeypatch)
    current_key, legacy_key = "synthetic-python-primary", "synthetic-python-inactive"
    monkeypatch.setenv("ATTESTEXTRACT_API_KEY", legacy_key)
    monkeypatch.setenv("NICEEXTRACT_MODEL_CACHE_DIR", str(tmp_path / "cache"))
    dotenv = tmp_path / "model.env"
    dotenv.write_text(f"NICEEXTRACT_API_KEY={current_key}\n", encoding="utf-8")
    sent = []

    def endpoint(request):
        if request.method == "GET":
            return httpx.Response(401)
        sent.append(request.headers["Authorization"])
        return httpx.Response(401, json={"error": {"message": f"Denied {current_key} and {legacy_key}"}})

    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *args, **kw:
                        original_client(*args, **{**kw, "transport": httpx.MockTransport(endpoint)}))
    directory = tmp_path / "run"
    with pytest.raises(BackendError) as error:
        extract(f"Sample Q contains {current_key} and {legacy_key}.", TASK, base_url="https://model.example/v1",
                model="unlisted-research-model", env_file=dotenv, output_dir=directory)
    assert error.value.status_code == 401 and sent == [f"Bearer {current_key}"]
    diagnostics = directory / "diagnostics"
    assert read_json(diagnostics / "manifest.json")["status"] == "failed"
    captured = capsys.readouterr()
    transcript = all_artifact_text(diagnostics) + captured.out + captured.err
    assert current_key not in transcript and legacy_key not in transcript


@pytest.mark.parametrize("api_key_env", ["NICEEXTRACT_API_KEY", "CUSTOM_DIAGNOSTIC_API_KEY"])
def test_cli_env_file_and_log_directory_produce_result_without_live_api(tmp_path, monkeypatch, capsys, api_key_env):
    clean_configuration(monkeypatch)
    monkeypatch.delenv(api_key_env, raising=False)
    source, task, env_file = cli_inputs(tmp_path)
    if api_key_env != "NICEEXTRACT_API_KEY":
        env_file.write_text(env_file.read_text(encoding="utf-8").replace("NICEEXTRACT_API_KEY=", f"{api_key_env}="), encoding="utf-8")
    output = tmp_path / "result.json"
    directory = tmp_path / "cli-log"
    constructors = []

    class OfflineBackend:
        name = "diagnostics-offline-test"

        def __init__(self, **kwargs):
            constructors.append(kwargs)

        def generate(self, request):
            assert json.loads(request.messages[-1]["content"])["source"] == TEXT
            return BackendResponse(text=json.dumps({"records": [FLAT_RECORD]}))

    monkeypatch.setattr("niceextract.backends.OpenAICompatibleBackend", OfflineBackend)
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

    monkeypatch.setattr("niceextract.backends.OpenAICompatibleBackend", mocked_backend)
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
            "choices": [{"message": {"content": json.dumps({"records": [FLAT_RECORD]})}, "finish_reason": "stop"}],
        })

    backend = OpenAICompatibleBackend(
        base_url="https://example.invalid/v1", model="synthetic-retry-model",
        api_key="synthetic-retry-token", transport=httpx.MockTransport(endpoint), max_attempts=2,
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

    monkeypatch.setattr("niceextract.backends.OpenAICompatibleBackend", mocked_backend)
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

    monkeypatch.setattr("niceextract.backends.OpenAICompatibleBackend", forbidden_backend)
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
