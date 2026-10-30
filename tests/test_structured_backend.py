"""Structured output is explicit, observable, and never silently downgraded."""

from __future__ import annotations

from copy import deepcopy
import json

import httpx
import pytest

from attestextract import cli
from attestextract.backends import OpenAICompatibleBackend
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError, ConfigurationError, InvalidResponseError
from attestextract.models import BackendResponse, ExtractionRequest


SCHEMA = {
    "type": "object",
    "properties": {"records": {"type": "array", "items": {"type": "object"}}},
    "required": ["records"],
    "additionalProperties": False,
}


def request(schema=True):
    return ExtractionRequest(
        chunk_id="chunk-0001",
        messages=[{"role": "user", "content": "Synthetic source; return records."}],
        output_schema=deepcopy(SCHEMA) if schema else None,
    )


def completion(content='{"records": []}', finish_reason="stop"):
    return {
        "model": "synthetic-model",
        "choices": [{"finish_reason": finish_reason, "message": {"content": content}}],
    }


def test_default_request_remains_compatible_without_an_output_schema():
    value = ExtractionRequest(chunk_id="legacy", messages=[])
    assert value.output_schema is None


@pytest.mark.parametrize("include_schema", [False, True])
def test_default_payload_keeps_only_model_and_messages(include_schema):
    value = request(schema=include_schema)
    backend = OpenAICompatibleBackend("https://models.example/v1", "configured-model")
    assert backend.prepare_payload(value) == {
        "model": "configured-model", "messages": value.messages,
    }


def test_opt_in_builds_strict_schema_envelope_without_mutating_request():
    value = request()
    before = value.model_dump()
    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "configured-model", structured_output="json_schema",
    )
    payload = backend.prepare_payload(value)
    assert payload == {
        "model": "configured-model",
        "messages": value.messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "attestextract_records", "strict": True, "schema": SCHEMA},
        },
    }
    payload["messages"][0]["content"] = "changed by caller"
    payload["response_format"]["json_schema"]["schema"]["required"].append("other")
    assert value.model_dump() == before


def test_opt_in_without_schema_fails_before_any_http_attempt():
    calls = []

    def handler(incoming):
        calls.append(incoming)
        return httpx.Response(200, json=completion())

    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "model", structured_output="json_schema",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ConfigurationError, match="output_schema"):
        backend.generate(request(schema=False))
    assert calls == []


@pytest.mark.parametrize("mode", ["auto", "synthetic-private-value", None, []])
def test_unknown_modes_fail_without_echoing_configuration_values(mode):
    with pytest.raises(ConfigurationError) as caught:
        OpenAICompatibleBackend("https://models.example/v1", "model", structured_output=mode)
    assert "synthetic-private-value" not in str(caught.value)


@pytest.mark.parametrize("mode", ["off", "json_schema"])
def test_http_body_matches_prepared_payload_and_credentials_stay_out_of_body(mode):
    received = []
    value = request()

    def handler(incoming):
        received.append(incoming)
        assert incoming.headers["Authorization"] == "Bearer synthetic-secret-key"
        return httpx.Response(200, json=completion())

    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "model", api_key="synthetic-secret-key",
        structured_output=mode, transport=httpx.MockTransport(handler),
    )
    result = backend.generate(value)
    assert result.text == '{"records": []}'
    assert len(received) == 1
    assert json.loads(received[0].content) == backend.prepare_payload(value)
    assert "synthetic-secret-key" not in received[0].content.decode("utf-8")


@pytest.mark.parametrize("status", [400, 422])
def test_unsupported_schema_never_falls_back_or_retries(status):
    calls = []
    pauses = []

    def handler(incoming):
        calls.append(incoming)
        assert json.loads(incoming.content)["response_format"]["type"] == "json_schema"
        return httpx.Response(status, text="private HTTP error body; synthetic-secret-key")

    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "model", api_key="synthetic-secret-key",
        structured_output="json_schema", max_attempts=4,
        transport=httpx.MockTransport(handler), sleep_fn=pauses.append,
    )
    with pytest.raises(BackendError) as caught:
        backend.generate(request())
    assert caught.value.status_code == status
    assert caught.value.attempts == len(calls) == 1
    assert pauses == []
    message = str(caught.value)
    assert "response_format/json_schema" in message
    assert "no fallback" in message
    assert "private HTTP" not in message and "synthetic-secret-key" not in message
    assert "models.example" not in message


def test_default_mode_keeps_ordinary_400_error_message():
    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "model",
        transport=httpx.MockTransport(lambda _: httpx.Response(400, text="bad request")),
    )
    with pytest.raises(BackendError) as caught:
        backend.generate(request())
    assert "response_format" not in str(caught.value)


def test_standalone_backend_traces_real_request_json_without_credentials_or_url(tmp_path):
    directory = tmp_path / "trace"
    trace = RunTrace(directory)
    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "model", api_key="synthetic-secret-key",
        structured_output="json_schema", trace=trace,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completion())),
    )
    value = request()
    backend.generate(value)
    path = directory / "requests" / "chunk-0001.request.json"
    assert json.loads(path.read_text(encoding="utf-8")) == backend.prepare_payload(value)
    raw = path.read_text(encoding="utf-8")
    assert "Authorization" not in raw and "synthetic-secret-key" not in raw
    assert "https://models.example/v1" not in raw


def test_truncated_response_and_finish_reason_remain_observable(tmp_path):
    directory = tmp_path / "trace"
    trace = RunTrace(directory)
    raw = '{"records": [unfinished synthetic-secret-key'
    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "model", api_key="synthetic-secret-key",
        structured_output="json_schema", trace=trace,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completion(raw, "length"))),
    )
    with pytest.raises(InvalidResponseError):
        backend.generate(request())
    response = (directory / "responses" / "chunk-0001.txt").read_text(encoding="utf-8")
    assert response == raw.replace("synthetic-secret-key", "[REDACTED]")
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(item.get("finish_reason") == "length" for item in events)


@pytest.mark.parametrize("mode", [None, "json_schema"])
def test_cli_forwards_explicit_mode_and_logs_effective_mode(tmp_path, monkeypatch, mode):
    source = tmp_path / "source.txt"
    task = tmp_path / "task.json"
    output = tmp_path / "result.json"
    directory = tmp_path / "trace"
    source.write_text("Synthetic source.", encoding="utf-8")
    task.write_text(json.dumps({
        "name": "Synthetic task",
        "record_types": {"note": {"fields": {"text": {"type": "string"}}}},
    }), encoding="utf-8")
    constructors = []

    class OfflineBackend:
        name = "offline-structured-test"

        def __init__(self, **kwargs):
            constructors.append(kwargs)

        def generate(self, value):
            return BackendResponse(text='{"records": []}')

    monkeypatch.setattr("attestextract.backends.OpenAICompatibleBackend", OfflineBackend)
    monkeypatch.delenv("ATTESTEXTRACT_API_KEY", raising=False)
    args = [
        "extract", "--input", str(source), "--task", str(task), "--output", str(output),
        "--base-url", "https://models.example/v1", "--model", "model", "--log-dir", str(directory),
    ]
    if mode is not None:
        args.extend(["--structured-output", mode])
    assert cli.main(args) == 0
    expected = mode or "off"
    assert constructors[0]["structured_output"] == expected
    assert json.loads((directory / "backend.json").read_text(encoding="utf-8"))["structured_output"] == expected
