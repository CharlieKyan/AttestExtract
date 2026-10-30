"""User-facing Python model selection, verified with synthetic HTTP only."""

import json

import httpx
import pytest

from attestextract import GenerationOptions, RunTrace, extract, providers
from attestextract.backends import OpenAICompatibleBackend, ReplayBackend
from attestextract.errors import BackendError, ConfigurationError


SOURCE = "Lamp A uses 8 W."
TASK = {"name": "power", "record_types": {"power": {"fields": {
    "value": {"type": "integer", "required": True},
}}}}
COMPLETION = '{"records":[{"record_type":"power","fields":{"value":{"value":8,"quote":"8"}}}]}'


@pytest.fixture(autouse=True)
def isolate_credentials(monkeypatch):
    for name in (
        "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
        "QWEN_API_KEY", "QWEN_BASE_URL", "DASHSCOPE_API_KEY",
        "VLM_API_KEY", "VLM_BASE_URL", "VLM_MODEL", "VLM_ENABLE_THINKING",
        "ATTESTEXTRACT_API_KEY", "ATTESTEXTRACT_BASE_URL", "ATTESTEXTRACT_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def install_mock(monkeypatch, handler):
    def factory(*args, **kwargs):
        return OpenAICompatibleBackend(*args, **kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("attestextract.backends.OpenAICompatibleBackend", factory)


def response(request, content=COMPLETION):
    return httpx.Response(200, json={
        "model": json.loads(request.content)["model"],
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    })


def test_switching_only_model_routes_to_matching_service_and_credentials(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-deepseek-key")
    monkeypatch.setenv("VLM_API_KEY", "synthetic-qwen-key")
    monkeypatch.setenv("VLM_BASE_URL", "https://synthetic.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("VLM_MODEL", "ignored-old-model")
    monkeypatch.setenv("ATTESTEXTRACT_BASE_URL", "https://stale.invalid/v1")
    monkeypatch.setenv("ATTESTEXTRACT_MODEL", "ignored-global-model")
    monkeypatch.setenv("ATTESTEXTRACT_API_KEY", "synthetic-unrelated-key")
    observed = []

    def handler(request):
        observed.append((request.url, request.headers["Authorization"], json.loads(request.content)))
        return response(request)

    install_mock(monkeypatch, handler)
    results = [extract(SOURCE, TASK, model=model) for model in (
        "deepseek:deepseek-flash", "qwen:qwen3.8-27b",
    )]
    assert observed[0][0].host == "api.deepseek.com"
    assert observed[1][0].host == "synthetic.cn-beijing.maas.aliyuncs.com"
    assert [item[1] for item in observed] == ["Bearer synthetic-deepseek-key", "Bearer synthetic-qwen-key"]
    assert [item[2]["model"] for item in observed] == ["deepseek-flash", "qwen3.8-27b"]
    assert observed[0][2]["thinking"] == {"type": "disabled"}
    assert observed[1][2]["enable_thinking"] is False
    for _, _, payload in observed:
        assert payload["max_tokens"] == 2048
        assert payload["response_format"] == {"type": "json_object"}
        assert json.loads(payload["messages"][-1]["content"])["source"] == SOURCE
    for result in results:
        assert len(result.candidates) == 1
        field = result.candidates[0].fields["value"]
        assert field.value == 8 and SOURCE[field.evidence.start:field.evidence.end] == "8"


def test_model_path_preserves_output_limit_when_only_temperature_is_overridden(monkeypatch):
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return response(request)

    install_mock(monkeypatch, handler)
    extract(SOURCE, TASK, model="deepseek:deepseek-flash", api_key="synthetic-direct-key",
            generation=GenerationOptions(temperature=0))
    assert payloads[0]["temperature"] == 0
    assert payloads[0]["max_tokens"] == 2048


def test_advanced_controls_are_sent_without_mutating_or_ignoring_them(monkeypatch):
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return response(request)

    install_mock(monkeypatch, handler)
    controls = {"generation": {"temperature": 0.3, "max_output_tokens": 90},
                "provider_options": {}, "structured_output": "off", "output_token_parameter": "max_completion_tokens"}
    original = json.loads(json.dumps(controls))
    extract(SOURCE, TASK, model="qwen:qwen3.8-27b", api_key="synthetic-direct-key",
            model_options=controls, generation=GenerationOptions(temperature=0.1))
    assert controls == original
    assert payloads[0]["temperature"] == 0.1
    assert payloads[0]["max_completion_tokens"] == 90
    assert "max_tokens" not in payloads[0]
    assert "enable_thinking" not in payloads[0] and "response_format" not in payloads[0]


def test_explicit_env_file_and_direct_key_override_without_secret_artifacts(monkeypatch, tmp_path):
    env_file = tmp_path / "private.env"
    env_file.write_text("DEEPSEEK_API_KEY=synthetic-file-key\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-process-key")
    received = []

    def handler(request):
        received.append(request.headers["Authorization"])
        return response(request)

    install_mock(monkeypatch, handler)
    extract(SOURCE, TASK, model="deepseek:deepseek-flash", env_file=env_file)
    trace = RunTrace(tmp_path / "trace")
    result = extract(SOURCE, TASK, model="deepseek:deepseek-flash", env_file=env_file,
                     api_key="synthetic-explicit-key", trace=trace)
    trace.finish(result.model_dump(mode="json"))
    assert received == ["Bearer synthetic-process-key", "Bearer synthetic-explicit-key"]
    backend = json.loads((trace.directory / "backend.json").read_text(encoding="utf-8"))
    assert backend["model_id"] == "deepseek:deepseek-flash" and backend["selection_mode"] == "preset"
    assert backend["generation"]["max_output_tokens"] == 2048
    artifacts = "".join(path.read_text(encoding="utf-8") for path in trace.directory.rglob("*") if path.is_file())
    assert not any(secret in artifacts for secret in ("synthetic-file-key", "synthetic-process-key", "synthetic-explicit-key"))
    assert "https://api.deepseek.com" not in artifacts


def test_named_credential_override_is_explicit(monkeypatch):
    monkeypatch.setenv("SYNTHETIC_SHARED_KEY", "synthetic-selected-key")
    received = []

    def handler(request):
        received.append(request.headers["Authorization"])
        return response(request)

    install_mock(monkeypatch, handler)
    extract(SOURCE, TASK, model="qwen:qwen3.8-27b", api_key_env="SYNTHETIC_SHARED_KEY")
    assert received == ["Bearer synthetic-selected-key"]


@pytest.mark.parametrize("configuration", [
    {}, {"model": "deepseek:deepseek-flash"}, {"model": "unknown:model", "api_key": "synthetic-secret"},
    {"model": "deepseek:deepseek-flash", "api_key": ""},
    {"model": "deepseek:deepseek-flash", "model_options": {"format_retries": 1}},
    {"model": "deepseek:deepseek-flash", "model_options": {"api_key": "synthetic-secret"}},
    {"model": "deepseek:deepseek-flash", "model_options": {"model": "synthetic-secret"}},
    {"model": "deepseek:deepseek-flash", "model_options": "synthetic-secret"},
])
def test_invalid_selection_or_missing_credentials_fail_before_http(configuration, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid configuration must fail before a request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    with pytest.raises(ConfigurationError) as caught:
        extract(SOURCE, TASK, **configuration)
    assert "synthetic-secret" not in str(caught.value)


@pytest.mark.parametrize("configuration", [
    {"model": "deepseek:deepseek-flash"}, {"api_key": "synthetic-secret"}, {"api_key_env": "ANY_KEY"},
    {"base_url": "https://synthetic.invalid"}, {"env_file": "not-a-real-file.env"},
    {"region": "beijing"}, {"model_options": {}},
])
def test_backend_and_selection_configuration_are_mutually_exclusive(configuration):
    with pytest.raises(ConfigurationError, match="either backend"):
        extract(SOURCE, TASK, backend=ReplayBackend([COMPLETION]), **configuration)


def test_existing_backend_injection_does_not_load_credentials_or_apply_presets(monkeypatch):
    received = []

    class Backend(ReplayBackend):
        def generate(self, request):
            received.append(request)
            return super().generate(request)

    def forbidden(*args, **kwargs):
        pytest.fail("Existing backend injection must not resolve model presets")

    monkeypatch.setattr(providers, "resolve_model_config", forbidden)
    monkeypatch.setattr("attestextract.envconfig.load_env_file", forbidden)
    result = extract(SOURCE, TASK, backend=Backend([COMPLETION]))
    assert result.run.mode == "replay"
    assert received[0].generation.max_output_tokens is None
    assert received[0].generation.temperature is None


def test_model_path_format_retry_keeps_generation_configuration(monkeypatch):
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return response(request, 'not JSON' if len(payloads) == 1 else COMPLETION)

    install_mock(monkeypatch, handler)
    result = extract(SOURCE, TASK, model="deepseek:deepseek-flash", api_key="synthetic-key",
                     format_retries=1, generation=GenerationOptions(max_output_tokens=99))
    assert result.run.response_count == 2
    assert len(payloads) == 2 and all(payload["max_tokens"] == 99 for payload in payloads)


def test_model_options_unsupported_by_service_are_not_dropped_or_retried(monkeypatch):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"code": "unsupported_parameter", "param": "special_option"}})

    install_mock(monkeypatch, handler)
    with pytest.raises(BackendError) as caught:
        extract(SOURCE, TASK, model="deepseek:deepseek-flash", api_key="synthetic-key",
                model_options={"max_attempts": 3, "provider_options": {"special_option": True}})
    assert len(sent) == 1 and sent[0]["special_option"] is True
    assert caught.value.code == "invalid_request" and caught.value.retryable is False
