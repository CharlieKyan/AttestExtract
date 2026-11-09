"""Model adapters with bounded retries and an explicit offline replay mode."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from copy import deepcopy
from typing import Any, Literal

import httpx

from .capabilities import check_structured_output
from .connection_observation import ConnectionObservation
from .errors import BackendError, ConfigurationError, InvalidResponseError
from .diagnostics import RunTrace, redact_text
from .http_diagnostics import describe_http_error
from .models import BackendResponse, ExtractionRequest
from .schema_diagnostics import assess_schema_compliance, schema_request_info
from .transport import (bounded_timeout, connection_signature, safe_to_retry, timeout_phase,
                        tls_context, tls_fallback_reason, transport_details)

_RETRYABLE_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})
_RESERVED_OPTIONS = frozenset({
    "model", "messages", "response_format", "stream", "stream_options", "n",
    "tools", "tool_choice", "functions", "function_call", "max_tokens",
    "max_completion_tokens", "temperature", "api_key", "authorization", "headers", "base_url",
})


def _validate_provider_options(options: dict[str, Any] | None) -> dict[str, Any]:
    if options is None:
        return {}
    if not isinstance(options, dict) or any(
        not isinstance(key, str) or not key.strip() or key.lower() in _RESERVED_OPTIONS
        for key in options
    ):
        raise ConfigurationError("provider_options must use nonempty, non-reserved string keys.")
    try:
        # Round-tripping also rejects non-JSON data without printing private values.
        encoded = json.dumps(options, allow_nan=False)
        normalized = json.loads(encoded)
    except (ValueError, TypeError, RecursionError):
        raise ConfigurationError("provider_options must contain finite JSON values.") from None
    if normalized != options:
        raise ConfigurationError("provider_options must contain JSON objects with string keys and JSON values.")
    return deepcopy(options)


def _http_error_code(status: int) -> str:
    if status in (400, 422):
        return "invalid_request"
    if status == 401:
        return "authentication_error"
    if status == 402:
        return "billing_error"
    if status == 403:
        return "permission_denied"
    if status == 404:
        return "not_found"
    if status == 408:
        return "timeout"
    if status == 429:
        return "rate_limited"
    return "server_error" if status >= 500 else "http_error"


class OpenAICompatibleBackend:
    """Call a non-streaming OpenAI-compatible chat-completions endpoint.

    ``base_url`` includes the API prefix, such as ``https://host.example/v1``.
    A request makes at most ``max_attempts`` attempts, each with an HTTPX
    bounded phase timeouts. Only safe connection failures and designated
    transient HTTP statuses are retried; uncertain POST outcomes stop. Raw bodies and endpoint URLs are
    omitted; bounded, redacted server error fields are available separately.
    """

    name = "openai-compatible"
    endpoint_path = "/chat/completions"
    output_token_parameters = ("max_tokens", "max_completion_tokens")
    format_parameter = "response_format"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float | None = None,
        max_attempts: int = 1,
        transport: httpx.BaseTransport | None = None,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        trace: RunTrace | None = None,
        structured_output: Literal["off", "json_object", "json_schema"] = "off",
        provider_options: dict[str, Any] | None = None,
        output_token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens",
        tls_mode: Literal["auto", "1.2", "auto_fallback", "tls12_fallback"] = "tls12_fallback",
        read_timeout: float | None = None,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ConfigurationError("A nonempty HTTP(S) API base URL is required.")
        try:
            url = httpx.URL(base_url)
        except (httpx.InvalidURL, ValueError):
            raise ConfigurationError("The API base URL is invalid.") from None
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
            or "?" in base_url
            or "#" in base_url
            or any(character.isspace() for character in base_url)
        ):
            raise ConfigurationError(
                "The API base URL must use HTTP(S) and contain no credentials, query, or fragment."
            )
        if not isinstance(model, str) or not model.strip():
            raise ConfigurationError("A nonempty model name is required.")
        if api_key is not None and (
            not isinstance(api_key, str)
            or not api_key.strip()
            or "\r" in api_key
            or "\n" in api_key
            or not api_key.isascii()
        ):
            raise ConfigurationError("The API key must be a nonempty ASCII string without newlines.")
        self.effective_timeout = bounded_timeout(timeout, read_timeout)
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or max_attempts < 1
        ):
            raise ConfigurationError("max_attempts must be a positive integer.")
        if not callable(sleep_fn):
            raise ConfigurationError("sleep_fn must be callable.")
        if structured_output not in ("off", "json_object", "json_schema"):
            raise ConfigurationError("structured_output must be 'off', 'json_object', or 'json_schema'.")
        if output_token_parameter not in self.output_token_parameters:
            raise ConfigurationError(
                "output_token_parameter must be one of: " + ", ".join(self.output_token_parameters) + "."
            )
        if tls_mode not in ("auto", "1.2", "auto_fallback", "tls12_fallback"):
            raise ConfigurationError("tls_mode must be 'auto', '1.2', 'auto_fallback', or 'tls12_fallback'.")

        self.base_url = str(url).rstrip("/")
        self.model = model
        self.timeout = float(timeout) if timeout is not None else None
        self.read_timeout = float(read_timeout) if read_timeout is not None else None
        self.max_attempts = max_attempts
        self.structured_output = structured_output
        self.provider_options = _validate_provider_options(provider_options)
        check_structured_output(self.base_url, self.model, self.structured_output,
                                provider_options=self.provider_options, adapter=self.name)
        self.output_token_parameter = output_token_parameter
        self.tls_mode = tls_mode
        self._api_key = api_key
        self._transport = transport
        self._sleep = sleep_fn
        self._trace = trace
        self.effective_tls_mode: str | None = None
        self.probe_requests = 0
        self.last_probe_requests = 0
        self.last_connection_observations: list[dict[str, Any]] = []
        self._tls_selection: tuple[tuple, str] | None = None
        if trace is not None and api_key:
            trace.add_secrets(api_key, api_key.strip())

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(model={self.model!r}, "
            f"timeout={self.timeout!r}, max_attempts={self.max_attempts!r}, "
            f"structured_output={self.structured_output!r}, "
            "api_key=<redacted>)"
        )

    def prepare_payload(self, request: ExtractionRequest) -> dict[str, Any]:
        """Build the HTTP JSON body without I/O or mutating the request.

        Structured output is opt-in because compatibility layers may accept
        chat completions without implementing strict JSON Schema enforcement.
        Local response and evidence validation remains mandatory in either mode.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(message) for message in request.messages],
        }
        # Revalidate mutable options so later mutation cannot bypass reserved keys.
        payload.update(_validate_provider_options(self.provider_options))
        check_structured_output(self.base_url, self.model, self.structured_output,
                                provider_options=self.provider_options, adapter=self.name)
        if request.generation.temperature is not None:
            payload["temperature"] = request.generation.temperature
        if request.generation.max_output_tokens is not None:
            payload[self.output_token_parameter] = request.generation.max_output_tokens
        if self.structured_output == "json_object":
            payload["response_format"] = {"type": "json_object"}
        elif self.structured_output == "json_schema":
            if request.output_schema is None:
                raise ConfigurationError("json_schema structured output requires a request output_schema.")
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "attestextract_records",
                    "strict": True,
                    "schema": deepcopy(request.output_schema),
                },
            }
        return payload

    def _select_tls(self, request: ExtractionRequest, started: float) -> None:
        """Probe without credentials before a model POST; never replay a POST to switch TLS."""
        self.last_probe_requests = 0
        if self.tls_mode not in ("auto", "1.2", "auto_fallback", "tls12_fallback"):
            raise ConfigurationError("Unknown TLS policy.")
        if self._transport is not None:
            self._tls_selection = None
            self.effective_tls_mode = None
            return
        if httpx.URL(self.base_url).scheme != "https":
            self._tls_selection = None
            self.effective_tls_mode = "not_applicable"
            return
        if self.tls_mode not in ("auto_fallback", "tls12_fallback"):
            self._tls_selection = None
            self.effective_tls_mode = self.tls_mode
            return
        signature = connection_signature(self.base_url, self.tls_mode, self._transport)
        if self._tls_selection is not None and self._tls_selection[0] == signature:
            self.effective_tls_mode = self._tls_selection[1]
            return
        self._tls_selection = None
        modes = ("1.2", "auto") if self.tls_mode == "tls12_fallback" else ("auto", "1.2")
        fallback_reason = None
        for index, mode in enumerate(modes):
            self.effective_tls_mode = mode
            self.last_probe_requests += 1
            self.probe_requests += 1
            observation = ConnectionObservation(self.base_url + "/models", method="GET", custom_transport=False)
            try:
                # Separate short-lived client: probe cookies cannot enter a model request.
                with httpx.Client(timeout=5.0, verify=tls_context(mode), follow_redirects=False,
                                  event_hooks={"response": [observation.response]}) as probe:
                    with probe.stream("GET", self.base_url + "/models", extensions={"trace": observation.trace}) as response:
                        status = response.status_code  # Do not consume or log the response body.
            except httpx.TransportError as exc:
                observed = self._publish_connection(observation, request, self.last_probe_requests,
                                                    request_kind="tls_probe", request_status="transport_failure")
                diagnostic, artifact = self._transport_failure(exc, request, 0, started=started, preflight=True,
                                                              observation=observed)
                reason = tls_fallback_reason(exc, policy=self.tls_mode)
                if index == 0 and reason is not None:
                    fallback_reason = reason
                    continue
                raise BackendError(
                    "Connection probe failed before any model request was sent.", attempts=0,
                    code="timeout" if isinstance(exc, httpx.TimeoutException) else "transport_error",
                    retryable=False, http_diagnostics=diagnostic, http_details=artifact,
                ) from None
            except KeyboardInterrupt:
                self._publish_connection(observation, request, self.last_probe_requests,
                                         request_kind="tls_probe", request_status="interrupted")
                raise
            self._publish_connection(observation, request, self.last_probe_requests,
                                     request_kind="tls_probe", request_status="http_response")
            self._tls_selection = signature, mode
            if self._trace is not None:
                self._trace.event("transport", "tls_selected", chunk_id=request.chunk_id,
                                  configured_tls_mode=self.tls_mode, effective_tls_mode=mode,
                                  fallback=index > 0, fallback_reason=fallback_reason,
                                  probe_requests=self.last_probe_requests,
                                  probe_http_status=status, certificate_verification=True)
            return

    def generate(self, request: ExtractionRequest) -> BackendResponse:
        self.last_connection_observations = []
        payload = self.prepare_payload(request)
        context = {"chunk_id": request.chunk_id, "format_attempt": request.format_attempt}
        schema_info = schema_request_info(request.output_schema, self.structured_output)
        if self._trace is not None:
            self._trace.write_json(f"requests/{request.artifact_stem}.request.json", payload)
            schema_path = f"schema/{request.artifact_stem}.request.json"
            self._trace.write_json(schema_path, schema_info)
            self._trace.describe_artifact(schema_path, "schema_request_metadata")
        headers = {"Accept": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"
        started = time.perf_counter()
        self.effective_timeout = bounded_timeout(self.timeout, self.read_timeout)
        self._select_tls(request, started)
        with httpx.Client(
            timeout=self.effective_timeout,
            transport=self._transport,
            verify=tls_context(self.effective_tls_mode) if self.effective_tls_mode in ("auto", "1.2") else True,
            follow_redirects=False,
        ) as client:
            for attempt in range(1, self.max_attempts + 1):
                observation = ConnectionObservation(self.base_url + self.endpoint_path, method="POST",
                                                    custom_transport=self._transport is not None)
                client.event_hooks["response"] = [observation.response]
                if self._trace is not None:
                    self._trace.event("http", "started", **context, attempt=attempt,
                                      read_timeout=self.effective_timeout.read, effective_tls_mode=self.effective_tls_mode)
                try:
                    response = client.post(
                        self.base_url + self.endpoint_path,
                        headers=headers,
                        json=payload,
                        extensions={"trace": observation.trace},
                    )
                except httpx.TransportError as exc:
                    observed = self._publish_connection(observation, request, attempt,
                                                        request_kind="model_post", request_status="transport_failure")
                    diagnostic, artifact = self._transport_failure(exc, request, attempt, started=started,
                                                                  observation=observed)
                    retryable = safe_to_retry(exc)
                    timed_out = isinstance(exc, httpx.TimeoutException)
                    if attempt == self.max_attempts or not retryable:
                        message = (f"The model request timed out after {attempt} attempt(s)." if timed_out
                                   else f"The model request failed in transport after {attempt} attempt(s).")
                        if not retryable:
                            message += " The server may have processed the request; it was not automatically retried."
                        raise BackendError(
                            message,
                            attempts=attempt,
                            code="timeout" if timed_out else "transport_error", retryable=retryable,
                            http_diagnostics=diagnostic, http_details=artifact,
                        ) from None
                    if self._trace is not None:
                        self._trace.event("http", "retry", **context, attempt=attempt,
                                          reason="timeout" if timed_out else "transport")
                    self._pause(attempt)
                    continue
                except httpx.DecodingError:
                    self._publish_connection(observation, request, attempt,
                                             request_kind="model_post", request_status="decode_failure")
                    raise InvalidResponseError(
                        "The model endpoint returned an invalid encoded response.",
                        attempts=attempt,
                    ) from None
                except KeyboardInterrupt:
                    self._publish_connection(observation, request, attempt,
                                             request_kind="model_post", request_status="interrupted")
                    raise

                observation.body_complete = True
                self._publish_connection(observation, request, attempt,
                                         request_kind="model_post", request_status="http_response")
                if self._trace is not None:
                    self._trace.event("http", "completed", **context,
                                      attempt=attempt, status_code=response.status_code)
                if not 200 <= response.status_code < 300:
                    compatibility_hint = None
                    if self.structured_output != "off" and response.status_code in {400, 422}:
                        compatibility_hint = (
                            f"The endpoint may not support {self.format_parameter}/{self.structured_output} or the supplied schema;"
                            " no fallback was attempted."
                        )
                    diagnostic = describe_http_error(
                        response, chunk_id=request.chunk_id, attempt=attempt,
                        secrets=[self._api_key] if self._api_key else [],
                        compatibility_hint=compatibility_hint,
                    )
                    diagnostic["format_attempt"] = request.format_attempt
                    diagnostic["schema_request"] = schema_info
                    artifact = None
                    if self._trace is not None:
                        if self._trace.directory is not None:
                            artifact = f"http/{request.artifact_stem}.attempt-{attempt}.error.json"
                            self._trace.write_json(artifact, diagnostic)
                            self._trace.describe_artifact(artifact, "http_error_details")
                        self._trace.event("http", "error_reported", **context,
                                          attempt=attempt, status_code=response.status_code,
                                          diagnostic_status=diagnostic["diagnostic_status"], artifact=artifact)
                    if (
                        response.status_code in _RETRYABLE_STATUS_CODES
                        and attempt < self.max_attempts
                    ):
                        if self._trace is not None:
                            self._trace.event("http", "retry", **context,
                                              attempt=attempt, status_code=response.status_code)
                        self._pause(attempt)
                        continue
                    raise BackendError(
                        f"The model endpoint returned HTTP {response.status_code} "
                        f"after {attempt} attempt(s)." + (" " + compatibility_hint if compatibility_hint else ""),
                        status_code=response.status_code,
                        attempts=attempt,
                        http_diagnostics=diagnostic, http_details=artifact,
                        code=_http_error_code(response.status_code),
                        retryable=response.status_code in _RETRYABLE_STATUS_CODES,
                    )
                if self._trace is not None:
                    self._trace.event("model_response", "started", **context)
                return self._parse_response(response, attempt, started, **context,
                                            artifact_stem=request.artifact_stem,
                                            output_schema=request.output_schema)

        raise BackendError("The model request did not complete.")  # pragma: no cover

    def _publish_connection(self, observation: ConnectionObservation, request: ExtractionRequest,
                            attempt: int, *, request_kind: str, request_status: str) -> dict[str, Any]:
        observed = observation.snapshot(request_status=request_status)
        observed.update(request_kind=request_kind, attempt=attempt, configured_tls_mode=self.tls_mode,
                        effective_tls_mode=self.effective_tls_mode)
        self.last_connection_observations.append(observed)
        if self._trace is not None:
            suffix = f"probe-{attempt}" if request_kind == "tls_probe" else f"attempt-{attempt}"
            artifact = f"http/{request.artifact_stem}.{suffix}.connection.json"
            self._trace.write_json(artifact, observed)
            if self._trace.directory is not None:
                self._trace.describe_artifact(artifact, "connection_observation")
            self._trace.event("http", "connection_observed", chunk_id=request.chunk_id,
                              format_attempt=request.format_attempt, **observed)
        return observed

    def _transport_failure(self, exc: Exception, request: ExtractionRequest,
                           attempt: int, *, started: float, preflight: bool = False,
                           observation: dict[str, Any] | None = None) -> tuple[dict[str, Any], str | None]:
        self._tls_selection = None
        diagnostic = transport_details(exc, tls_mode=self.tls_mode, attempt=attempt)
        active_timeout = httpx.Timeout(5.0) if preflight else self.effective_timeout
        diagnostic.update(custom_transport=self._transport is not None,
                          effective_tls_mode=self.effective_tls_mode,
                          phase="preflight" if preflight else timeout_phase(exc),
                          timeout=active_timeout.as_dict(), elapsed_seconds=time.perf_counter() - started,
                          probe_requests=self.last_probe_requests, model_post_attempts=attempt,
                          request_outcome="not_sent" if preflight or safe_to_retry(exc) else "unknown",
                          automatic_retry_allowed=False if preflight else safe_to_retry(exc))
        if observation is not None:
            diagnostic["connection_observation"] = observation
        if self._transport is not None or self.effective_tls_mode == "not_applicable":
            diagnostic["certificate_verification"] = None  # Controlled by the injected transport.
        artifact = None
        if self._trace is not None and self._trace.directory is not None:
            suffix = f"probe-{self.last_probe_requests}" if preflight else f"attempt-{attempt}"
            artifact = f"http/{request.artifact_stem}.{suffix}.transport.json"
            self._trace.write_json(artifact, diagnostic)
            self._trace.describe_artifact(artifact, "transport_error_details")
        if self._trace is not None:
            self._trace.event("http", "transport_failed", chunk_id=request.chunk_id,
                              format_attempt=request.format_attempt, attempt=attempt,
                              phase=diagnostic["phase"], request_outcome=diagnostic["request_outcome"],
                              automatic_retry_allowed=diagnostic["automatic_retry_allowed"],
                              probe_requests=self.last_probe_requests, model_post_attempts=attempt,
                              read_timeout=active_timeout.read, elapsed_seconds=diagnostic["elapsed_seconds"])
        return diagnostic, artifact

    def _pause(self, attempt: int) -> None:
        self._sleep(min(0.5 * (2 ** min(attempt - 1, 3)), 2.0))

    def _parse_response(
        self, response: httpx.Response, attempt: int, started: float,
        *, chunk_id: str | None = None, format_attempt: int = 1,
        artifact_stem: str | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> BackendResponse:
        try:
            payload = response.json()
        except (ValueError, UnicodeError, RecursionError):
            raise InvalidResponseError(
                "The model endpoint did not return valid JSON.", attempts=attempt
            ) from None
        if not isinstance(payload, dict):
            raise InvalidResponseError(
                "The model endpoint returned an invalid response object.", attempts=attempt
            )
        choices = payload.get("choices")
        if (
            not isinstance(choices, list)
            or not choices
            or not isinstance(choices[0], dict)
        ):
            raise InvalidResponseError(
                "The model response has no valid completion choice.", attempts=attempt
            )
        choice = choices[0]
        # Preserve model text even when finish_reason or message validation
        # subsequently rejects a truncated/refused completion. Never save an
        # HTTP error body or headers here.
        diagnostic_message = choice.get("message")
        if self._trace is not None and chunk_id is not None and isinstance(diagnostic_message, dict):
            diagnostic_content = diagnostic_message.get("content")
            if isinstance(diagnostic_content, str):
                self._trace.write_text(f"responses/{artifact_stem or chunk_id}.txt", diagnostic_content)
                compliance = assess_schema_compliance(diagnostic_content, output_schema, self.structured_output)
                compliance.update(http_attempt=attempt, format_attempt=format_attempt)
                artifact = f"schema/{artifact_stem or chunk_id}.compliance.json"
                self._trace.write_json(artifact, compliance)
                self._trace.describe_artifact(artifact, "observed_schema_compliance")
                self._trace.event("schema_compliance", "checked", chunk_id=chunk_id,
                                  format_attempt=format_attempt, status=compliance["status"], artifact=artifact)
        usage = payload.get("usage")
        safe_usage = (
            {
                key: value
                for key, value in usage.items()
                if isinstance(key, str)
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            }
            if isinstance(usage, dict)
            else {}
        )
        response_model = payload.get("model")
        reported_model = response_model if isinstance(response_model, str) and response_model.strip() else None
        request_id = None
        for header in ("x-request-id", "request-id", "x-correlation-id", "x-goog-request-id"):
            identifier = response.headers.get(header)
            if identifier and identifier.strip():
                identifier = redact_text(identifier, [self._api_key, self._api_key.strip()] if self._api_key else [])
                identifier = re.sub(r"https?://\S+", "[URL REDACTED]", identifier, flags=re.IGNORECASE)
                identifier = re.sub(r"\bBearer\s+\S+", "Bearer [REDACTED]", identifier, flags=re.IGNORECASE)
                request_id = " ".join("".join(ch if ch.isprintable() else " " for ch in identifier).split())[:256]
                break
        finish_reason = choice.get("finish_reason")
        metadata = {
            "model": reported_model or self.model, "attempts": attempt, "usage": safe_usage,
            "elapsed_seconds": time.perf_counter() - started,
            "finish_reason": finish_reason if isinstance(finish_reason, str) else None,
            "request_id": request_id, "reported_model": reported_model,
        }
        if self._trace is not None and chunk_id is not None:
            self._trace.write_json(f"responses/{artifact_stem or chunk_id}.meta.json",
                                   {**metadata, "format_attempt": format_attempt})
        if self._trace is not None:
            self._trace.event(
                "model_response", "completion_status", chunk_id=chunk_id,
                format_attempt=format_attempt,
                finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            )
        if finish_reason is not None and finish_reason != "stop":
            code = "unsupported_response"
            if finish_reason == "length":
                code = "truncated_response"
            elif finish_reason == "content_filter":
                code = "refusal"
            raise InvalidResponseError(
                ("The model completion was truncated (finish_reason=length); the partial response was retained, not accepted."
                 if code == "truncated_response" else "The model completion was refused or did not finish normally."),
                attempts=attempt, code=code, status_code=response.status_code, response_metadata=metadata,
            )
        message = choice.get("message")
        if not isinstance(message, dict):
            raise InvalidResponseError(
                "The model response has no valid assistant message.", attempts=attempt,
                status_code=response.status_code, response_metadata=metadata,
            )
        if message.get("refusal"):
            raise InvalidResponseError(
                "The model response contains a refusal or an unsupported tool call.",
                attempts=attempt, code="refusal",
                status_code=response.status_code, response_metadata=metadata,
            )
        if message.get("tool_calls") or message.get("function_call"):
            raise InvalidResponseError(
                "The model response contains a refusal or an unsupported tool call.",
                attempts=attempt, code="unsupported_response",
                status_code=response.status_code, response_metadata=metadata,
            )
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise InvalidResponseError(
                "The model response has no nonempty text content.", attempts=attempt,
                code="empty_response" if content is None or isinstance(content, str) else "unsupported_response",
                status_code=response.status_code, response_metadata=metadata,
            )

        return BackendResponse(
            text=content,
            model=reported_model or self.model,
            usage=safe_usage,
            attempts=attempt,
            elapsed_seconds=time.perf_counter() - started,
            finish_reason=finish_reason, request_id=request_id, reported_model=reported_model,
        )


class ReplayBackend:
    """Consume fixed completions, for demos and tests without network access.

    A dictionary is serialized as completion text. It is not interpreted as
    an HTTP response envelope. This adapter never generates new extractions.
    """

    name = "replay"
    mode = "replay"

    def __init__(self, responses: Sequence[str | dict[str, Any]], *,
                 output_protocol: Literal["flat-v1", "native-v1"] = "flat-v1") -> None:
        if output_protocol not in ("flat-v1", "native-v1"):
            raise ConfigurationError("Replay output_protocol must be flat-v1 or native-v1.")
        self.output_protocol = output_protocol
        if isinstance(responses, (str, bytes)) or not isinstance(responses, Sequence):
            raise ConfigurationError("Replay responses must be a sequence of strings or objects.")
        self._responses = list(responses)
        if any(not isinstance(item, (str, dict)) for item in self._responses):
            raise ConfigurationError("Each replay response must be a string or an object.")
        self._position = 0

    def generate(self, request: ExtractionRequest) -> BackendResponse:
        if self._position >= len(self._responses):
            raise BackendError("The replay backend has no remaining responses.")
        response = self._responses[self._position]
        self._position += 1
        try:
            text = (
                response
                if isinstance(response, str)
                else json.dumps(response, ensure_ascii=False, allow_nan=False)
            )
        except (TypeError, ValueError):
            raise InvalidResponseError("The replay response cannot be serialized as JSON.") from None
        return BackendResponse(text=text, model="replay")
