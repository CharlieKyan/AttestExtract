"""Synthetic protocol fixtures; these tests never call a real endpoint."""

import json
from copy import deepcopy

import httpx
import pytest

from attestextract.backends import OpenAICompatibleBackend
from attestextract import ExecutionOptions, extract
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError, ConfigurationError, InvalidResponseError
from attestextract.models import ExtractionRequest, GenerationOptions
from attestextract.responses_backend import DeepSeekResponsesBackend


SCHEMA = {
    "type": "object", "properties": {"records": {"type": "array", "items": {"type": "object"}}},
    "required": ["records"], "additionalProperties": False,
}


def request(**kwargs):
    return ExtractionRequest(
        chunk_id="chunk-0001", messages=[{"role": "user", "content": "Return synthetic JSON records."}],
        output_schema=deepcopy(SCHEMA), **kwargs,
    )


def message(text='{"records": []}', **overrides):
    return {"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text}], **overrides}


def completion(**overrides):
    return {
        "id": "synthetic-response-id", "object": "response", "status": "completed",
        "model": "deepseek-flash", "output": [message()], "error": None,
        "incomplete_details": None,
        "usage": {"input_tokens": 15, "output_tokens": 5, "total_tokens": 20}, **overrides,
    }


def backend(handler, **kwargs):
    return DeepSeekResponsesBackend(
        "https://api.deepseek.com", "deepseek-flash",
        transport=httpx.MockTransport(handler), **kwargs,
    )


@pytest.mark.parametrize("prefix", ["", "/v1/"])
def test_schema_wire_contract_auth_and_normalized_response(prefix):
    seen = []
    options = {"reasoning": {"effort": "none"}}
    incoming_request = request(generation=GenerationOptions(temperature=0, max_output_tokens=128))

    def handler(incoming):
        seen.append(incoming)
        assert str(incoming.url) == "https://api.deepseek.com" + prefix.rstrip("/") + "/responses"
        assert incoming.headers["Authorization"] == "Bearer synthetic-secret"
        assert json.loads(incoming.content) == {
            "model": "deepseek-flash",
            "input": [{"type": "message", "role": "user", "content": "Return synthetic JSON records."}],
            "reasoning": {"effort": "none"}, "temperature": 0, "max_output_tokens": 128,
            "text": {"format": {"type": "json_schema", "name": "attestextract_records", "schema": SCHEMA}},
        }
        return httpx.Response(200, json=completion(), headers={"x-request-id": "synthetic-request"})

    adapter = DeepSeekResponsesBackend(
        "https://api.deepseek.com" + prefix, "deepseek-flash", api_key="synthetic-secret",
        provider_options=options, transport=httpx.MockTransport(handler),
    )
    response = adapter.generate(incoming_request)
    assert response.text == '{"records": []}'
    assert response.finish_reason == "stop"
    assert response.usage == {"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20}
    assert response.request_id == "synthetic-request"
    assert response.reported_model == "deepseek-flash"
    assert response.attempts == len(seen) == 1
    assert incoming_request.output_schema == SCHEMA
    assert options == {"reasoning": {"effort": "none"}}
    assert "synthetic-secret" not in repr(adapter)
    assert "DeepSeekResponsesBackend" in repr(adapter)


@pytest.mark.parametrize("mode, expected", [("off", None), ("json_object", {"format": {"type": "json_object"}})])
def test_other_modes_and_omitted_generation_limits(mode, expected):
    adapter = backend(lambda _: httpx.Response(200, json=completion()), structured_output=mode)
    payload = adapter.prepare_payload(request())
    assert payload.get("text") == expected
    assert "max_output_tokens" not in payload
    assert "temperature" not in payload
    assert "response_format" not in payload
    assert "messages" not in payload


def test_text_parts_are_concatenated_verbatim_without_reasoning_or_repair():
    output = [
        {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "Synthetic reasoning, not extraction."}]},
        message(content=[{"type": "output_text", "text": '{"records": ['}, {"type": "output_text", "text": "]"}]),
        message("}"),
    ]
    result = backend(lambda _: httpx.Response(200, json=completion(output=output))).generate(request())
    assert result.text == '{"records": []}'


