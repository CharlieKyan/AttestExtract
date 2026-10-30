"""A deliberately synthetic second protocol; all HTTP stays in MockTransport.

This example demonstrates request/response conversion, not another real provider
integration. Fixed envelopes never provide evidence of model quality.
"""

from __future__ import annotations

from copy import deepcopy
import json
import time
from collections.abc import Sequence
from typing import Any

import httpx

from .diagnostics import RunTrace
from .errors import BackendError, ConfigurationError, InvalidResponseError
from .models import BackendResponse, ExtractionRequest


class SyntheticEnvelopeBackend:
    """Map ``deployment/input`` and ``output/metrics/status`` without a network."""

    name = "synthetic-envelope"
    mode = "replay"

    def __init__(
        self, envelopes: Sequence[dict[str, Any]], *, model: str = "synthetic-deployment",
        trace: RunTrace | None = None,
    ) -> None:
        if isinstance(envelopes, (str, bytes)) or not isinstance(envelopes, Sequence):
            raise ConfigurationError("Synthetic envelopes must be a sequence of objects.")
        if any(not isinstance(envelope, dict) for envelope in envelopes):
            raise ConfigurationError("Each synthetic envelope must be an object.")
        self._envelopes = deepcopy(list(envelopes))
        self.model = model
        self.trace = trace
        self.requests: list[dict[str, Any]] = []
        self._transport = httpx.MockTransport(self._respond)

    def prepare_payload(self, request: ExtractionRequest) -> dict[str, Any]:
        """Pure preview: repeated calls neither send HTTP nor consume an envelope."""
        if [message.get("role") for message in request.messages] != ["system", "user"]:
            raise ConfigurationError("Synthetic protocol requires one system and one user message.")
        payload: dict[str, Any] = {
            "deployment": self.model,
            "input": {
                "system": request.messages[0]["content"],
                "prompt": request.messages[1]["content"],
            },
        }
        parameters = {}
        if request.generation.temperature is not None:
            parameters["temperature"] = request.generation.temperature
        if request.generation.max_output_tokens is not None:
            parameters["output_limit"] = request.generation.max_output_tokens
        if parameters:
            payload["parameters"] = parameters
        return payload

    def _respond(self, request: httpx.Request) -> httpx.Response:
        if not self._envelopes:
            raise BackendError("The synthetic adapter has no remaining envelopes.", code="provider_error")
        self.requests.append(json.loads(request.content))
        return httpx.Response(200, json=self._envelopes.pop(0))

    def generate(self, request: ExtractionRequest) -> BackendResponse:
        started = time.perf_counter()
        payload = self.prepare_payload(request)
        with httpx.Client(transport=self._transport) as client:
            response = client.post("https://synthetic.invalid/generate", json=payload)
        native = response.json()
        status = native.get("status")
        output = native.get("output")
        text = output.get("text") if isinstance(output, dict) else None
        if self.trace is not None:
            if isinstance(text, str):
                self.trace.write_text(f"responses/{request.artifact_stem}.txt", text)
            self.trace.event("model_response", "synthetic_envelope", chunk_id=request.chunk_id,
                             completed=status == "completed", real_endpoint_calls=0)
        if status == "error":
            # A provider's error text can contain credentials or private input.
            raise BackendError("Synthetic provider reported a native error.", code="provider_error")
        if status == "incomplete":
            raise InvalidResponseError("Synthetic provider returned incomplete output.", code="truncated_response")
        if status != "completed":
            raise InvalidResponseError("Synthetic provider returned an unknown completion status.")
        if not isinstance(text, str):
            raise InvalidResponseError("Synthetic provider did not return text output.")
        if not text.strip():
            raise InvalidResponseError("Synthetic provider returned empty text.", code="empty_response")
        metrics = native.get("metrics")
        usage = {}
        if isinstance(metrics, dict):
            for source, target in (("input_tokens", "prompt_tokens"),
                                   ("output_tokens", "completion_tokens"),
                                   ("total_tokens", "total_tokens")):
                value = metrics.get(source)
                if type(value) is int and value >= 0:
                    usage[target] = value
        reported_model = native.get("deployment")
        if not isinstance(reported_model, str) or not reported_model.strip():
            reported_model = None
        request_id = native.get("id")
        if not isinstance(request_id, str) or not request_id.strip():
            request_id = None
        return BackendResponse(
            text=text, model=reported_model or self.model, reported_model=reported_model,
            request_id=request_id, finish_reason="stop", usage=usage,
            attempts=1, elapsed_seconds=time.perf_counter() - started,
        )


def synthetic_factory(envelopes: Sequence[dict[str, Any]]):
    """Return an explicit registry factory; no import paths are read from config.

    Each construction gets its own copy of the fixed responses. Connection and
    credential settings do not cause network access in this synthetic example.
    """
    def create(config, *, api_key=None, trace=None):
        if config.structured_output != "off" or config.provider_options:
            raise ConfigurationError("Synthetic adapter supports off mode without provider options.")
        return SyntheticEnvelopeBackend(envelopes, model=config.model, trace=trace)

    return create
