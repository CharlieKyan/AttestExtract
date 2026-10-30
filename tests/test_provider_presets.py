"""Minimal selection contracts with synthetic environment values and no network."""

import httpx
import pytest

from attestextract.errors import ConfigurationError
from attestextract.providers import available_models, resolve_model_config, resolve_model_credentials


@pytest.mark.parametrize("selector,key,base,options", [
    ("deepseek:deepseek-flash", "DEEPSEEK_API_KEY", "https://api.deepseek.com", {"thinking": {"type": "disabled"}}),
    ("qwen:qwen3.8-27b", "DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1", {"enable_thinking": False}),
    ("qwen:qwen-plus", "DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1", {"enable_thinking": False}),
])
def test_selecting_a_known_model_requires_only_its_credential(selector, key, base, options, monkeypatch):
    monkeypatch.setattr(httpx.Client, "request", lambda *a, **kw: pytest.fail("Preset resolution must not call a service"))
    environment = {key: "synthetic-credential", "ATTESTEXTRACT_BASE_URL": "https://unrelated.invalid",
                   "ATTESTEXTRACT_API_KEY": "unrelated-key", "ATTESTEXTRACT_MODEL": "old-model",
                   "VLM_MODEL": "ignored-model", "DEEPSEEK_MODEL": "ignored-model"}
    config = resolve_model_config(selector, environment=environment)
    assert config.base_url == base and config.model == selector.split(":", 1)[1]
    assert config.api_key_env == key and config.provider_options == options
    assert config.generation.max_output_tokens == 2048
    assert config.max_attempts == 1 and config.format_retries == 0
    assert config.structured_output == "json_object"
    assert resolve_model_credentials(config, environment) == "synthetic-credential"


def test_local_catalog_is_fixed_and_does_not_claim_service_discovery():
    assert available_models() == ("deepseek:deepseek-flash", "qwen:qwen3.8-27b", "qwen:qwen-plus")


def test_vlm_credential_keeps_its_own_endpoint_pair():
    environment = {"VLM_API_KEY": "vlm-key", "VLM_BASE_URL": "https://synthetic.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
                   "QWEN_BASE_URL": "https://different.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"}
    config = resolve_model_config("qwen:qwen3.8-27b", environment=environment)
    assert config.base_url == environment["VLM_BASE_URL"] and config.api_key_env == "VLM_API_KEY"
    assert resolve_model_credentials(config, environment) == "vlm-key"


@pytest.mark.parametrize("environment,selected", [
    ({"QWEN_API_KEY": "q", "DASHSCOPE_API_KEY": "d", "VLM_API_KEY": "v"}, "QWEN_API_KEY"),
    ({"DASHSCOPE_API_KEY": "d", "VLM_API_KEY": "v"}, "DASHSCOPE_API_KEY"),
    ({"VLM_API_KEY": "v"}, "VLM_API_KEY"),
])
def test_qwen_key_precedence_is_explicit_and_stable(environment, selected):
    environment["VLM_BASE_URL"] = "https://unused.invalid"
    if selected == "VLM_API_KEY":
        environment.pop("VLM_BASE_URL")
    config = resolve_model_config("qwen:qwen-plus", environment=environment)
    assert config.api_key_env == selected
    assert resolve_model_credentials(config, environment) == environment[selected]
    assert config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"


@pytest.mark.parametrize("value", ["", " "])
def test_empty_preferred_credential_never_falls_back_to_another_account(value):
    environment = {"QWEN_API_KEY": value, "VLM_API_KEY": "other-account"}
    config = resolve_model_config("qwen:qwen-plus", environment=environment)
    with pytest.raises(ConfigurationError, match="QWEN_API_KEY"):
        resolve_model_credentials(config, environment)


def test_missing_selected_key_does_not_use_global_or_other_provider_credential():
    environment = {"ATTESTEXTRACT_API_KEY": "old-key", "VLM_API_KEY": "qwen-key"}
    config = resolve_model_config("deepseek:deepseek-flash", environment=environment)
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        resolve_model_credentials(config, environment)


def test_explicit_credential_binding_and_value_take_precedence():
    environment = {"VLM_API_KEY": "vlm-key", "CUSTOM_KEY": "custom-key"}
    config = resolve_model_config("qwen:qwen-plus", environment=environment, overrides={"api_key_env": "CUSTOM_KEY"})
    assert resolve_model_credentials(config, environment) == "custom-key"
    assert resolve_model_credentials(config, environment, api_key="explicit-key") == "explicit-key"
    with pytest.raises(ConfigurationError, match="Missing credential"):
        resolve_model_credentials(config, environment, api_key="")


