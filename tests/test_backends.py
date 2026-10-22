import json

import httpx
import pytest

from attestextract.backends import OpenAICompatibleBackend, ReplayBackend
from attestextract.errors import BackendError, ConfigurationError, InvalidResponseError
from attestextract.models import ExtractionRequest


def request():
    return ExtractionRequest(
        chunk_id="chunk-1", messages=[{"role": "user", "content": "Return JSON."}]
    )


def completion(**overrides):
    result = {
        "model": "actual-model",
        "choices": [
            {"finish_reason": "stop", "message": {"content": '{"records": []}'}}
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 2},
    }
    result.update(overrides)
    return result


def test_success_preserves_api_prefix_and_sends_auth_and_messages():
    seen = []

    def handler(incoming):
        seen.append(incoming)
        assert str(incoming.url) == "https://models.example/custom/v1/chat/completions"
        assert incoming.headers["Authorization"] == "Bearer test-secret"
        assert json.loads(incoming.content) == {
            "model": "configured-model",
            "messages": request().messages,
        }
        return httpx.Response(200, json=completion())

    backend = OpenAICompatibleBackend(
        "https://models.example/custom/v1/",
        "configured-model",
        api_key="test-secret",
        transport=httpx.MockTransport(handler),
    )
    result = backend.generate(request())
    assert result.text == '{"records": []}'
    assert result.model == "actual-model"
    assert result.usage == {"prompt_tokens": 4, "completion_tokens": 2}
    assert result.attempts == 1
    assert result.elapsed_seconds >= 0
    assert len(seen) == 1
    assert "test-secret" not in repr(backend)


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504])
def test_transient_status_retries_then_succeeds(status):
    calls = []
    waits = []

    def handler(incoming):
        calls.append(incoming)
        if len(calls) == 1:
            return httpx.Response(status, text="sensitive error body")
        return httpx.Response(200, json=completion())

    backend = OpenAICompatibleBackend(
        "http://localhost/v1", "model", transport=httpx.MockTransport(handler), sleep_fn=waits.append
    )
    assert backend.generate(request()).attempts == 2
    assert len(calls) == 2
    assert waits == [0.5]


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ConnectError])
def test_transport_failure_has_bounded_retries_without_leaking_details(failure):
    calls = []
    waits = []

    def handler(incoming):
        calls.append(incoming)
        raise failure("secret key and private URL", request=incoming)

    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "model", transport=httpx.MockTransport(handler), sleep_fn=waits.append
    )
    with pytest.raises(BackendError) as captured:
        backend.generate(request())
    assert captured.value.attempts == 3
    assert "secret" not in str(captured.value)
    assert "models.example" not in str(captured.value)
    assert captured.value.__suppress_context__
    assert len(calls) == 3
    assert waits == [0.5, 1.0]


def test_timeout_can_recover():
    calls = []

    def handler(incoming):
        calls.append(incoming)
        if len(calls) == 1:
            raise httpx.ReadTimeout("private detail", request=incoming)
        return httpx.Response(200, json=completion())

    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "model", transport=httpx.MockTransport(handler), sleep_fn=lambda _: None
    )
    assert backend.generate(request()).attempts == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 302])
def test_non_transient_status_is_not_retried(status):
    calls = []

    def handler(incoming):
        calls.append(incoming)
        return httpx.Response(status, text="do not expose this body")

    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "model", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(BackendError) as captured:
        backend.generate(request())
    assert captured.value.status_code == status
    assert captured.value.attempts == 1
    assert str(status) in str(captured.value)
    assert "expose" not in str(captured.value)
    assert len(calls) == 1


def test_transient_status_exhaustion_is_reported():
    calls = []

    def handler(incoming):
        calls.append(incoming)
        return httpx.Response(429, json={"error": "secret"})

    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "model", max_attempts=2,
        transport=httpx.MockTransport(handler), sleep_fn=lambda _: None,
    )
    with pytest.raises(BackendError) as captured:
        backend.generate(request())
    assert captured.value.status_code == 429
    assert captured.value.attempts == len(calls) == 2
    assert "secret" not in str(captured.value)


