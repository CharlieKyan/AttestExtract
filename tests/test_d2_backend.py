"""D2 contracts: portable controls, explicit failures and observed metadata."""

import json

import httpx
import pytest
from pydantic import ValidationError

from attestextract.backends import OpenAICompatibleBackend, ReplayBackend
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError, ConfigurationError, InvalidResponseError
from attestextract.models import ExtractionRequest, GenerationOptions
from attestextract.pipeline import extract


TASK = {"name": "synthetic", "record_types": {"item": {"fields": {"name": {"type": "string"}}}}}
RECORDS = '{"records":[{"record_type":"item","fields":{"name":{"value":"A","quote":"A"}}}]}'


def request():
    return ExtractionRequest(chunk_id="chunk-1", messages=[{"role": "user", "content": "Return JSON."}])


def envelope(content=RECORDS, finish_reason="stop", **extra):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}], **extra}


@pytest.mark.parametrize("control", [
    {"temperature": -0.1}, {"temperature": 2.1}, {"temperature": True},
    {"temperature": float("nan")}, {"max_output_tokens": 0},
    {"max_output_tokens": True}, {"max_output_tokens": 1.5}, {"max_output_tokens": "10"},
])
def test_generation_rejects_invalid_portable_controls(control):
    with pytest.raises(ValidationError):
        GenerationOptions(**control)


@pytest.mark.parametrize("token_parameter", ["max_tokens", "max_completion_tokens"])
def test_generation_mapping_and_provider_options_are_pure(token_parameter):
    options = {"thinking": {"type": "disabled"}, "enable_thinking": False}
    backend = OpenAICompatibleBackend(
        "https://synthetic.example/v1", "demo", structured_output="json_object",
        provider_options=options, output_token_parameter=token_parameter,
    )
    value = request()
    value.generation = GenerationOptions(temperature=0, max_output_tokens=512)
    before = value.model_dump()
    options["thinking"]["type"] = "enabled"
    expected = {
        "model": "demo", "messages": value.messages, "response_format": {"type": "json_object"},
        "temperature": 0.0, token_parameter: 512,
        "thinking": {"type": "disabled"}, "enable_thinking": False,
    }
    assert backend.prepare_payload(value) == expected
    preview = backend.prepare_payload(value)
    preview["thinking"]["type"] = "changed"
    preview["messages"][0]["content"] = "changed"
    assert backend.prepare_payload(value) == expected
    assert value.model_dump() == before


@pytest.mark.parametrize("key", [
    "model", "messages", "stream", "n", "tools", "tool_choice", "response_format",
    "temperature", "max_tokens", "max_completion_tokens", "api_key", "Authorization",
])
def test_provider_options_cannot_override_reserved_fields(key):
    with pytest.raises(ConfigurationError) as caught:
        OpenAICompatibleBackend("https://synthetic.example/v1", "demo", provider_options={key: "synthetic-secret"})
    assert "synthetic-secret" not in str(caught.value)


@pytest.mark.parametrize("options", [
    {"private": float("inf")}, {"private": object()}, {"private": {1: "a"}},
    {"private": (1, 2)}, {1: "a"}, ["not an object"],
])
def test_provider_options_reject_non_json_values_without_printing_them(options):
    with pytest.raises(ConfigurationError):
        OpenAICompatibleBackend("https://synthetic.example/v1", "demo", provider_options=options)


def test_mutated_provider_options_cannot_bypass_reserved_fields():
    backend = OpenAICompatibleBackend("https://synthetic.example/v1", "demo")
    backend.provider_options["model"] = "replacement"
    with pytest.raises(ConfigurationError):
        backend.prepare_payload(request())


def test_unsupported_explicit_parameter_is_sent_once_and_never_dropped():
    sent = []

    def handler(incoming):
        sent.append(json.loads(incoming.content))
        return httpx.Response(400, json={"error": {"code": "unsupported_parameter", "param": "enable_thinking"}})

    backend = OpenAICompatibleBackend(
        "https://synthetic.example/v1", "demo", provider_options={"enable_thinking": False},
        transport=httpx.MockTransport(handler), max_attempts=3,
    )
    with pytest.raises(BackendError) as caught:
        backend.generate(request())
    assert len(sent) == 1 and sent[0]["enable_thinking"] is False
    assert caught.value.code == "invalid_request"
    assert caught.value.retryable is False
    assert caught.value.http_diagnostics["provider_error"]["code"] == "unsupported_parameter"


@pytest.mark.parametrize(("status", "code", "retryable"), [
    (400, "invalid_request", False), (401, "authentication_error", False),
    (403, "permission_denied", False), (404, "not_found", False),
    (408, "timeout", True), (429, "rate_limited", True),
    (503, "server_error", True), (302, "http_error", False),
])
def test_http_failure_codes_preserve_retry_policy(status, code, retryable):
    backend = OpenAICompatibleBackend(
        "https://synthetic.example/v1", "demo", max_attempts=1,
        transport=httpx.MockTransport(lambda _: httpx.Response(status)),
    )
    with pytest.raises(BackendError) as caught:
        backend.generate(request())
    assert (caught.value.code, caught.value.retryable, caught.value.attempts) == (code, retryable, 1)


