"""Execution-layer reservations, provenance and recovery for automatic routes.

No network calls are made here. Each reservation authorizes exactly one call of
one concrete backend; the pipeline still parses and grounds every response.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import Field

from .errors import BackendError, ConfigurationError, InvalidResponseError
from .execution import ENGINE_REVISION, ExecutionSession, _State, _FormatFeedback, digest
from .models import (BackendResponse, ChunkProvenance, ContextOptions, ExecutionOptions,
                     GenerationAttempt, GenerationOptions, TaskSpec)
from .planning import plan_chunks


class AutomaticState(_State):
    checkpoint_version: Literal["4"] = "4"
    route_plan: dict[str, Any]
    generation_attempts: list[GenerationAttempt] = Field(default_factory=list)
    chunk_routes: dict[str, str] = Field(default_factory=dict)
    disabled_routes: list[str] = Field(default_factory=list)
    chunk_provenance: dict[str, ChunkProvenance] = Field(default_factory=dict)
    pending_formats: dict[str, int] = Field(default_factory=dict)
    format_feedbacks: dict[str, list[_FormatFeedback]] = Field(default_factory=dict)
    format_regenerations: dict[str, int] = Field(default_factory=dict)


def validate_automatic_state(state: AutomaticState, cached: dict | None = None) -> None:
    """Cross-check the ledger independently of aggregate counters and caches."""
    plan = state.route_plan
    if not isinstance(plan.get("routes"), list) or not plan["routes"]:
        raise ValueError("missing automatic routes")
    routes = {route["id"]: route for route in plan["routes"]}
    if len(routes) != len(plan["routes"]) or any(
        not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9_-]+", key) is None for key in routes
    ):
        raise ValueError("invalid automatic route identifiers")
    if plan.get("preferred_route_id") not in routes or (
        plan.get("fallback_route_id") is not None and plan["fallback_route_id"] not in routes
    ):
        raise ValueError("unknown preferred automatic route")
    if any(key not in routes for key in state.disabled_routes) or len(set(state.disabled_routes)) != len(state.disabled_routes):
        raise ValueError("invalid disabled automatic routes")
    if any(key not in state.nodes or route not in routes for key, route in state.chunk_routes.items()):
        raise ValueError("invalid pending automatic route")
    attempts = state.generation_attempts
    if state.calls_used > state.max_model_calls or state.model is not None:
        raise ValueError("automatic aggregate bounds")
    ids = [attempt.attempt_id for attempt in attempts]
    if len(ids) != len(set(ids)) or len(attempts) != state.calls_used:
        raise ValueError("automatic reservation ledger")
    total_usage: dict[str, int] = {}
    for key, node in state.nodes.items():
        history = [item for item in attempts if item.chunk_id == key]
        if len(history) != node.calls or len(history) > 3:
            raise ValueError("automatic per-chunk limit")
        for number, item in enumerate(history, 1):
            expected = f"{key}.{item.route_id}.attempt-{number}"
            if item.route_id not in routes or item.attempt_id != expected or item.format_attempt not in (1, 2):
                raise ValueError("automatic attempt identity")
            if item.http_attempts > 1 or any(type(v) is not int or v < 0 for v in item.usage.values()):
                raise ValueError("automatic request accounting")
            if item.response_received and (item.http_attempts != 1 or item.outcome_unknown):
                raise ValueError("automatic response accounting")
            if not item.response_received and item.usage:
                raise ValueError("usage without a received response")
            if item.status in {"responded", "rejected", "completed"} and not item.response_received:
                raise ValueError("missing automatic response")
            if item.status == "reserved" and (item.response_received or item.http_attempts or item.usage):
                raise ValueError("invalid open reservation")
            for metric, value in item.usage.items():
                total_usage[metric] = total_usage.get(metric, 0) + value
        regeneration_count = sum(item.format_attempt == 2 for item in history)
        if regeneration_count != state.format_regenerations.get(key, 0) or regeneration_count > 1:
            raise ValueError("automatic format allowance ledger")
        successes = [item for item in history if item.status == "completed"]
        provenance = state.chunk_provenance.get(key)
        if node.status == "completed":
            if len(successes) != 1 or history[-1] != successes[0] or provenance is None:
                raise ValueError("automatic success provenance")
            item = successes[0]
            if provenance != ChunkProvenance(route_id=item.route_id, attempt_id=item.attempt_id,
                                              model=item.model, reported_model=item.reported_model):
                raise ValueError("automatic success provenance mismatch")
            if cached is not None:
                response = cached[key]
                if (response.model != item.model or response.reported_model != item.reported_model
                        or response.usage != item.usage or response.attempts != item.http_attempts
                        or response.request_id != item.request_id):
                    raise ValueError("automatic cached response provenance")
        elif successes or provenance is not None:
            raise ValueError("unexpected automatic success provenance")
    if any(item.chunk_id not in state.nodes for item in attempts):
        raise ValueError("orphan automatic attempt")
    if set(state.chunk_provenance) - set(state.nodes):
        raise ValueError("orphan automatic provenance")
    if any(key not in state.nodes or value not in (1, 2) for key, value in state.pending_formats.items()):
        raise ValueError("invalid automatic format state")
    if set(state.format_feedbacks) - set(state.nodes) or set(state.format_regenerations) - set(state.nodes):
        raise ValueError("orphan automatic format state")
    if (sum(item.http_attempts for item in attempts) != state.request_attempts
            or sum(item.response_received for item in attempts) != state.response_count
            or total_usage != state.usage):
        raise ValueError("automatic aggregate ledger mismatch")


class AutomaticExecutionSession(ExecutionSession):
    automatic = True

    def __init__(self, text, chunks, options, fingerprint, *, backend, **kwargs):
        self.backend = backend
        self._initial_plan = deepcopy(backend.snapshot())
        if options.max_model_calls is None and kwargs.get("resume_from") is None:
            options = options.model_copy(update={"max_model_calls": max(1, len(chunks) + 2)})
        super().__init__(text, chunks, options, fingerprint, **kwargs)

    def save(self) -> None:
        if not isinstance(self.state, AutomaticState):
            values = self.state.model_dump(mode="json")
            values["checkpoint_version"] = "4"
            self.state = AutomaticState(**values, route_plan=deepcopy(self._initial_plan))
        super().save()

    def _load(self, resume_from: str | Path) -> None:
        super()._load(resume_from)
        if not isinstance(self.state, AutomaticState):
            raise ConfigurationError("Automatic recovery requires a v4 checkpoint; old fixed-model runs cannot acquire new fallback routes.")
        try:
            frozen = deepcopy(self.state.route_plan)
            current = deepcopy(self._initial_plan)
            for name in ("preferred_route_id", "fallback_route_id"):
                frozen.pop(name, None)
                current.pop(name, None)
            if frozen != current:
                raise ValueError("automatic route plan changed")
            validate_automatic_state(self.state, self.cached)
            self.backend.preferred_route_id = self.state.route_plan["preferred_route_id"]
            self.backend.fallback_route_id = self.state.route_plan["fallback_route_id"]
        except (ValueError, KeyError, TypeError):
            raise ConfigurationError("Automatic checkpoint routes, provenance or call ledger are damaged or incompatible.") from None

    def summary(self) -> dict[str, Any]:
        result = super().summary()
        if isinstance(self.state, AutomaticState):
            result.update(automatic=True, per_chunk_call_limit=3,
                          format_retries_reserved=sum(self.state.format_regenerations.values()),
                          format_recovered_chunks=sum(item.status == "completed" and item.format_attempt == 2
                                                      for item in self.state.generation_attempts),
                          route_switches=sum(max(0, len({a.route_id for a in self.state.generation_attempts
                                                        if a.chunk_id == key}) - 1) for key in self.state.nodes),
                          disabled_routes=list(self.state.disabled_routes))
        return result

    def route_for(self, chunk_id: str):
        route_id = self.state.chunk_routes.get(chunk_id, self.state.route_plan["preferred_route_id"])
        return self.backend.routes[route_id]

    def attempt_session(self, chunk_id: str, route_id: str):
        return _AttemptSession(self, chunk_id, route_id)

    def attempts_for(self, chunk_id: str):
        return [item for item in self.state.generation_attempts if item.chunk_id == chunk_id]

    def observe(self, response: BackendResponse) -> None:
        self.state.response_count += 1
        self.state.request_attempts += response.attempts
        # Per-attempt/per-chunk provenance is authoritative, never the last model.
        self.state.model = None
        for key, value in response.usage.items():
            self.state.usage[key] = self.state.usage.get(key, 0) + value
        self.save()

    def advance(self, chunk_id: str, route_id: str, exc: Exception, *, allow_retry: bool = True) -> bool:
        history = self.attempts_for(chunk_id)
        if not history:
            return False
        self.failed(chunk_id, exc)
        previous = history[-1]
        if previous.status not in {"rejected", "completed"}:
            previous.status = "failed"
            previous.error_code = getattr(exc, "code", "execution_error")
        if not allow_retry:
            self.save()
            return False
        local_format = isinstance(exc, InvalidResponseError) and exc.validation_details is not None
        regenerate = (local_format and self.state.format_retries == 1
                      and self.state.format_regenerations.get(chunk_id, 0) == 0)
        next_id = route_id if regenerate else self.backend.next_route(
            route_id, exc, {a.route_id for a in history}, set(self.state.disabled_routes))
        if next_id is None:
            self.save()
            return False
        # Persist the chosen next step before enforcing the total ceiling. A
        # manual budget increase must continue here, not resend a rejected path.
        self.state.chunk_routes[chunk_id] = next_id
        self.state.pending_formats[chunk_id] = 2 if regenerate else 1
        if not regenerate and not isinstance(exc, InvalidResponseError):
            if route_id not in self.state.disabled_routes:
                self.state.disabled_routes.append(route_id)
            self.state.route_plan["preferred_route_id"] = next_id
            self.backend.invalidate(route_id)
        self.save()
        if len(history) >= 3:
            self.save()
            raise BackendError("This chunk has exhausted its three generation attempts; no fallback request was sent.",
                               attempts=0, code="chunk_call_budget_exhausted")
        if self.state.calls_used >= self.state.max_model_calls:
            self.save()
            raise BackendError("Model-call budget exhausted before fallback; no additional request was sent.",
                               attempts=0, code="call_budget_exhausted")
        if regenerate:
            self.event("format_retry", chunk_id=chunk_id, route_id=route_id)
            return True
        target = self.backend.routes[next_id].config
        self.event("route_switched", chunk_id=chunk_id, from_route=route_id, to_route=next_id,
                   reason=getattr(exc, "code", "invalid_response"), model=target.model,
                   adapter=target.adapter, structured_output=target.structured_output,
                   used=self.state.calls_used, limit=self.state.max_model_calls, **self.summary())
        return True

    def subdivide(self, chunk_id: str) -> list[str]:
        route_id = self.route_for(chunk_id).route_id
        children = super().subdivide(chunk_id)
        for child_id in children:
            self.state.chunk_routes[child_id] = route_id
        if children:
            self.save()
        return children

    def provenance_dump(self) -> dict[str, Any]:
        from .automatic import public_route_snapshot
        return {"routes": {item["id"]: public_route_snapshot(item) for item in self.state.route_plan["routes"]},
                "generation_attempts": [item.model_dump(mode="json") for item in self.state.generation_attempts],
                "chunk_provenance": {key: item.model_dump(mode="json") for key, item in self.state.chunk_provenance.items()}}


class _AttemptSession:
    """The existing parse/validation loop sees one already-chosen route."""

    def __init__(self, session: AutomaticExecutionSession, chunk_id: str, route_id: str):
        self.session, self.chunk_id, self.route_id = session, chunk_id, route_id
        self.current: GenerationAttempt | None = None

    def format_attempt(self, chunk_id: str):
        value = self.session.state.pending_formats.get(chunk_id, 1)
        feedback = [item.model_dump(exclude_none=True) for item in self.session.state.format_feedbacks.get(chunk_id, [])]
        return value, feedback if value == 2 else []

    def artifact_stem(self, chunk_id: str, format_attempt: int = 1) -> str:
        number = self.session.state.nodes[chunk_id].calls + (0 if self.current is not None else 1)
        return self.current.attempt_id if self.current is not None else f"{chunk_id}.{self.route_id}.attempt-{number}"

    def reserve(self, chunk_id: str, *, format_attempt: int = 1) -> None:
        session = self.session
        state, node = session.state, session.state.nodes[chunk_id]
        if node.calls >= 3:
            raise BackendError("This chunk has exhausted its three generation attempts; recovery retains consumed attempts.",
                               attempts=0, code="chunk_call_budget_exhausted")
        if state.calls_used >= state.max_model_calls:
            raise BackendError("Model-call budget exhausted; completed chunks are retained.", attempts=0, code="call_budget_exhausted")
        if format_attempt == 2 and (state.format_retries != 1 or state.format_regenerations.get(chunk_id, 0) >= 1):
            raise InvalidResponseError("The format regeneration allowance is exhausted.", attempts=0, code="format_retry_exhausted")
        for previous in session.attempts_for(chunk_id):
            if previous.status in {"reserved", "responded"}:
                previous.status, previous.error_code = "failed", "interrupted"
                previous.outcome_unknown = not previous.response_received
        node.calls += 1
        state.calls_used += 1
        node.status, node.error_code = "in_flight", None
        state.chunk_routes[chunk_id] = self.route_id
        if format_attempt == 2:
            state.format_regenerations[chunk_id] = 1
        self.current = GenerationAttempt(attempt_id=f"{chunk_id}.{self.route_id}.attempt-{node.calls}",
                                         chunk_id=chunk_id, route_id=self.route_id, status="reserved",
                                         format_attempt=format_attempt)
        state.generation_attempts.append(self.current)
        session.save()
        session.event("call_reserved", chunk_id=chunk_id, route_id=self.route_id,
                      attempt_id=self.current.attempt_id, used=state.calls_used, limit=state.max_model_calls)

    def observe(self, response: BackendResponse) -> None:
        attempt = self.current
        if attempt is None or attempt.response_received:
            raise ConfigurationError("Automatic response has no unique reserved call.")
        attempt.response_received = True
        attempt.status = "responded"
        attempt.model, attempt.reported_model = response.model, response.reported_model
        attempt.request_id = response.request_id
        attempt.http_attempts, attempt.usage = response.attempts, dict(response.usage)
        attempt.elapsed_seconds = response.elapsed_seconds
        self.session.observe(response)

    def backend_failed(self, exc: BackendError) -> None:
        if self.current is None:
            raise ConfigurationError("Automatic failure has no reserved call.")
        metadata = getattr(exc, "response_metadata", None)
        if metadata is not None:
            self.observe(BackendResponse(text="", **metadata))
        else:
            self.current.http_attempts = exc.attempts
            self.session.state.request_attempts += exc.attempts
        self.current.status, self.current.error_code = "failed", exc.code
        self.current.outcome_unknown = bool(exc.attempts and exc.code in {"timeout", "transport_error"})
        self.session.save()

    def reject_format(self, chunk_id: str, feedback: list[dict]) -> None:
        if self.current is None:
            raise ConfigurationError("Automatic validation has no reserved call.")
        self.current.status, self.current.error_code = "rejected", "invalid_response"
        self.session.state.format_feedbacks[chunk_id] = [_FormatFeedback.model_validate(item) for item in feedback]
        self.session.state.nodes[chunk_id].status = "failed"
        self.session.state.nodes[chunk_id].error_code = "invalid_response"
        self.session.save()

    def complete(self, chunk_id: str, response: BackendResponse) -> None:
        if self.current is None or not self.current.response_received:
            raise ConfigurationError("Automatic completion has no received response.")
        self.current.status = "completed"
        self.session.state.chunk_provenance[chunk_id] = ChunkProvenance(
            route_id=self.route_id, attempt_id=self.current.attempt_id,
            model=response.model, reported_model=response.reported_model)
        self.session.complete(chunk_id, response)
        self.session.backend.remember_success(self.route_id)


def prepare_automatic_execution(text: str, task: TaskSpec, backend: Any, generation: GenerationOptions,
                                options: ExecutionOptions, *, max_chars: int = 6000,
                                table_rows_per_chunk: int = 12, format_retries: int = 0,
                                source_name: str | None = None, backend_identity: str | None = None,
                                trace=None, resume_from=None, context: ContextOptions | None = None,
                                structure=None, table_mode: str = "text") -> AutomaticExecutionSession:
    from .document_routing import ROUTING_REVISION, routing_structure
    routed = routing_structure(text, table_mode, structure)
    if routed is not None:
        structure = routed
    chunks = plan_chunks(text, task, generation, options, max_chars=max_chars,
                         table_rows_per_chunk=table_rows_per_chunk, context=context, structure=structure,
                         table_mode=table_mode)
    identity = {"engine_revision": ENGINE_REVISION, "output_protocol": "flat-v1",
                "source": hashlib.sha256(text.encode("utf-8")).hexdigest(), "source_name": source_name,
                "task": task.model_dump(mode="json"), "automatic_plan": backend.identity_snapshot(),
                "generation": generation.model_dump(mode="json"),
                "policy": options.model_dump(exclude={"max_model_calls"}), "max_chars": max_chars,
                "table_rows_per_chunk": table_rows_per_chunk,
                "context": (context or ContextOptions()).model_dump(),
                "structure": structure.model_dump(mode="json") if structure is not None else None}
    if routed is not None:
        identity["table_routing"] = {"mode": "auto", "revision": ROUTING_REVISION}
    return AutomaticExecutionSession(text, chunks, options, digest(identity), backend=backend, trace=trace,
                                     resume_from=resume_from, format_retries=format_retries,
                                     output_protocol="flat-v1", structure=structure,
                                     transport_settings=backend.transport_settings)