@pytest.mark.parametrize(
    "payload",
    [
        [], {}, {"choices": []}, {"choices": [None]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": []}}]},
        {"choices": [{"message": {"content": "  "}}]},
        {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]},
        {"choices": [{"finish_reason": "content_filter", "message": {"content": "partial"}}]},
        {"choices": [{"finish_reason": [], "message": {"content": "partial"}}]},
        {"choices": [{"message": {"content": "{}", "refusal": "cannot comply"}}]},
        {"choices": [{"message": {"content": "{}", "tool_calls": [{"id": "call"}]}}]},
        {"choices": [{"message": {"content": "{}", "function_call": {"name": "tool"}}}]},
    ],
)
def test_malformed_or_incomplete_responses_are_not_retried(payload):
    calls = []

    def handler(incoming):
        calls.append(incoming)
        return httpx.Response(200, json=payload)

    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "model", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(InvalidResponseError):
        backend.generate(request())
    assert len(calls) == 1


def test_invalid_response_json_does_not_expose_body():
    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "model",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="secret raw body")),
    )
    with pytest.raises(InvalidResponseError) as captured:
        backend.generate(request())
    assert "secret" not in str(captured.value)
    assert captured.value.__suppress_context__


def test_invalid_compression_is_reported_without_retry_or_private_details():
    calls = []

    def handler(incoming):
        calls.append(incoming)
        raise httpx.DecodingError("private response body", request=incoming)

    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "model", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(InvalidResponseError) as captured:
        backend.generate(request())
    assert len(calls) == 1
    assert "private" not in str(captured.value)
    assert captured.value.__suppress_context__


@pytest.mark.parametrize("usage", [None, [], "unknown", {"total_tokens": 7, "negative": -1, "flag": True, "float": 1.5, "nested": {}}])
def test_unknown_usage_shape_is_ignored_and_only_nonnegative_integers_survive(usage):
    backend = OpenAICompatibleBackend(
        "https://models.example/v1", "model",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completion(usage=usage))),
    )
    assert backend.generate(request()).usage == ({"total_tokens": 7} if isinstance(usage, dict) else {})


@pytest.mark.parametrize(
    "base_url",
    [
        "", "example.com/v1", "ftp://example.com/v1", "http://", "http://localhost:bad/v1",
        "https://user:password@example.com/v1", "https://example.com/v1?key=secret",
        "https://example.com/v1#secret", "https://example.com/v1?", "https://example.com/v1#",
        "https://example.com/space here",
    ],
)
def test_rejects_invalid_or_credential_bearing_urls(base_url):
    with pytest.raises(ConfigurationError) as captured:
        OpenAICompatibleBackend(base_url, "model")
    assert "password" not in str(captured.value)
    assert "secret" not in str(captured.value)


@pytest.mark.parametrize("settings", [{"timeout": 0}, {"timeout": 61}, {"timeout": float("nan")}, {"timeout": True}, {"max_attempts": 0}, {"max_attempts": True}, {"model": ""}, {"api_key": "bad\nkey"}])
def test_rejects_invalid_connection_settings(settings):
    config = {"base_url": "https://models.example/v1", "model": "model"}
    config.update(settings)
    with pytest.raises(ConfigurationError):
        OpenAICompatibleBackend(**config)


def test_replay_consumes_fixed_responses_and_fails_on_exhaustion():
    backend = ReplayBackend([{"records": [{"value": "中文😀"}]}, '{"records": []}'])
    assert json.loads(backend.generate(request()).text) == {"records": [{"value": "中文😀"}]}
    assert backend.generate(request()).text == '{"records": []}'
    with pytest.raises(BackendError, match="no remaining responses"):
        backend.generate(request())


@pytest.mark.parametrize("responses", ["not a sequence of responses", [1], [None]])
def test_replay_rejects_invalid_configuration(responses):
    with pytest.raises(ConfigurationError):
        ReplayBackend(responses)


def test_replay_rejects_non_json_objects():
    backend = ReplayBackend([{"not_json": {1, 2}}])
    with pytest.raises(InvalidResponseError):
        backend.generate(request())
