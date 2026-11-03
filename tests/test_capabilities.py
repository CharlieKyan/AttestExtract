"""Documented endpoint constraints are checked locally, without live probes."""

import httpx
import pytest

from attestextract.backends import OpenAICompatibleBackend
from attestextract.capabilities import check_structured_output, structured_output_capabilities
from attestextract.errors import ConfigurationError
from attestextract.models import ExtractionRequest
from attestextract.providers import resolve_model_config


REQUEST = ExtractionRequest(chunk_id="chunk-0001", messages=[{"role": "user", "content": "Return JSON."}],
                            output_schema={"type": "object", "properties": {}, "additionalProperties": False})


@pytest.mark.parametrize("base", ["https://api.deepseek.com", "https://api.deepseek.com/v1/"])
def test_deepseek_chat_schema_is_rejected_before_transport(base, monkeypatch):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("Invalid mode must not contact service"))
    with pytest.raises(ConfigurationError, match="DeepSeek Chat Completions does not support json_schema"):
        OpenAICompatibleBackend(base, "deepseek-flash", structured_output="json_schema")
    details = structured_output_capabilities(base, "deepseek-flash")
    assert details["modes"] == {"off": "available", "json_object": "documented", "json_schema": "unsupported"}
    assert details["evidence"] == "documentation_only"
    assert details["server_enforcement"] == "not_verified"
    assert details["protocol"] == "chat_completions"


def test_mutating_output_mode_cannot_bypass_preflight():
    backend = OpenAICompatibleBackend("https://api.deepseek.com", "deepseek-flash", structured_output="json_object")
    backend.structured_output = "json_schema"
    with pytest.raises(ConfigurationError, match="separate adapter"):
        backend.prepare_payload(REQUEST)


@pytest.mark.parametrize("base", ["https://dashscope.aliyuncs.com/compatible-mode/v1",
                                  "https://synthetic.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"])
def test_qwen_27b_is_not_in_the_documented_schema_list(base):
    details = structured_output_capabilities(base, "qwen3.8-27b")
    assert details["region"] == "beijing"
    assert details["modes"]["json_object"] == "documented"
    assert details["modes"]["json_schema"] == "not_documented"
    with pytest.raises(ConfigurationError, match="not listed"):
        OpenAICompatibleBackend(base, "qwen3.8-27b", structured_output="json_schema")


def test_qwen_plus_ambiguous_schema_documentation_is_not_reported_as_verified():
    base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    details = check_structured_output(base, "qwen-plus", "json_schema", provider_options={"enable_thinking": False})
    assert details["modes"]["json_schema"] == "unknown"
    assert details["server_enforcement"] == "not_verified"
    with pytest.raises(ConfigurationError, match="enable_thinking=false"):
        check_structured_output(base, "qwen-plus", "json_object", provider_options={"enable_thinking": True})


@pytest.mark.parametrize("base,model", [
    ("https://synthetic.invalid/v1", "deepseek-flash"),
    ("http://localhost:1234/v1", "qwen3.8-27b"),
    ("https://api.deepseek.com.unrelated.invalid/v1", "deepseek-flash"),
    ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "qwen3.8-27b"),
    ("https://dashscope.aliyuncs.com/compatible-mode/v1", "future-model"),
])
def test_unregistered_service_region_or_model_keeps_explicit_schema(base, model):
    backend = OpenAICompatibleBackend(base, model, structured_output="json_schema")
    assert backend.prepare_payload(REQUEST)["response_format"]["type"] == "json_schema"
    details = structured_output_capabilities(base, model)
    assert details["modes"]["json_schema"] == "unknown"
    assert details["evidence"] == "unregistered"
    assert base not in str(details)


def test_custom_adapter_does_not_inherit_chat_protocol_constraints():
    details = check_structured_output("https://api.deepseek.com", "deepseek-flash", "json_schema",
                                      adapter="synthetic-other-protocol")
    assert details["protocol"] == "unknown" and details["modes"]["json_schema"] == "unknown"


def test_presets_do_not_automatically_promote_or_downgrade_modes():
    for selector in ("deepseek:deepseek-flash", "qwen:qwen3.8-27b", "qwen:qwen-plus"):
        assert resolve_model_config(selector, environment={}).structured_output == "json_object"
    with pytest.raises(ConfigurationError, match="json_schema"):
        resolve_model_config("deepseek:deepseek-flash", environment={}, overrides={"structured_output": "json_schema"})
    with pytest.raises(ConfigurationError, match="not listed"):
        resolve_model_config("qwen:qwen3.8-27b", environment={}, overrides={"structured_output": "json_schema"})


def test_capability_results_cannot_mutate_the_catalog():
    first = structured_output_capabilities("https://api.deepseek.com", "deepseek-flash")
    first["modes"]["json_schema"] = "documented"
    assert structured_output_capabilities("https://api.deepseek.com", "deepseek-flash")["modes"]["json_schema"] == "unsupported"
