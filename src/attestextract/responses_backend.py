"""Explicit, non-streaming DeepSeek Responses protocol adapter.

Only the official service and its documented text request subset are supported.
HTTP handling is shared with the Chat adapter; there is no protocol fallback.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from copy import deepcopy
from typing import Any, Literal

import httpx

from .backends import OpenAICompatibleBackend, _validate_provider_options
from .capabilities import check_structured_output
from .diagnostics import RunTrace, redact_text
from .errors import ConfigurationError, InvalidResponseError
from .models import BackendResponse, ExtractionRequest
from .schema_diagnostics import assess_schema_compliance


def _validate_options(options: dict[str, Any] | None) -> dict[str, Any]:
    options = _validate_provider_options(options)
    if options.keys() - {"reasoning", "top_p", "top_logprobs", "user"}:
        raise ConfigurationError(
            "DeepSeek Responses provider_options supports only reasoning.effort, top_p, top_logprobs and user."
        )
    if "reasoning" in options:
        reasoning = options["reasoning"]
        if (not isinstance(reasoning, dict) or set(reasoning) != {"effort"}
                or reasoning["effort"] not in ("none", "low", "high", "max", "minimal", "medium", "xhigh")):
            raise ConfigurationError("DeepSeek Responses reasoning requires a documented effort value; use 'none' to disable thinking.")
    if "top_p" in options:
        value = options["top_p"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ConfigurationError("DeepSeek Responses top_p must be between 0 and 1.")
    if "top_logprobs" in options:
        value = options["top_logprobs"]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 20:
            raise ConfigurationError("DeepSeek Responses top_logprobs must be an integer between 0 and 20.")
    if "user" in options:
        value = options["user"]
        if not isinstance(value, str) or len(value.encode("utf-8", errors="replace")) > 512:
            raise ConfigurationError("DeepSeek Responses user must be a string of at most 512 UTF-8 bytes.")
    return options


def _validate_endpoint(base_url: str) -> None:
    if not isinstance(base_url, str):
        raise ConfigurationError("DeepSeek Responses requires the official HTTPS API base URL.")
    try:
        url = httpx.URL(base_url)
    except (httpx.InvalidURL, ValueError, TypeError):
        raise ConfigurationError("DeepSeek Responses requires the official HTTPS API base URL.") from None
    if (url.scheme != "https" or url.host != "api.deepseek.com"
            or url.port not in (None, 443) or url.path.rstrip("/") not in ("", "/v1")
            or url.userinfo or url.query or url.fragment
            or "?" in base_url or "#" in base_url or any(char.isspace() for char in base_url)):
        raise ConfigurationError("DeepSeek Responses requires https://api.deepseek.com with an optional /v1 prefix.")


def _output_text(output: Any) -> tuple[str, str | None]:
    """Collect only verbatim assistant text, also flagging unsupported output.

    Keep available text for diagnosis even if another item is invalid. Never
    include reasoning, refusals or tool arguments in the extraction text.
    """
    if not isinstance(output, list):
        return "", "unsupported_response"
    pieces = []
    error = None
    for item in output:
        if not isinstance(item, dict):
            error = error or "unsupported_response"
            continue
        if item.get("status") not in (None, "completed"):
            error = error or "unsupported_response"
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            error = error or "unsupported_response"
            continue
        content = item.get("content")
        if not isinstance(content, list):
            error = error or "unsupported_response"
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "refusal":
                error = "refusal"
            elif isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                pieces.append(part["text"])
            else:
                error = error or "unsupported_response"
    return "".join(pieces), error


def _usage(usage: Any) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {}
    result = {}
    for source, target in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"), ("total_tokens", "total_tokens")):
        value = usage.get(source)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[target] = value
    return result


def _safe_identifier(value: Any, api_key: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = redact_text(value, [api_key, api_key.strip()] if api_key else [])
    value = re.sub(r"https?://\S+", "[URL REDACTED]", value, flags=re.IGNORECASE)
    value = re.sub(r"\bBearer\s+\S+", "Bearer [REDACTED]", value, flags=re.IGNORECASE)
    return " ".join("".join(ch if ch.isprintable() else " " for ch in value).split())[:256]


class DeepSeekResponsesBackend(OpenAICompatibleBackend):
    """Translate unified extraction requests to DeepSeek's Responses API.

    Schema mode uses the documented ``text.format`` object, without an
    undocumented strict flag. Local JSON/schema/evidence checks still apply.
    Tools, streaming, stateful requests and unsupported parameters are rejected.
    """

    name = "deepseek-responses"
    endpoint_path = "/responses"
    output_token_parameters = ("max_output_tokens",)
    format_parameter = "text.format"

    def __init__(
        self, base_url: str, model: str, api_key: str | None = None,
        timeout: float | None = None, max_attempts: int = 1,
        transport: httpx.BaseTransport | None = None, *,
        sleep_fn: Callable[[float], None] = time.sleep, trace: RunTrace | None = None,
        structured_output: Literal["off", "json_object", "json_schema"] = "json_schema",
        provider_options: dict[str, Any] | None = None,
        output_token_parameter: Literal["max_output_tokens"] = "max_output_tokens",
        tls_mode: Literal["auto", "1.2", "auto_fallback"] = "auto",
        read_timeout: float | None = None,
    ) -> None:
        _validate_endpoint(base_url)
        super().__init__(
            base_url, model, api_key, timeout, max_attempts, transport,
            sleep_fn=sleep_fn, trace=trace, structured_output=structured_output,
            provider_options=_validate_options(provider_options),
            output_token_parameter=output_token_parameter, tls_mode=tls_mode, read_timeout=read_timeout,
        )

    def prepare_payload(self, request: ExtractionRequest) -> dict[str, Any]:
        _validate_endpoint(self.base_url)
        if self.output_token_parameter != "max_output_tokens":
            raise ConfigurationError("DeepSeek Responses requires output_token_parameter='max_output_tokens'.")
        if self.structured_output not in ("off", "json_object", "json_schema"):
            raise ConfigurationError("Unknown DeepSeek Responses output mode.")
        options = _validate_options(self.provider_options)
        check_structured_output(self.base_url, self.model, self.structured_output,
                                provider_options=options, adapter=self.name)
        if not request.messages or any(
            set(message) != {"role", "content"}
            or message["role"] not in ("system", "user", "assistant", "developer")
            for message in request.messages
        ):
            raise ConfigurationError("DeepSeek Responses requires nonempty text messages with supported roles and content only.")
        payload: dict[str, Any] = {
            "model": self.model,
            "input": [{"type": "message", **message} for message in request.messages],
            **options,
        }
        if request.generation.temperature is not None:
            payload["temperature"] = request.generation.temperature
        if request.generation.max_output_tokens is not None:
            payload["max_output_tokens"] = request.generation.max_output_tokens
        if self.structured_output == "json_schema":
            if request.output_schema is None:
                raise ConfigurationError("json_schema structured output requires a request output_schema.")
            payload["text"] = {"format": {
                "type": "json_schema", "name": "attestextract_records",
                "schema": deepcopy(request.output_schema),
            }}
        elif self.structured_output == "json_object":
            payload["text"] = {"format": {"type": "json_object"}}
        return payload

    def _parse_response(
        self, response: httpx.Response, attempt: int, started: float, *,
        chunk_id: str | None = None, format_attempt: int = 1,
        artifact_stem: str | None = None, output_schema: dict[str, Any] | None = None,
    ) -> BackendResponse:
        try:
            payload = response.json()
        except (ValueError, UnicodeError, RecursionError):
            raise InvalidResponseError("The Responses endpoint did not return valid JSON.",
                                       attempts=attempt, status_code=response.status_code) from None
        if not isinstance(payload, dict):
            raise InvalidResponseError("The Responses endpoint returned an invalid response object.",
                                       attempts=attempt, status_code=response.status_code)
        content, output_error = _output_text(payload.get("output"))
        status = payload.get("status")
        details = payload.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        finish_reason = "stop" if status == "completed" else None
        if status == "incomplete":
            finish_reason = {"max_output_tokens": "length", "content_filter": "content_filter"}.get(reason) if isinstance(reason, str) else None
        reported = payload.get("model")
        reported = reported if isinstance(reported, str) and reported.strip() else None
        request_id = None
        for header in ("x-request-id", "request-id", "x-correlation-id"):
            request_id = _safe_identifier(response.headers.get(header), self._api_key)
            if request_id:
                break
        metadata = {
            "model": reported or self.model, "reported_model": reported,
            "attempts": attempt, "usage": _usage(payload.get("usage")),
            "elapsed_seconds": time.perf_counter() - started,
            "finish_reason": finish_reason, "request_id": request_id,
        }
        if self._trace is not None:
            if chunk_id is not None:
                stem = artifact_stem or chunk_id
                self._trace.write_text(f"responses/{stem}.txt", content)
                self._trace.write_json(f"responses/{stem}.meta.json", {
                    **metadata, "format_attempt": format_attempt,
                })
                # The pipeline owns the unified .meta.json; retain protocol-only
                # fields separately so successful processing cannot replace them.
                protocol_path = f"responses/{stem}.protocol.json"
                self._trace.write_json(protocol_path, {
                    "protocol": "responses", "format_attempt": format_attempt,
                    "response_id": _safe_identifier(payload.get("id"), self._api_key),
                    "response_status": status if status in ("completed", "incomplete", "failed", "in_progress") else "unknown",
                    "incomplete_reason": reason if reason in ("max_output_tokens", "content_filter") else None,
                })
                self._trace.describe_artifact(protocol_path, "response_protocol_metadata")
                compliance = assess_schema_compliance(content, output_schema, self.structured_output)
                compliance.update(http_attempt=attempt, format_attempt=format_attempt)
                artifact = f"schema/{stem}.compliance.json"
                self._trace.write_json(artifact, compliance)
                self._trace.describe_artifact(artifact, "observed_schema_compliance")
                self._trace.event("schema_compliance", "checked", chunk_id=chunk_id,
                                  format_attempt=format_attempt, status=compliance["status"], artifact=artifact)
            self._trace.event("model_response", "completion_status", chunk_id=chunk_id,
                              format_attempt=format_attempt, finish_reason=finish_reason)
        error = None
        if finish_reason == "length":
            error = "truncated_response"
        elif finish_reason == "content_filter":
            error = "refusal"
        elif status == "failed" or payload.get("error") is not None:
            error = "response_failed"
        elif status != "completed" or payload.get("object") != "response" or details is not None:
            error = "unsupported_response"
        else:
            error = output_error or ("empty_response" if not content.strip() else None)
        if error is not None:
            messages = {
                "truncated_response": "The Responses output reached max_output_tokens; partial text was retained, not accepted.",
                "refusal": "The Responses output was refused or filtered; no extraction was accepted.",
                "response_failed": "The Responses endpoint reported a failed response; no extraction was accepted.",
                "unsupported_response": "The Responses output contains unsupported items or did not complete normally.",
                "empty_response": "The Responses output has no nonempty assistant text.",
            }
            raise InvalidResponseError(messages[error], code=error, attempts=attempt,
                                       status_code=response.status_code, response_metadata=metadata)
        return BackendResponse(text=content, **metadata)
