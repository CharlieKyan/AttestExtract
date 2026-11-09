"""Connection defaults and override precedence, without real service calls."""

import json

import httpx
import pytest

from attestextract import extract
from attestextract.backends import OpenAICompatibleBackend
from attestextract.config import resolve_backend_config
from attestextract.errors import ConfigurationError
from attestextract.providers import resolve_model_config
from attestextract.registry import default_registry


PROFILE = '''[profiles.test]
base_url = "https://synthetic.invalid/v1"
model = "synthetic-model"
timeout = 60
read_timeout = 240
tls_mode = "auto_fallback"
'''


@pytest.mark.parametrize("overrides,timeout,read_timeout", [
    ({}, 60, 240),
    ({"timeout": 90}, 90, None),
    ({"timeout": 90, "read_timeout": None}, 90, None),
    ({"read_timeout": 120}, 60, 120),
    ({"timeout": 90, "read_timeout": 120}, 90, 120),
])
def test_uniform_override_clears_only_lower_priority_read_timeout(tmp_path, overrides, timeout, read_timeout):
    path = tmp_path / "config.toml"
    path.write_text(PROFILE, encoding="utf-8")
    config = resolve_backend_config(config_path=path, profile="test", environment={}, overrides=overrides)
    assert config.timeout == timeout
    assert config.read_timeout == read_timeout
    assert config.tls_mode == "auto_fallback"


@pytest.mark.parametrize("field", ["timeout", "read_timeout"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 601, float("inf"), float("nan"), "private-marker"])
def test_timeout_limits_are_strict_finite_and_do_not_echo_values(field, value):
    with pytest.raises(ConfigurationError) as caught:
        resolve_backend_config(environment={}, overrides={
            "base_url": "https://synthetic.invalid", "model": "synthetic", field: value,
        })
    assert "private-marker" not in str(caught.value)


@pytest.mark.parametrize("field", ["timeout", "read_timeout"])
@pytest.mark.parametrize("value", [0.1, 180, 600])
def test_finite_timeout_limits_up_to_ten_minutes_are_valid(field, value):
    config = resolve_backend_config(environment={}, overrides={
        "base_url": "https://synthetic.invalid", "model": "synthetic", field: value,
    })
    assert getattr(config, field) == value


def test_invalid_profile_limit_is_not_hidden_by_valid_override(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(PROFILE.replace("read_timeout = 240", "read_timeout = 601"), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        resolve_backend_config(config_path=path, profile="test", environment={}, overrides={"timeout": 180})


@pytest.mark.parametrize("selector,expected", [
    ("qwen:qwen-plus", "tls12_fallback"),
    ("qwen:qwen3.8-27b", "tls12_fallback"),
    ("deepseek:deepseek-flash", "tls12_fallback"),
    ("deepseek-responses:deepseek-flash", "tls12_fallback"),
])
def test_preset_and_registry_construction_are_offline(selector, expected, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Configuration and construction must be offline")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    config = resolve_model_config(selector, environment={})
    assert config.timeout is None and config.read_timeout is None
    assert config.tls_mode == expected
    backend = default_registry().create(config.adapter, config=config)
    assert backend.tls_mode == expected


@pytest.mark.parametrize("tls_mode", ["auto", "1.2", "auto_fallback", "tls12_fallback"])
@pytest.mark.parametrize("controls,expected", [
    ({}, {"connect": 15, "read": 180, "write": 30, "pool": 15}),
    ({"timeout": 60}, {"connect": 60, "read": 60, "write": 60, "pool": 60}),
    ({"read_timeout": 240}, {"connect": 15, "read": 240, "write": 30, "pool": 15}),
    ({"timeout": 60, "read_timeout": 240}, {"connect": 60, "read": 240, "write": 60, "pool": 60}),
])
def test_python_model_options_reach_actual_request_timeouts(controls, expected, tls_mode, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"records":[]}'}, "finish_reason": "stop"}]})

    def factory(*args, **kwargs):
        assert kwargs["tls_mode"] == tls_mode
        return OpenAICompatibleBackend(*args, **kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("attestextract.backends.OpenAICompatibleBackend", factory)
    original = json.loads(json.dumps(controls))
    extract("Synthetic text.", {"name": "synthetic", "record_types": {"item": {"fields": {"value": {"type": "string"}}}}},
            model="qwen:qwen-plus", api_key="synthetic-key",
            model_options={**controls, "tls_mode": tls_mode})
    assert controls == original
    assert seen == [expected]


@pytest.mark.parametrize("adapter", ["openai-compatible", "deepseek-responses"])
def test_profile_omitted_connection_controls_use_bounded_defaults(tmp_path, monkeypatch, adapter):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("Profile resolution must be offline"))
    path = tmp_path / "profiles.toml"
    token_parameter = "max_output_tokens" if adapter == "deepseek-responses" else "max_tokens"
    path.write_text('[profiles.test]\nbase_url="https://api.deepseek.com"\nmodel="synthetic"\n'
                    f'adapter="{adapter}"\noutput_token_parameter="{token_parameter}"\n', encoding="utf-8")
    config = resolve_backend_config(config_path=path, profile="test", environment={}, overrides={})
    assert config.tls_mode == "tls12_fallback"
    assert config.max_attempts == 1 and config.format_retries == 0
    assert config.timeout is None and config.read_timeout is None
    backend = default_registry().create(config.adapter, config=config)
    assert backend.tls_mode == config.tls_mode
    assert backend.max_attempts == 1


@pytest.mark.parametrize("tls_mode", ["auto", "1.2", "auto_fallback", "tls12_fallback"])
def test_explicit_profile_connection_controls_remain_authoritative(tmp_path, tls_mode):
    path = tmp_path / "profiles.toml"
    path.write_text(PROFILE.replace('tls_mode = "auto_fallback"', f'tls_mode = "{tls_mode}"')
                    + "max_attempts = 3\n", encoding="utf-8")
    config = resolve_backend_config(config_path=path, profile="test", environment={}, overrides={})
    assert config.tls_mode == tls_mode and config.max_attempts == 3
    assert config.timeout == 60 and config.read_timeout == 240
