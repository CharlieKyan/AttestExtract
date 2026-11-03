"""Explicit Responses selection contracts; synthetic inputs and intercepted HTTP only."""

import json
import os

import httpx
import pytest

from attestextract import cli
from attestextract.capabilities import structured_output_capabilities
from attestextract.config import resolve_backend_config
from attestextract.errors import BackendError, ConfigurationError
from attestextract.models import ExtractionRequest
from attestextract.providers import resolve_model_config, resolve_model_credentials
from attestextract.registry import default_registry
from attestextract.responses_backend import DeepSeekResponsesBackend


SELECTOR = "deepseek-responses:deepseek-flash"
SCHEMA = {"type": "object", "properties": {"records": {"type": "array", "items": {"type": "object"}}},
          "required": ["records"], "additionalProperties": False}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("ATTESTEXTRACT_", "DEEPSEEK_", "QWEN_", "DASHSCOPE_", "VLM_")):
            monkeypatch.delenv(key)


def test_responses_preset_is_explicit_and_keeps_existing_chat_defaults(monkeypatch):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("Selection made a network call"))
    config = resolve_model_config(SELECTOR, environment={"ATTESTEXTRACT_MODEL": "stale-model",
                                                        "ATTESTEXTRACT_BASE_URL": "https://stale.invalid"})
    assert config.adapter == "deepseek-responses"
    assert config.base_url == "https://api.deepseek.com"
    assert config.model == "deepseek-flash" and config.api_key_env == "DEEPSEEK_API_KEY"
    assert config.structured_output == "json_schema" and config.output_token_parameter == "max_output_tokens"
    assert config.generation.max_output_tokens == 8192 and config.generation.temperature == 0
    assert config.provider_options == {"reasoning": {"effort": "none"}}
    assert config.max_attempts == 1 and config.format_retries == 0 and config.tls_mode == "1.2"
    legacy = resolve_model_config("deepseek:deepseek-flash", environment={})
    assert legacy.adapter == "openai-compatible" and legacy.structured_output == "json_object"
    assert legacy.output_token_parameter == "max_tokens"


