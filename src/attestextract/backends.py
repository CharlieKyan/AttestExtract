"""Model adapters with bounded retries and an explicit offline replay mode."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Sequence
from copy import deepcopy
from typing import Any, Literal

import httpx

from .errors import BackendError, ConfigurationError, InvalidResponseError
from .diagnostics import RunTrace
from .http_diagnostics import describe_http_error
from .models import BackendResponse, ExtractionRequest

_RETRYABLE_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})


class OpenAICompatibleBackend:
    """Call a non-streaming OpenAI-compatible chat-completions endpoint.

    ``base_url`` includes the API prefix, such as ``https://host.example/v1``.
    A request makes at most ``max_attempts`` attempts, each with an HTTPX
    timeout of at most 60 seconds. Only transport failures and designated
    transient HTTP statuses are retried. Raw bodies and endpoint URLs are
    omitted; bounded, redacted server error fields are available separately.
    """

    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 60,
        max_attempts: int = 3,
        transport: httpx.BaseTransport | None = None,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        trace: RunTrace | None = None,
        structured_output: Literal["off", "json_schema"] = "off",
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
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 60
        ):
            raise ConfigurationError("The timeout must be greater than 0 and at most 60 seconds.")
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or max_attempts < 1
        ):
            raise ConfigurationError("max_attempts must be a positive integer.")
        if not callable(sleep_fn):
            raise ConfigurationError("sleep_fn must be callable.")
        if structured_output not in ("off", "json_schema"):
            raise ConfigurationError("structured_output must be 'off' or 'json_schema'.")

        self.base_url = str(url).rstrip("/")
        self.model = model
        self.timeout = float(timeout)
        self.max_attempts = max_attempts
        self.structured_output = structured_output
        self._api_key = api_key
        self._transport = transport
        self._sleep = sleep_fn
        self._trace = trace
        if trace is not None and api_key:
            trace.add_secrets(api_key)

    def __repr__(self) -> str:
        return (
            f"OpenAICompatibleBackend(model={self.model!r}, "
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
        if self.structured_output == "json_schema":
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

    def generate(self, request: ExtractionRequest) -> BackendResponse:
        payload = self.prepare_payload(request)
        context = {"chunk_id": request.chunk_id, "format_attempt": request.format_attempt}
        if self._trace is not None:
            self._trace.write_json(f"requests/{request.artifact_stem}.request.json", payload)
        headers = {"Accept": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"
        started = time.perf_counter()
        with httpx.Client(
            timeout=self.timeout,
            transport=self._transport,
            follow_redirects=False,
        ) as client:
            for attempt in range(1, self.max_attempts + 1):
                if self._trace is not None:
                    self._trace.event("http", "started", **context, attempt=attempt)
                try:
                    response = client.post(
                        self.base_url + "/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                except httpx.TimeoutException:
                    if attempt == self.max_attempts:
                        raise BackendError(
                            f"The model request timed out after {attempt} attempt(s).",
                            attempts=attempt,
                        ) from None
                    if self._trace is not None:
                        self._trace.event("http", "retry", **context, attempt=attempt, reason="timeout")
                    self._pause(attempt)
                    continue
                except httpx.TransportError:
                    if attempt == self.max_attempts:
                        raise BackendError(
                            f"The model request failed in transport after {attempt} attempt(s).",
                            attempts=attempt,
                        ) from None
                    if self._trace is not None:
                        self._trace.event("http", "retry", **context, attempt=attempt, reason="transport")
                    self._pause(attempt)
                    continue
                except httpx.DecodingError:
                    raise InvalidResponseError(
                        "The model endpoint returned an invalid encoded response.",
                        attempts=attempt,
                    ) from None

                if self._trace is not None:
                    self._trace.event("http", "completed", **context,
                                      attempt=attempt, status_code=response.status_code)
                if not 200 <= response.status_code < 300:
                    compatibility_hint = None
                    if self.structured_output == "json_schema" and response.status_code in {400, 422}:
                        compatibility_hint = (
                            "The endpoint may not support response_format/json_schema or the supplied schema;"
                            " no fallback was attempted."
                        )
                    diagnostic = describe_http_error(
                        response, chunk_id=request.chunk_id, attempt=attempt,
                        secrets=[self._api_key] if self._api_key else [],
                        compatibility_hint=compatibility_hint,
                    )
                    diagnostic["format_attempt"] = request.format_attempt
                    artifact = None
                    if self._trace is not None:
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
                    )
                if self._trace is not None:
                    self._trace.event("model_response", "started", **context)
                return self._parse_response(response, attempt, started, **context,
                                            artifact_stem=request.artifact_stem)

        raise BackendError("The model request did not complete.")  # pragma: no cover

    def _pause(self, attempt: int) -> None:
        self._sleep(min(0.5 * (2 ** min(attempt - 1, 3)), 2.0))

    def _parse_response(
        self, response: httpx.Response, attempt: int, started: float,
        *, chunk_id: str | None = None, format_attempt: int = 1,
        artifact_stem: str | None = None,
    ) -> BackendResponse:
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
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
        finish_reason = choice.get("finish_reason")
        if self._trace is not None:
            self._trace.event(
                "model_response", "completion_status", chunk_id=chunk_id,
                format_attempt=format_attempt,
                finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            )
        if finish_reason is not None and finish_reason != "stop":
            raise InvalidResponseError(
                "The model completion was truncated, refused, or did not finish normally.",
                attempts=attempt,
            )
        message = choice.get("message")
        if not isinstance(message, dict):
            raise InvalidResponseError(
                "The model response has no valid assistant message.", attempts=attempt
            )
        if message.get("refusal") or message.get("tool_calls") or message.get("function_call"):
            raise InvalidResponseError(
                "The model response contains a refusal or an unsupported tool call.",
                attempts=attempt,
            )
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise InvalidResponseError(
                "The model response has no nonempty text content.", attempts=attempt
            )

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
        return BackendResponse(
            text=content,
            model=response_model if isinstance(response_model, str) and response_model else self.model,
            usage=safe_usage,
            attempts=attempt,
            elapsed_seconds=time.perf_counter() - started,
        )


class ReplayBackend:
    """Consume fixed completions, for demos and tests without network access.

    A dictionary is serialized as completion text. It is not interpreted as
    an HTTP response envelope. This adapter never generates new extractions.
    """

    name = "replay"

    def __init__(self, responses: Sequence[str | dict[str, Any]]) -> None:
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
