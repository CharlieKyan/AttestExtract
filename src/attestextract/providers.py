"""Small, explicit provider presets for selecting a model without a profile.

Presets describe request defaults, not a guarantee of service-side enforcement.
Unknown models and other regions use the existing advanced profile interface.
No network request or environment mutation occurs while resolving a selection.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re
from typing import Any

import httpx

from .backends import OpenAICompatibleBackend
from .config import BackendConfig, resolve_backend_config
from .errors import ConfigurationError


_PRESETS = {
    "deepseek:deepseek-flash": {"thinking": {"type": "disabled"}},
    "qwen:qwen3.8-27b": {"enable_thinking": False},
    "qwen:qwen-plus": {"enable_thinking": False},
}
_DEEPSEEK_URL = "https://api.deepseek.com"
_QWEN_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_QWEN_KEYS = ("QWEN_API_KEY", "DASHSCOPE_API_KEY", "VLM_API_KEY")


def available_models() -> tuple[str, ...]:
    """Return local preset identifiers; this does not query provider model lists."""
    return tuple(_PRESETS)


def _connection_defaults(provider: str, environment: Mapping[str, str], explicit_key: str | None) -> dict:
    if provider == "deepseek":
        key_name = explicit_key if explicit_key is not None else "DEEPSEEK_API_KEY"
        return {"api_key_env": key_name, "base_url": environment.get("DEEPSEEK_BASE_URL", _DEEPSEEK_URL)}
    # Presence, including an empty value, selects a variable. Empty selected keys
    # must fail rather than silently falling back to another account's credential.
    key_name = explicit_key if explicit_key is not None else next(
        (name for name in _QWEN_KEYS if name in environment), "DASHSCOPE_API_KEY",
    )
    url_name = "VLM_BASE_URL" if key_name == "VLM_API_KEY" else "QWEN_BASE_URL"
    return {"api_key_env": key_name, "base_url": environment.get(url_name, _QWEN_URL)}


def _check_endpoint(config: BackendConfig, provider: str, *, explicit_url: bool,
                    explicit_key: bool, region: str | None) -> None:
    # Reuse the compatible backend's URL and parameter validation without I/O.
    OpenAICompatibleBackend(
        config.base_url, config.model, timeout=config.timeout, max_attempts=config.max_attempts,
        structured_output=config.structured_output, provider_options=config.provider_options,
        output_token_parameter=config.output_token_parameter,
    )
    url = httpx.URL(config.base_url)
    path = url.path.rstrip("/")
    if provider == "deepseek":
        official_host = url.host == "api.deepseek.com"
        official = official_host and path in ("", "/v1")
    else:
        official_host = url.host == "dashscope.aliyuncs.com" or url.host.endswith(".cn-beijing.maas.aliyuncs.com")
        official = official_host and path == "/compatible-mode/v1"
        if url.host.endswith(".aliyuncs.com") and not official_host:
            raise ConfigurationError("The Qwen shortcut supports Beijing endpoints only; use a complete profile for another region.")
    official = official and url.scheme == "https" and url.port in (None, 443)
    if official:
        return
    if official_host:
        raise ConfigurationError("The selected official endpoint needs HTTPS and the provider's API base path; use a complete profile for other endpoints.")
    if not explicit_url:
        raise ConfigurationError("Provider environment addresses must use that provider's official endpoint; custom services need an explicit base_url and api_key_env, or a complete profile.")
    if not explicit_key:
        raise ConfigurationError("A custom base_url requires an explicit api_key_env binding; a default provider credential will not be sent automatically.")
    if region is not None:
        raise ConfigurationError("A region selection cannot verify a custom endpoint; use a complete profile or omit region with an explicit credential binding.")


def resolve_model_config(
    selector: str, *, environment: Mapping[str, str],
    overrides: Mapping[str, Any] | None = None, region: str | None = None,
) -> BackendConfig:
    """Resolve a supported ``provider:model`` plus optional explicit overrides.

    Qwen keys are selected by presence: QWEN_API_KEY, DASHSCOPE_API_KEY, then
    VLM_API_KEY. The first two use QWEN_BASE_URL; VLM uses VLM_BASE_URL. Missing
    provider URLs use the official default. Global ATTESTEXTRACT settings and
    dedicated *_MODEL variables never influence this explicit model selection.
    """
    if not isinstance(selector, str) or selector.count(":") != 1 or any(ch.isspace() for ch in selector):
        raise ConfigurationError("Select a model as provider:model; list available presets or use a complete profile.")
    provider, model = selector.split(":", 1)
    if provider not in {"deepseek", "qwen"}:
        raise ConfigurationError("Unknown provider; select deepseek or qwen, or configure an advanced profile and adapter.")
    if selector not in _PRESETS:
        raise ConfigurationError("This model has no preset; list available models or use a complete profile with explicit endpoint and parameters.")
    if region is not None and (provider != "qwen" or region != "beijing"):
        raise ConfigurationError("Region is supported only as beijing for Qwen; use a complete profile for other regions.")
    if overrides is not None and not isinstance(overrides, Mapping):
        raise ConfigurationError("Model overrides must be a mapping of configuration options.")
    supplied = dict(overrides or {})
    if set(supplied) & {"adapter", "model", "api_key"}:
        raise ConfigurationError("Select the provider/model through the model identifier; pass api_key separately and use a profile for another adapter.")
    supplied = {key: value for key, value in supplied.items() if value is not None}
    values = _connection_defaults(provider, environment, supplied.get("api_key_env"))
    values.update(
        adapter="openai-compatible", model=model, timeout=60.0, max_attempts=1,
        structured_output="json_object", format_retries=0,
        output_token_parameter="max_tokens", provider_options=deepcopy(_PRESETS[selector]),
    )
    generation = {"max_output_tokens": 2048}
    if "generation" in supplied:
        if not isinstance(supplied["generation"], Mapping):
            raise ConfigurationError("generation overrides must be a mapping of generation controls.")
        generation.update(supplied.pop("generation"))
    values.update(supplied)
    values["generation"] = generation
    # This validates unknown keys/types with messages that do not echo values.
    config = resolve_backend_config(overrides=values, environment={})
    _check_endpoint(config, provider, explicit_url="base_url" in supplied,
                    explicit_key="api_key_env" in supplied, region=region)
    return config


def resolve_model_credentials(
    config: BackendConfig, environment: Mapping[str, str], api_key: str | None = None,
) -> str:
    """Resolve one credential; an explicit empty value never triggers fallback."""
    name = config.api_key_env
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
        raise ConfigurationError("api_key_env must be a valid environment variable name.")
    value = api_key if api_key is not None else environment.get(name)
    if value is None or value == "" or isinstance(value, str) and not value.strip():
        raise ConfigurationError(f"Missing credential: set {name} in the environment or an explicit env_file, or supply api_key.")
    if not isinstance(value, str) or not value.isascii() or "\r" in value or "\n" in value:
        raise ConfigurationError("The API key must be a nonempty ASCII string without newlines.")
    return value