@pytest.mark.parametrize(("failure", "code"), [
    (httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "transport_error"),
])
def test_transport_failure_has_stable_code_without_exposing_details(failure, code):
    def handler(incoming):
        raise failure("private transport detail", request=incoming)

    backend = OpenAICompatibleBackend(
        "https://synthetic.example/v1", "demo", max_attempts=1,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(BackendError) as caught:
        backend.generate(request())
    assert caught.value.code == code and caught.value.retryable is True
    assert "private" not in str(caught.value)


@pytest.mark.parametrize(("payload", "code"), [
    (envelope(" "), "empty_response"), (envelope(None), "empty_response"),
    (envelope([]), "unsupported_response"),
    (envelope("partial", "length"), "truncated_response"),
    (envelope("", "content_filter"), "refusal"),
    ({"choices": [{"message": {"content": RECORDS, "refusal": "No"}}]}, "refusal"),
    ({"choices": [{"message": {"content": RECORDS, "tool_calls": [{"id": "call"}]}}]}, "unsupported_response"),
    ({"choices": []}, "invalid_response"),
])
def test_unusable_completion_codes_are_nonretryable(payload, code):
    calls = []

    def handler(incoming):
        calls.append(incoming)
        return httpx.Response(200, json=payload)

    backend = OpenAICompatibleBackend("https://synthetic.example/v1", "demo", transport=httpx.MockTransport(handler))
    with pytest.raises(InvalidResponseError) as caught:
        backend.generate(request())
    assert caught.value.code == code and caught.value.retryable is False
    assert len(calls) == 1


def test_unknown_metadata_does_not_turn_configured_model_into_observed_model():
    backend = OpenAICompatibleBackend(
        "https://synthetic.example/v1", "configured", transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"id": "completion-id", "choices": [{"message": {"content": RECORDS}}]})
        ),
    )
    response = backend.generate(request())
    assert response.model == "configured"
    assert response.reported_model is None
    assert response.finish_reason is None and response.request_id is None


def test_success_records_observed_metadata_and_redacts_identifier(tmp_path):
    trace = RunTrace(tmp_path / "trace")
    backend = OpenAICompatibleBackend(
        "https://synthetic.example/v1", "alias", api_key="synthetic-secret", trace=trace,
        transport=httpx.MockTransport(lambda _: httpx.Response(
            200, json=envelope(model="observed-snapshot"), headers={"x-request-id": "id-synthetic-secret"},
        )),
    )
    result = extract("A", TASK, backend=backend, trace=trace)
    assert result.run.model == "observed-snapshot"
    meta = json.loads(next((trace.directory / "responses").glob("*.meta.json")).read_text(encoding="utf-8"))
    assert meta["reported_model"] == "observed-snapshot" and meta["finish_reason"] == "stop"
    assert meta["request_id"] == "id-[REDACTED]"


def test_error_trace_records_machine_readable_code_and_retryability(tmp_path):
    trace = RunTrace(tmp_path / "trace")
    backend = OpenAICompatibleBackend(
        "https://synthetic.example/v1", "demo", trace=trace, max_attempts=1,
        transport=httpx.MockTransport(lambda _: httpx.Response(429)),
    )
    with pytest.raises(BackendError):
        extract("A", TASK, backend=backend, trace=trace)
    error = json.loads((trace.directory / "error.json").read_text(encoding="utf-8"))
    assert error["code"] == "rate_limited" and error["retryable"] is True


def test_format_regeneration_keeps_generation_options_and_independent_evidence():
    sent = []

    def handler(incoming):
        body = json.loads(incoming.content)
        sent.append(body)
        return httpx.Response(200, json=envelope("not JSON" if len(sent) == 1 else RECORDS))

    backend = OpenAICompatibleBackend("https://synthetic.example/v1", "demo", transport=httpx.MockTransport(handler))
    controls = GenerationOptions(temperature=0, max_output_tokens=250)
    result = extract("A", TASK, backend=backend, generation=controls, format_retries=1)
    assert len(sent) == 2
    assert all(body["temperature"] == 0 and body["max_tokens"] == 250 for body in sent)
    assert controls == GenerationOptions(temperature=0, max_output_tokens=250)
    assert result.candidates[0].fields["name"].evidence.start == 0
    assert result.candidates[0].fields["name"].evidence.end == 1


def test_simulated_adapter_can_declare_replay_mode_without_using_replay_name():
    backend = ReplayBackend([RECORDS])
    backend.name = "synthetic-envelope"
    assert extract("A", TASK, backend=backend).run.mode == "replay"


def test_invalid_backend_mode_fails_before_generation():
    backend = ReplayBackend([RECORDS])
    backend.mode = "unknown"
    with pytest.raises(ConfigurationError, match="mode"):
        extract("A", TASK, backend=backend)
    assert backend._position == 0


def test_mutated_generation_is_validated_before_any_backend_call():
    backend = ReplayBackend([RECORDS])
    generation = GenerationOptions()
    generation.max_output_tokens = -1
    with pytest.raises(ConfigurationError, match="generation"):
        extract("A", TASK, backend=backend, generation=generation)
    assert backend._position == 0
