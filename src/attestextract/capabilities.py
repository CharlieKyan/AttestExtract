"""Small, documentation-backed output constraints for known model endpoints.

This is neither live model discovery nor a claim of server-side enforcement.
Unregistered endpoints/adapters keep their explicit configuration; they are
reported as unknown instead of inheriting another service's capabilities.
"""

from __future__ import annotations

from typing import Any

import httpx

from .errors import ConfigurationError


_DEEPSEEK_SOURCE = "https://api-docs.deepseek.com/api/create-chat-completion/"
_DEEPSEEK_RESPONSES_SOURCE = "https://api-docs.deepseek.com/api/create-response/"
_QWEN_SOURCE = "https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output"


def structured_output_capabilities(
    base_url: str, model: str, *, adapter: str = "openai-compatible",
) -> dict[str, Any]:
    """Describe known constraints without I/O or echoing a private endpoint.

    Documentation checked 2026-11-03. DeepSeek's Chat protocol lists only text and
    json_object. Qwen's Schema feature table and model list disagree about Plus;
    therefore the moving qwen-plus alias remains unknown. The qwen3.8-27b model is
    in the JSON Object family but not the documented Schema model list. No preset
    is automatically promoted to Schema on this evidence. DeepSeek Responses
    uses an explicitly selected adapter and documents its own Schema format.
    """
    result: dict[str, Any] = {
        "provider": "unknown", "region": "unknown",
        "protocol": {"openai-compatible": "chat_completions", "deepseek-responses": "responses"}.get(adapter, "unknown"),
        "modes": {"off": "available", "json_object": "unknown", "json_schema": "unknown"},
        "documentation": [], "documentation_checked": None,
        "evidence": "unregistered", "server_enforcement": "not_verified",
        "requires_non_thinking": False,
    }
    if adapter not in {"openai-compatible", "deepseek-responses"}:
        return result
    try:
        url = httpx.URL(base_url)
    except (httpx.InvalidURL, ValueError, TypeError):
        return result  # URL validation belongs to the adapter.
    if url.scheme != "https" or url.port not in (None, 443) or url.userinfo or url.query or url.fragment:
        return result
    path = url.path.rstrip("/")
    if adapter == "deepseek-responses":
        if url.host == "api.deepseek.com" and path in ("", "/v1") and model in {"deepseek-flash", "deepseek-v4-pro"}:
            result.update(provider="deepseek", region="provider_default", evidence="documentation_only",
                          documentation=[_DEEPSEEK_RESPONSES_SOURCE], documentation_checked="2026-11-03")
            result["modes"].update(json_object="documented", json_schema="documented")
        return result
    if url.host == "api.deepseek.com" and path in ("", "/v1"):
        result.update(provider="deepseek", region="provider_default", evidence="documentation_only",
                      documentation=[_DEEPSEEK_SOURCE], documentation_checked="2026-11-03")
        result["modes"].update(json_object="documented", json_schema="unsupported")
    elif (url.host == "dashscope.aliyuncs.com" or url.host.endswith(".cn-beijing.maas.aliyuncs.com")) and path == "/compatible-mode/v1":
        result.update(provider="qwen", region="beijing")
        if model in ("qwen3.8-27b", "qwen-plus"):
            result.update(evidence="documentation_only", documentation=[_QWEN_SOURCE],
                          documentation_checked="2026-11-03")
            result["modes"]["json_object"] = "documented"
            if model == "qwen3.8-27b":
                result["modes"]["json_schema"] = "not_documented"
            else:
                result["requires_non_thinking"] = True
    return result


def check_structured_output(
    base_url: str, model: str, structured_output: str, *,
    provider_options: dict[str, Any] | None = None, adapter: str = "openai-compatible",
) -> dict[str, Any]:
    """Reject documented incompatibilities without fallback or paid probes.

Unknown support does not certify a service, but permits explicit advanced
configuration. Local format and evidence validation remains mandatory.
    """
    capability = structured_output_capabilities(base_url, model, adapter=adapter)
    mode_status = capability["modes"].get(structured_output, "unknown")
    if mode_status == "unsupported":
        raise ConfigurationError(
            "DeepSeek Chat Completions does not support json_schema; use json_object. "
            "The Responses protocol requires a separate adapter."
        )
    if mode_status == "not_documented":
        raise ConfigurationError(
            "The selected Qwen model is not listed for JSON Schema on this endpoint; "
            "use json_object or configure a model with confirmed Schema support."
        )
    if structured_output != "off" and capability["requires_non_thinking"] and (provider_options or {}).get("enable_thinking") is True:
        raise ConfigurationError("The selected Qwen model requires enable_thinking=false for structured output.")
    return capability