@pytest.mark.parametrize("selector", [None, "qwen", "qwen: qwen-plus", "other:secret-value", "qwen:secret-value", "qwen:qwen-plus:extra"])
def test_unknown_or_malformed_selection_fails_without_echoing_values(selector):
    with pytest.raises(ConfigurationError) as caught:
        resolve_model_config(selector, environment={})
    assert "secret-value" not in str(caught.value)


@pytest.mark.parametrize("selector,region", [("deepseek:deepseek-flash", "beijing"), ("qwen:qwen-plus", "singapore"), ("qwen:qwen-plus", [])])
def test_unimplemented_regions_require_a_profile(selector, region):
    with pytest.raises(ConfigurationError, match="Region"):
        resolve_model_config(selector, environment={}, region=region)


def test_qwen_beijing_region_and_workspace_endpoint_are_supported():
    config = resolve_model_config("qwen:qwen-plus", environment={"QWEN_BASE_URL": "https://sample.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"}, region="beijing")
    assert config.base_url.endswith(".cn-beijing.maas.aliyuncs.com/compatible-mode/v1")


@pytest.mark.parametrize("base", ["", "https://unrelated.invalid/v1", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                                 "https://user:secret-value@dashscope.aliyuncs.com/compatible-mode/v1", "https://dashscope.aliyuncs.com/compatible-mode/v1?key=secret-value",
                                 "http://dashscope.aliyuncs.com/compatible-mode/v1", "https://dashscope.aliyuncs.com/wrong-path"])
def test_automatic_urls_do_not_leak_credentials_or_mix_regions(base):
    with pytest.raises(ConfigurationError) as caught:
        resolve_model_config("qwen:qwen-plus", environment={"QWEN_BASE_URL": base})
    assert "secret-value" not in str(caught.value)


def test_custom_address_requires_explicit_binding_and_is_not_inferred_from_env():
    with pytest.raises(ConfigurationError, match="binding"):
        resolve_model_config("qwen:qwen-plus", environment={}, overrides={"base_url": "http://localhost:1234/v1"})
    config = resolve_model_config("qwen:qwen-plus", environment={}, overrides={
        "base_url": "http://localhost:1234/v1", "api_key_env": "LOCAL_KEY",
    })
    assert config.base_url == "http://localhost:1234/v1" and config.api_key_env == "LOCAL_KEY"
    with pytest.raises(ConfigurationError, match="region"):
        resolve_model_config("qwen:qwen-plus", environment={}, region="beijing", overrides={
            "base_url": "http://localhost:1234/v1", "api_key_env": "LOCAL_KEY",
        })


def test_explicit_options_override_defaults_without_mutating_other_calls():
    options = {"provider_options": {}, "structured_output": "off", "max_attempts": 2,
               "generation": {"temperature": 0.2}, "max_output_tokens": 55,
               "output_token_parameter": "max_completion_tokens"}
    config = resolve_model_config("qwen:qwen-plus", environment={}, overrides=options)
    assert config.provider_options == {} and config.structured_output == "off"
    assert config.generation.temperature == 0.2 and config.generation.max_output_tokens == 55
    assert config.max_attempts == 2 and config.output_token_parameter == "max_completion_tokens"
    assert options["generation"] == {"temperature": 0.2}
    config.provider_options["unexpected"] = True
    assert resolve_model_config("qwen:qwen-plus", environment={}).provider_options == {"enable_thinking": False}


@pytest.mark.parametrize("options", [
    {"adapter": "other"}, {"model": "other"}, {"api_key": "secret-value"},
    {"generation": []}, {"timeout": True}, {"provider_options": {"model": "other"}},
    {"unknown": "secret-value"}, {"api_key_env": "secret-value!"}, {"format_retries": True},
])
def test_invalid_overrides_are_safe_and_explicit(options):
    with pytest.raises(ConfigurationError) as caught:
        resolve_model_config("deepseek:deepseek-flash", environment={}, overrides=options)
    assert "secret-value" not in str(caught.value)


@pytest.mark.parametrize("value", [123, "secret-value\n", "secret-value\r", "秘密"])
def test_invalid_credentials_are_not_echoed(value):
    config = resolve_model_config("deepseek:deepseek-flash", environment={})
    with pytest.raises(ConfigurationError) as caught:
        resolve_model_credentials(config, {}, api_key=value)
    assert "secret-value" not in str(caught.value)