@pytest.mark.parametrize("changes, code", [
    ({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, "truncated_response"),
    ({"status": "incomplete", "incomplete_details": {"reason": "content_filter"}}, "refusal"),
    ({"status": "incomplete", "incomplete_details": {"reason": "unrecognized"}}, "unsupported_response"),
    ({"status": "failed", "error": {"message": "private server details"}}, "response_failed"),
    ({"status": "in_progress"}, "unsupported_response"),
    ({"status": None}, "unsupported_response"),
    ({"status": []}, "unsupported_response"),
    ({"object": "chat.completion"}, "unsupported_response"),
    ({"incomplete_details": {"reason": "max_output_tokens"}}, "unsupported_response"),
    ({"output": []}, "empty_response"),
    ({"output": [message(" \n ")]}, "empty_response"),
    ({"output": [message(content=[{"type": "refusal", "refusal": "cannot comply"}])]}, "refusal"),
    ({"output": [message(), {"type": "function_call", "arguments": "{}"}]}, "unsupported_response"),
    ({"output": [message(), {"type": "web_search_call"}]}, "unsupported_response"),
    ({"output": [message(), {"type": "custom_tool_call"}]}, "unsupported_response"),
    ({"output": [message(), {"type": "tool_search_call"}]}, "unsupported_response"),
    ({"output": [message(status="incomplete")]}, "unsupported_response"),
    ({"output": [message(role="user")]}, "unsupported_response"),
    ({"output": [message(content=[{"type": "output_text", "text": ["bad"]}])]}, "unsupported_response"),
    ({"output": [message(content="bad")]}, "unsupported_response"),
    ({"output": [None]}, "unsupported_response"),
    ({"output": {}}, "unsupported_response"),
])
def test_incomplete_refused_failed_and_unsupported_responses_are_never_accepted_or_retried(changes, code):
    seen = []

    def handler(incoming):
        seen.append(incoming)
        return httpx.Response(200, json=completion(**changes))

    with pytest.raises(InvalidResponseError) as captured:
        backend(handler, max_attempts=3).generate(request())
    assert captured.value.code == code
    assert captured.value.attempts == len(seen) == 1
    assert captured.value.response_metadata["usage"]["completion_tokens"] == 5
    assert "private server" not in str(captured.value)
    assert not captured.value.retryable


def test_failure_retains_verbatim_text_usage_request_id_and_schema_diagnostics(tmp_path):
    partial = '{"records": [{"synthetic": '
    trace = RunTrace(tmp_path / "trace")
    adapter = backend(lambda _: httpx.Response(
        200, json=completion(status="incomplete", incomplete_details={"reason": "max_output_tokens"}, output=[message(partial)]),
        headers={"x-request-id": "synthetic-request"},
    ), trace=trace)
    with pytest.raises(InvalidResponseError) as captured:
        adapter.generate(request(format_attempt=2))
    assert captured.value.response_metadata["finish_reason"] == "length"
    stem = "chunk-0001.format-2"
    assert (trace.directory / f"responses/{stem}.txt").read_text(encoding="utf-8") == partial
    metadata = json.loads((trace.directory / f"responses/{stem}.meta.json").read_text(encoding="utf-8"))
    assert metadata["request_id"] == "synthetic-request"
    assert metadata["usage"]["completion_tokens"] == 5
    protocol = json.loads((trace.directory / f"responses/{stem}.protocol.json").read_text(encoding="utf-8"))
    assert protocol["response_status"] == "incomplete"
    assert protocol["incomplete_reason"] == "max_output_tokens"
    assert protocol["response_id"] == "synthetic-response-id"
    assert (trace.directory / f"schema/{stem}.compliance.json").is_file()


def test_http_retry_uses_shared_attempt_bound_and_no_fallback():
    seen = []
    sleeps = []

    def handler(incoming):
        seen.append(incoming)
        if len(seen) == 1:
            return httpx.Response(429, json={"error": {"message": "rate limit"}})
        return httpx.Response(200, json=completion())

    assert backend(handler, max_attempts=2, sleep_fn=sleeps.append).generate(request()).attempts == 2
    assert len(seen) == 2
    assert sleeps == [0.5]
    assert seen[0].content == seen[1].content
    assert all(incoming.url.path == "/responses" for incoming in seen)


def test_http_schema_rejection_uses_correct_protocol_hint_without_retry():
    seen = []

    def handler(incoming):
        seen.append(incoming)
        return httpx.Response(400, json={"error": {"message": "Unsupported synthetic schema"}})

    with pytest.raises(BackendError) as captured:
        backend(handler, max_attempts=3).generate(request())
    assert captured.value.code == "invalid_request"
    assert len(seen) == 1
    assert "text.format/json_schema" in json.dumps(captured.value.http_diagnostics)
    assert "no fallback" in json.dumps(captured.value.http_diagnostics)


def test_transport_failure_is_bounded_and_redacted():
    seen = []

    def handler(incoming):
        seen.append(incoming)
        raise httpx.ConnectError("Bearer synthetic-secret private URL", request=incoming)

    with pytest.raises(BackendError) as captured:
        backend(handler).generate(request())
    assert len(seen) == 1
    assert captured.value.code == "transport_error"
    assert "synthetic-secret" not in str(captured.value)


def test_trace_and_identifier_redact_known_key_and_urls(tmp_path):
    trace = RunTrace(tmp_path / "trace")
    adapter = backend(lambda _: httpx.Response(
        200, json=completion(output=[message('{"records": [], "synthetic": "test-key"}')]),
        headers={"x-request-id": "test-key https://private.example/request Bearer other-secret"},
    ), api_key="test-key", trace=trace)
    result = adapter.generate(request())
    assert "test-key" not in result.request_id
    assert "private.example" not in result.request_id
    assert "other-secret" not in result.request_id
    for path in trace.directory.rglob("*"):
        if path.is_file():
            assert "test-key" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("url", [None, "http://api.deepseek.com", "https://private.example/v1", "https://api.deepseek.com:444", "https://api.deepseek.com/beta", "https://api.deepseek.com/responses", "https://user@api.deepseek.com", "https://api.deepseek.com?", "https://api.deepseek.com#"])
def test_only_official_documented_base_urls_are_allowed(url):
    with pytest.raises(ConfigurationError):
        DeepSeekResponsesBackend(url, "deepseek-flash")


@pytest.mark.parametrize("options", [
    {"thinking": {"type": "disabled"}}, {"stream": True}, {"tools": []},
    {"previous_response_id": "id"}, {"input": []}, {"text": {}},
    {"max_output_tokens": 32}, {"reasoning": {"summary": "auto"}},
    {"reasoning": {"effort": "unknown"}}, {"reasoning": []},
    {"top_p": True}, {"top_p": 1.1}, {"top_logprobs": -1},
    {"top_logprobs": 1.5}, {"user": 1}, {"user": "甲" * 171},
])
def test_unsupported_or_ignored_provider_options_fail_before_network(options):
    with pytest.raises(ConfigurationError):
        backend(lambda _: pytest.fail("must not call"), provider_options=options)


def test_prepare_revalidates_mutable_settings_and_preserves_schema_copy():
    adapter = backend(lambda _: pytest.fail("must not call"), provider_options={"reasoning": {"effort": "none"}})
    original = request()
    payload = adapter.prepare_payload(original)
    payload["text"]["format"]["schema"]["properties"].clear()
    assert original.output_schema == SCHEMA
    adapter.provider_options["input"] = []
    with pytest.raises(ConfigurationError):
        adapter.prepare_payload(original)
    adapter.provider_options = {}
    adapter.base_url = "https://private.example"
    with pytest.raises(ConfigurationError):
        adapter.generate(original)


def test_schema_mode_requires_schema_and_protocol_token_mapping_is_explicit():
    adapter = backend(lambda _: pytest.fail("must not call"))
    with pytest.raises(ConfigurationError, match="output_schema"):
        adapter.prepare_payload(ExtractionRequest(chunk_id="x", messages=[{"role": "user", "content": "JSON"}]))
    with pytest.raises(ConfigurationError, match="output_token_parameter"):
        backend(lambda _: pytest.fail("must not call"), output_token_parameter="max_tokens")
    with pytest.raises(ConfigurationError, match="output_token_parameter"):
        OpenAICompatibleBackend("https://synthetic.example", "model", output_token_parameter="max_output_tokens")


@pytest.mark.parametrize("messages", [[], [{"role": "tool", "content": "x"}], [{"role": "user", "content": "x", "name": "extra"}]])
def test_unsupported_input_messages_are_not_silently_changed(messages):
    adapter = backend(lambda _: pytest.fail("must not call"))
    with pytest.raises(ConfigurationError):
        adapter.prepare_payload(ExtractionRequest(chunk_id="x", messages=messages, output_schema=SCHEMA))


def test_invalid_usage_values_are_not_coerced_or_counted_twice():
    result = backend(lambda _: httpx.Response(200, json=completion(usage={
        "input_tokens": True, "output_tokens": -1, "total_tokens": 20,
        "input_tokens_details": {"cached_tokens": 5}, "output_tokens_details": {"reasoning_tokens": 2},
    }))).generate(request())
    assert result.usage == {"total_tokens": 20}


@pytest.mark.parametrize("payload", [None, [], "non-envelope"])
def test_non_object_envelope_is_reported(payload):
    with pytest.raises(InvalidResponseError):
        backend(lambda _: httpx.Response(200, json=payload)).generate(request())


def test_non_json_envelope_is_reported_without_echoing_body():
    with pytest.raises(InvalidResponseError) as captured:
        backend(lambda _: httpx.Response(200, text="synthetic private HTTP body")).generate(request())
    assert "private" not in str(captured.value)


SYNTHETIC_TASK = {"name": "synthetic power", "record_types": {"power": {
    "fields": {"watts": {"type": "integer", "required": True}},
}}}
SYNTHETIC_TEXT = "Lamp A uses 8 W."
SYNTHETIC_RECORDS = {"records": [{"record_type": "power", "fields": {
    "watts": {"value": 8, "quote": SYNTHETIC_TEXT},
}}]}


def persistent_run(directory, *, handler, resume=None, key="synthetic-first-key", **options):
    trace = RunTrace(directory)
    adapter = DeepSeekResponsesBackend(
        options.pop("base_url", "https://api.deepseek.com"), options.pop("model", "deepseek-flash"),
        api_key=key, trace=trace, transport=httpx.MockTransport(handler), **options,
    )
    return extract(
        SYNTHETIC_TEXT, SYNTHETIC_TASK, backend=adapter, trace=trace,
        resume_from=resume, execution=ExecutionOptions(adaptive_chunking=False, max_model_calls=1),
    )


@pytest.mark.parametrize("next_key", ["synthetic-first-key", "synthetic-rotated-key"])
def test_python_persistent_resume_reuses_completed_with_same_or_rotated_credentials(tmp_path, next_key):
    calls = []

    def first_handler(incoming):
        calls.append(incoming)
        return httpx.Response(200, json=completion(output=[message(json.dumps(SYNTHETIC_RECORDS))]))

    first = persistent_run(tmp_path / "first", handler=first_handler)
    assert len(calls) == 1
    second = persistent_run(tmp_path / "second", resume=tmp_path / "first", key=next_key,
                            handler=lambda _: pytest.fail("resume must reuse the completed response"))
    assert [candidate.model_dump() for candidate in second.candidates] == [candidate.model_dump() for candidate in first.candidates]
    assert second.candidates[0].fields["watts"].value == 8
    evidence = second.candidates[0].fields["watts"].evidence
    assert SYNTHETIC_TEXT[evidence.start:evidence.end] == SYNTHETIC_TEXT
    assert second.run.usage == {"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20}
    assert second.run.request_attempts == 1
    state = (tmp_path / "first/execution/checkpoint.json").read_text(encoding="utf-8")
    assert "synthetic-first-key" not in state and "synthetic-rotated-key" not in state
    protocol = json.loads((tmp_path / "first/responses/chunk-0001.protocol.json").read_text(encoding="utf-8"))
    assert protocol["response_status"] == "completed"
    summary = json.loads((tmp_path / "second/execution/summary.json").read_text(encoding="utf-8"))
    assert summary["reused_chunks"] == 1


@pytest.mark.parametrize("change", [
    {"base_url": "https://api.deepseek.com/v1"}, {"model": "deepseek-v4-pro"},
    {"structured_output": "json_object"}, {"provider_options": {"reasoning": {"effort": "none"}}},
    {"tls_mode": "1.2"},
])
def test_python_resume_rejects_changed_endpoint_or_model_settings_before_call(tmp_path, change):
    persistent_run(tmp_path / "first", handler=lambda _: httpx.Response(200, json=completion(output=[message(json.dumps(SYNTHETIC_RECORDS))])))
    with pytest.raises(ConfigurationError, match="identity mismatch"):
        persistent_run(tmp_path / "second", resume=tmp_path / "first",
                       handler=lambda _: pytest.fail("identity mismatch must fail before network"), **change)