def test_credentials_and_explicit_overrides_keep_precedence():
    environment = {"DEEPSEEK_API_KEY": "synthetic-provider", "VLM_API_KEY": "synthetic-other",
                   "CUSTOM_KEY": "synthetic-custom", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1"}
    default = resolve_model_config(SELECTOR, environment=environment)
    assert resolve_model_credentials(default, environment) == "synthetic-provider"
    assert default.base_url.endswith("/v1")
    selected = resolve_model_config(SELECTOR, environment=environment, overrides={
        "api_key_env": "CUSTOM_KEY", "base_url": "https://api.deepseek.com", "structured_output": "json_object",
        "generation": {"max_output_tokens": 512}, "timeout": 20, "tls_mode": "auto",
    })
    assert resolve_model_credentials(selected, environment) == "synthetic-custom"
    assert resolve_model_credentials(selected, environment, api_key="synthetic-direct") == "synthetic-direct"
    assert selected.base_url == "https://api.deepseek.com" and selected.structured_output == "json_object"
    assert selected.timeout == 20 and selected.tls_mode == "auto" and selected.generation.max_output_tokens == 512
    with pytest.raises(ConfigurationError, match="Missing credential"):
        resolve_model_credentials(selected, environment, api_key="")
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        resolve_model_credentials(default, {"VLM_API_KEY": "synthetic-other"})


@pytest.mark.parametrize("base", ["https://synthetic.invalid/v1", "http://api.deepseek.com",
                                  "https://api.deepseek.com/chat/completions", "https://api.deepseek.com/responses"])
def test_specialized_selector_rejects_nonofficial_or_resource_addresses(base):
    with pytest.raises(ConfigurationError):
        resolve_model_config(SELECTOR, environment={}, overrides={"base_url": base, "api_key_env": "CUSTOM_KEY"})
    with pytest.raises(ConfigurationError):
        resolve_model_config(SELECTOR, environment={"DEEPSEEK_BASE_URL": base})


@pytest.mark.parametrize("base", ["https://api.deepseek.com", "https://api.deepseek.com/v1/"])
def test_official_responses_capability_is_documentation_not_live_verification(base):
    capability = structured_output_capabilities(base, "deepseek-flash", adapter="deepseek-responses")
    assert capability["protocol"] == "responses" and capability["provider"] == "deepseek"
    assert capability["modes"]["json_schema"] == "documented"
    assert capability["evidence"] == "documentation_only" and capability["server_enforcement"] == "not_verified"
    assert capability["documentation"] == ["https://api-docs.deepseek.com/api/create-response/"]


@pytest.mark.parametrize("base,model", [
    ("https://synthetic.invalid", "deepseek-flash"),
    ("https://api.deepseek.com.unrelated.invalid", "deepseek-flash"),
    ("https://api.deepseek.com/wrong-path", "deepseek-flash"),
    ("https://api.deepseek.com", "future-model"),
])
def test_responses_capability_does_not_infer_unknown_service_or_model(base, model):
    capability = structured_output_capabilities(base, model, adapter="deepseek-responses")
    assert capability["protocol"] == "responses" and capability["modes"]["json_schema"] == "unknown"
    assert capability["evidence"] == "unregistered" and capability["documentation"] == []
    assert base not in str(capability)


def test_named_profile_registers_responses_and_rejects_cross_protocol_token_parameter(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text('[profiles.responses]\nadapter="deepseek-responses"\n'
                    'base_url="https://api.deepseek.com"\nmodel="deepseek-flash"\n'
                    'structured_output="json_schema"\noutput_token_parameter="max_output_tokens"\n', encoding="utf-8")
    config = resolve_backend_config(config_path=path, profile="responses", environment={}, overrides={})
    backend = default_registry().create(config.adapter, config=config)
    assert isinstance(backend, DeepSeekResponsesBackend)
    request = ExtractionRequest(chunk_id="synthetic", messages=[{"role": "user", "content": "Return JSON."}], output_schema=SCHEMA)
    assert backend.prepare_payload(request)["text"]["format"]["type"] == "json_schema"
    config = config.model_copy(update={"adapter": "openai-compatible", "structured_output": "json_object"})
    with pytest.raises(ConfigurationError, match="output_token_parameter"):
        default_registry().create(config.adapter, config=config)


@pytest.mark.parametrize("status", [200, 400])
def test_registry_routes_only_to_responses_and_never_falls_back(monkeypatch, status):
    calls = []

    def send(client, request, **kwargs):
        calls.append(request)
        if status == 400:
            return httpx.Response(400, json={"error": {"message": "Synthetic unsupported schema", "type": "invalid_request_error"}}, request=request)
        return httpx.Response(200, json={"id": "synthetic-response", "object": "response", "model": "deepseek-flash", "status": "completed",
                                        "output": [{"type": "message", "role": "assistant", "content": [
                                            {"type": "output_text", "text": '{"records":[]}'}]}],
                                        "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}}, request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    config = resolve_model_config(SELECTOR, environment={})
    backend = default_registry().create(config.adapter, config=config, api_key="synthetic-key")
    request = ExtractionRequest(chunk_id="chunk-0001", messages=[{"role": "user", "content": "Return JSON."}],
                                output_schema=SCHEMA, generation=config.generation)
    if status == 400:
        with pytest.raises(BackendError) as caught:
            backend.generate(request)
        assert caught.value.code == "invalid_request" and caught.value.attempts == 1
    else:
        assert backend.generate(request).text == '{"records":[]}'
    assert len(calls) == 1 and str(calls[0].url) == "https://api.deepseek.com/responses"
    assert calls[0].headers["authorization"] == "Bearer synthetic-key"
    payload = json.loads(calls[0].content)
    assert payload["max_output_tokens"] == 8192 and payload["reasoning"] == {"effort": "none"}
    assert payload["text"]["format"]["type"] == "json_schema" and payload["text"]["format"]["schema"] == SCHEMA
    assert "response_format" not in payload and "messages" not in payload


def test_cli_selection_check_is_offline_and_keeps_its_format_retry_default(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("Local check contacted a service"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    source, task = tmp_path / "source.txt", tmp_path / "task.json"
    source.write_text("Lamp A uses 8 W.", encoding="utf-8")
    task.write_text(json.dumps({"name": "synthetic", "record_types": {"power": {
        "fields": {"value": {"type": "integer"}},
    }}}), encoding="utf-8")
    assert cli.main(["extract", "--input", str(source), "--task", str(task), "--model-id", SELECTOR, "--check"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["network_calls"] == 0 and plan["format_retries"] == 0
    assert plan["structured_output_capability"]["protocol"] == "responses"
    assert plan["structured_output_capability"]["server_enforcement"] == "not_verified"
