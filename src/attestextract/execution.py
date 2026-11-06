"""Bounded extraction state and immutable-run checkpoint recovery.

Checkpoints are local diagnostic data, not accepted facts. Completed responses
are revalidated and grounded again; credentials never form part of this state.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from .diagnostics import RunTrace
from .errors import BackendError, ConfigurationError, InvalidResponseError
from .models import (BackendResponse, ContextOptions, ContractModel, DocumentChunk, ExecutionOptions,
                     GenerationOptions, OutputProtocol, SourceSpan, TaskSpec)
from .planning import plan_chunks, split_chunk
from .output_protocol import output_protocol_for_backend
# Hold the concrete built-in type, not a mutable registry/factory replacement.
from .backends import OpenAICompatibleBackend as _CompatibleBackend
from .responses_backend import DeepSeekResponsesBackend as _ResponsesBackend

# Change this when prompts, planning, parsing, or evidence semantics change.
# Transport-only releases deliberately retain the dev19 extraction revision.
ENGINE_REVISION = "dev19-extraction-1"


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


class _StructureFeedback(ContractModel):
    code: Literal["unexpected_object_in_object"]
    path: str = Field(max_length=256)
    actual_token: Literal["{"]
    previous_key: str | None = Field(default=None, max_length=64)
    next_key: str = Field(max_length=64)
    wrong_shape: str = Field(max_length=384)
    correct_shape: str = Field(max_length=384)
    message: str = Field(max_length=256)


class _FormatFeedback(ContractModel):
    path: str = Field(max_length=256)
    code: str = Field(max_length=256)
    expected: str = Field(max_length=256)
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=256)
    structure: _StructureFeedback | None = None


class _Node(ContractModel):
    segments: list[SourceSpan]
    body_segments: list[SourceSpan] = Field(default_factory=list)
    depth: int = Field(ge=0)
    status: Literal["pending", "in_flight", "failed", "split", "completed"] = "pending"
    children: list[str] = Field(default_factory=list)
    calls: int = Field(default=0, ge=0)
    cache_hash: str | None = None
    error_code: str | None = None
    format_failures: int = Field(default=0, ge=0, le=2)
    format_retries_reserved: int = Field(default=0, ge=0, le=1)
    format_feedback: list[_FormatFeedback] = Field(default_factory=list, max_length=5)


class _State(ContractModel):
    checkpoint_version: Literal["2", "3"] = "3"
    fingerprint: str
    engine_revision: str = ENGINE_REVISION
    package_version: str | None = None
    transport_settings: dict[str, Any] = Field(default_factory=dict)
    transport_changes: list[dict[str, Any]] = Field(default_factory=list)
    migrations: list[dict[str, str]] = Field(default_factory=list)
    output_protocol: OutputProtocol = "native-v1"
    roots: list[str]
    nodes: dict[str, _Node]
    max_model_calls: int = Field(gt=0)
    calls_used: int = Field(default=0, ge=0)
    response_count: int = Field(default=0, ge=0)
    request_attempts: int = Field(default=0, ge=0)
    usage: dict[str, int] = Field(default_factory=dict)
    model: str | None = None
    history: list[str] = Field(default_factory=list)
    format_retries: int = Field(default=0, ge=0, le=1)
    policy_changes: list[dict[str, dict[str, int]]] = Field(default_factory=list)


def _read_state(envelope: dict) -> _State:
    if (not isinstance(envelope, dict) or set(envelope) != {"state", "sha256"} or
            not isinstance(envelope["state"], dict) or digest(envelope["state"]) != envelope["sha256"]):
        raise ValueError("checksum")
    if envelope["state"].get("checkpoint_version") not in {"2", "3"}:
        raise ConfigurationError("This checkpoint uses an older or unsupported execution format. Start a new run; dev12 chunk plans cannot be resumed with the sentence-aware planner.")
    if envelope["state"].get("checkpoint_version") == "3" and not {
        "engine_revision", "package_version", "transport_settings", "transport_changes", "migrations"
    }.issubset(envelope["state"]):
        raise ValueError("missing v3 identity metadata")
    return _State.model_validate(envelope["state"])


def _checkpoint_path(path: str | Path) -> Path:
    path = Path(path).resolve()
    if path.is_dir():
        if (path / "diagnostics").is_dir():
            path = path / "diagnostics"
        path = path / "execution" / "checkpoint.json"
    if path.name != "checkpoint.json" or path.parent.name != "execution" or not path.is_file():
        raise ConfigurationError("Resume needs a run containing execution/checkpoint.json; older runs without checkpoints cannot be resumed.")
    return path.resolve()


def check_resume_paths(resume_from: str | Path, targets: list[Path]) -> None:
    """Reject writes into any previous run before CLI creates output directories."""
    path = _checkpoint_path(resume_from)
    try:
        envelope = json.loads(path.read_bytes().decode("utf-8"))
        state = _read_state(envelope)
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        raise ConfigurationError("Resume checkpoint is damaged or incompatible.") from None
    for directory in [*_resume_directories(resume_from), *(Path(value) for value in state.history)]:
        root = (directory.parent if directory.name == "diagnostics" else directory).resolve()
        for target in targets:
            resolved = target.resolve()
            if resolved.is_relative_to(root) or root.is_relative_to(resolved):
                raise ConfigurationError("Resume outputs, reports and diagnostics must be outside previous run directories; choose a new destination.")


def _resume_directories(resume_from: str | Path) -> list[Path]:
    # Keep both the logical run boundary and the real checkpoint location when
    # diagnostics/execution is a directory link.
    requested = Path(resume_from).absolute()
    if requested.is_dir():
        logical = requested / "diagnostics" if (requested / "diagnostics").is_dir() else requested
    else:
        logical = requested.parent.parent
    actual = _checkpoint_path(resume_from).parent.parent
    return list(dict.fromkeys([logical, actual]))


class ExecutionSession:
    def __init__(self, text: str, chunks: list[DocumentChunk], options: ExecutionOptions,
                 fingerprint: str, *, trace: RunTrace | None = None,
                 resume_from: str | Path | None = None, format_retries: int = 0,
                 output_protocol: OutputProtocol = "flat-v1", structure=None,
                 transport_settings: dict[str, Any] | None = None, legacy_compatibility=None) -> None:
        from . import __version__
        if resume_from is not None and trace is not None and trace.directory is not None:
            check_resume_paths(resume_from, [trace.directory])
        self.text, self.options, self.trace = text, options, trace
        self.structure = structure
        self._legacy_compatibility = legacy_compatibility
        self.chunks = {chunk.chunk_id: chunk for chunk in chunks}
        self.cached: dict[str, BackendResponse] = {}
        self.reused = 0
        self.state = _State(fingerprint=fingerprint, output_protocol=output_protocol, roots=list(self.chunks),
                            package_version=__version__, transport_settings=transport_settings or {},
                            nodes={chunk.chunk_id: _Node(segments=chunk.segments, body_segments=chunk.body_segments, depth=0) for chunk in chunks},
                            max_model_calls=options.max_model_calls or max(1, 3 * len(chunks)),
                            format_retries=format_retries)
        if resume_from is not None:
            self._load(resume_from)
        changes = {}
        if options.max_model_calls is not None:
            if options.max_model_calls < self.state.calls_used:
                raise ConfigurationError("The requested total model-call budget is below the reservations already saved in the checkpoint.")
            if options.max_model_calls != self.state.max_model_calls:
                changes["max_model_calls"] = {"from": self.state.max_model_calls, "to": options.max_model_calls}
                self.state.max_model_calls = options.max_model_calls
        if format_retries != self.state.format_retries:
            changes["format_retries"] = {"from": self.state.format_retries, "to": format_retries}
            self.state.format_retries = format_retries
        if changes:
            self.state.policy_changes.append(changes)
        # Copy only verified completed responses into the NEW run. Failed source
        # artifacts stay in the old, unmodified run referenced by history.
        if trace is not None and trace.directory is not None:
            for chunk_id, response in self.cached.items():
                self._write_cache(chunk_id, response)
        self.save()

    def _load(self, resume_from: str | Path) -> None:
        path = _checkpoint_path(resume_from)
        try:
            envelope = json.loads(path.read_bytes().decode("utf-8"))
            state = _read_state(envelope)
            legacy = state.checkpoint_version == "2"
            if legacy:
                if self._legacy_compatibility is None:
                    raise ConfigurationError("This v2 checkpoint cannot be verified for migration. Only supported dev19 built-in backend runs can migrate; start a new run.")
                self._legacy_compatibility.migrate_identity(state, path, self.state)
            if (state.fingerprint != self.state.fingerprint or state.roots != self.state.roots
                    or state.output_protocol != self.state.output_protocol
                    or state.engine_revision != self.state.engine_revision):
                raise ConfigurationError("Resume identity mismatch: use the same source, task, engine revision, output protocol, model, endpoint and generation/planning settings. Connection settings, total call budget and format-retry allowance may change; consumed allowances are retained.")
            pending = [(self.chunks[key], 0) for key in state.roots]
            visited = set()
            while pending:
                chunk, depth = pending.pop(0)
                key = chunk.chunk_id
                node = state.nodes[key]
                if (key in visited or node.depth != depth or node.segments != chunk.segments
                        or node.body_segments != chunk.body_segments):
                    raise ValueError("chunk tree")
                if node.status in {"completed", "split", "in_flight"} and node.calls < 1:
                    raise ValueError("missing call reservation")
                if (node.format_failures > node.calls or
                        node.format_failures > 1 + node.format_retries_reserved or
                        node.format_retries_reserved and (not node.format_failures or node.calls < 2) or
                        node.status == "completed" and node.format_failures > 1 or
                        bool(node.format_feedback) != bool(node.format_failures)):
                    raise ValueError("format retry ledger")
                visited.add(key)
                self.chunks[key] = chunk
                if node.status == "split":
                    children = split_chunk(self.text, chunk, min_chars=self.options.min_chunk_chars, structure=self.structure)
                    if depth >= self.options.max_split_depth or not children or node.children != [c.chunk_id for c in children]:
                        raise ValueError("split tree")
                    pending[0:0] = [(child, depth + 1) for child in children]
                elif node.children:
                    raise ValueError("unexpected children")
                if node.status == "completed":
                    cache = (path.parent / "cache" / f"{key}.json").resolve()
                    if not cache.is_relative_to(path.parent.resolve()):
                        raise ValueError("cache path")
                    payload = json.loads(cache.read_bytes().decode("utf-8"))
                    if digest(payload) != node.cache_hash:
                        raise ValueError("cache checksum")
                    response = BackendResponse.model_validate(payload)
                    if response.finish_reason not in (None, "stop"):
                        raise ValueError("incomplete cache")
                    self.cached[key] = response
                elif node.cache_hash is not None:
                    raise ValueError("unexpected cache")
            if visited != set(state.nodes) or sum(n.calls for n in state.nodes.values()) != state.calls_used:
                raise ValueError("call ledger")
            if state.calls_used > state.max_model_calls or any(v < 0 for v in state.usage.values()):
                raise ValueError("budget ledger")
            if state.response_count > state.calls_used or state.request_attempts < state.response_count:
                raise ValueError("response ledger")
            if legacy:
                self._legacy_compatibility.validate_requests(state, path, self.chunks)
        except ConfigurationError:
            raise
        except (OSError, ValueError, TypeError, KeyError, ValidationError, RecursionError):
            raise ConfigurationError("Resume checkpoint/cache is missing, damaged, redacted or incompatible; no cached result was trusted.") from None
        if state.transport_settings != self.state.transport_settings:
            state.transport_changes.append({"from": state.transport_settings, "to": self.state.transport_settings})
        state.transport_settings = self.state.transport_settings
        state.package_version = self.state.package_version
        self.state = state
        self.state.history.extend(str(directory) for directory in _resume_directories(resume_from)
                                  if str(directory) not in self.state.history)

    def leaves(self) -> list[str]:
        pending = list(self.state.roots)
        result = []
        while pending:
            key = pending.pop(0)
            node = self.state.nodes[key]
            if node.status == "split":
                pending[0:0] = node.children
            else:
                result.append(key)
        return result

    def summary(self) -> dict[str, Any]:
        leaves = [self.state.nodes[key] for key in self.leaves()]
        return {"budget_unit": "backend.generate invocations", "max_model_calls": self.state.max_model_calls,
                "calls_reserved": self.state.calls_used, "initial_chunks": len(self.state.roots),
                "leaf_chunks": len(self.leaves()), "completed_chunks": len(self.cached),
                "reused_chunks": self.reused, "split_chunks": sum(n.status == "split" for n in self.state.nodes.values()),
                "response_count": self.state.response_count, "request_attempts": self.state.request_attempts,
                "usage": self.state.usage, "history": self.state.history,
                "format_retries": self.state.format_retries,
                "format_retries_reserved": sum(n.format_retries_reserved for n in self.state.nodes.values()),
                "first_pass_chunks": sum(n.status == "completed" and n.calls == 1 for n in leaves),
                "format_recovered_chunks": sum(n.status == "completed" and n.format_retries_reserved == 1 for n in leaves),
                "failed_chunks": sum(n.status == "failed" for n in leaves),
                "pending_chunks": sum(n.status in {"pending", "in_flight"} for n in leaves),
                "policy_changes": self.state.policy_changes,
                "transport_settings": self.state.transport_settings,
                "transport_changes": self.state.transport_changes,
                "migrations": self.state.migrations}

    def save(self) -> None:
        if self.trace is not None and self.trace.directory is not None:
            payload = self.state.model_dump(mode="json")
            self.trace.write_json("execution/checkpoint.json", {"state": payload, "sha256": digest(payload)}, atomic=True)
            self.trace.write_json("execution/summary.json", self.summary(), atomic=True)

    def event(self, event: str, **details: Any) -> None:
        if self.trace is not None:
            self.trace.event("execution", event, **details)

    def format_attempt(self, chunk_id: str) -> tuple[int, list[dict]]:
        """Resume the pending regeneration without issuing fresh allowances."""
        node = self.state.nodes[chunk_id]
        if not node.format_failures:
            return 1, []
        if node.format_retries_reserved >= self.state.format_retries:
            self.event("format_retry_exhausted", chunk_id=chunk_id, **self.summary())
            if self.trace is not None:
                self.trace.stage = "validation"
            raise InvalidResponseError(
                "This chunk has no remaining format regeneration allowance. Resume retains used or interrupted regeneration reservations; increasing the total call budget does not reset them. Inspect the saved failures before starting a new run.",
                attempts=0, code="format_retry_exhausted")
        return 2, [item.model_dump(exclude_none=True) for item in node.format_feedback]

    def reject_format(self, chunk_id: str, feedback: list[dict]) -> None:
        node = self.state.nodes[chunk_id]
        node.format_failures += 1
        node.format_feedback = [_FormatFeedback.model_validate(item) for item in feedback]
        node.status, node.error_code = "failed", "invalid_response"
        self.save()

    def reserve(self, chunk_id: str, *, format_attempt: int = 1) -> None:
        if format_attempt == 2:
            self.format_attempt(chunk_id)
        if self.state.calls_used >= self.state.max_model_calls:
            self.event("budget_exhausted", **self.summary())
            raise BackendError("Model-call budget exhausted. Completed chunks are retained; resume with a larger explicit total --max-model-calls budget.",
                               attempts=0, code="call_budget_exhausted")
        self.state.calls_used += 1
        node = self.state.nodes[chunk_id]
        node.calls += 1
        if format_attempt == 2:
            node.format_retries_reserved += 1
        node.status = "in_flight"
        self.save()  # Reserve before contacting the backend, including crashes.
        self.event("call_reserved", chunk_id=chunk_id, used=self.state.calls_used, limit=self.state.max_model_calls)

    def observe(self, response: BackendResponse) -> None:
        self.state.response_count += 1
        self.state.request_attempts += response.attempts
        self.state.model = response.model or self.state.model
        for key, value in response.usage.items():
            self.state.usage[key] = self.state.usage.get(key, 0) + value
        self.save()

    def backend_failed(self, exc: BackendError) -> None:
        metadata = getattr(exc, "response_metadata", None)
        if metadata is not None:
            self.observe(BackendResponse(text="", **metadata))
        else:
            self.state.request_attempts += exc.attempts
            self.save()

    def failed(self, chunk_id: str, exc: Exception) -> None:
        node = self.state.nodes[chunk_id]
        node.status = "failed"
        node.error_code = exc.code if isinstance(exc, BackendError) else "execution_error"
        self.save()

    def _write_cache(self, chunk_id: str, response: BackendResponse) -> None:
        if self.trace is not None:
            self.trace.write_json(f"execution/cache/{chunk_id}.json", response.model_dump(mode="json"), atomic=True)

    def complete(self, chunk_id: str, response: BackendResponse) -> None:
        self._write_cache(chunk_id, response)
        self.cached[chunk_id] = response
        node = self.state.nodes[chunk_id]
        node.cache_hash = digest(response.model_dump(mode="json"))
        node.status, node.error_code = "completed", None
        self.save()

    def subdivide(self, chunk_id: str) -> list[str]:
        node = self.state.nodes[chunk_id]
        children = [] if node.depth >= self.options.max_split_depth else split_chunk(
            self.text, self.chunks[chunk_id], min_chars=self.options.min_chunk_chars, structure=self.structure)
        if not children:
            self.event("unsplittable", chunk_id=chunk_id, depth=node.depth)
            return []
        node.status = "split"
        node.children = [child.chunk_id for child in children]
        for child in children:
            self.chunks[child.chunk_id] = child
            self.state.nodes[child.chunk_id] = _Node(segments=child.segments, body_segments=child.body_segments, depth=node.depth + 1)
        self.save()
        self.event("split", chunk_id=chunk_id, children=node.children, leaf_chunks=len(self.leaves()))
        return node.children


def prepare_execution(text: str, task: TaskSpec, backend: Any, generation: GenerationOptions,
                      options: ExecutionOptions, *, max_chars: int = 6000,
                      table_rows_per_chunk: int = 12, format_retries: int = 0,
                      source_name: str | None = None, backend_identity: str | None = None,
                      trace: RunTrace | None = None, resume_from: str | Path | None = None,
                      context: ContextOptions | None = None, structure=None) -> ExecutionSession:
    builtin = type(backend) in (_CompatibleBackend, _ResponsesBackend)
    if backend_identity is None:
        if builtin:
            backend_identity = digest({key: getattr(backend, key, None) for key in (
                "name", "base_url", "model", "structured_output",
                "provider_options", "output_token_parameter")})
        elif resume_from is not None or trace is not None and trace.directory is not None:
            raise ConfigurationError("Persistent execution with a custom backend needs an explicit backend_identity string binding its model and configuration.")
        else:
            backend_identity = backend.name
    if not isinstance(backend_identity, str) or not backend_identity:
        raise ConfigurationError("backend_identity must be a nonempty string; do not include credentials.")
    chunks = plan_chunks(text, task, generation, options, max_chars=max_chars, table_rows_per_chunk=table_rows_per_chunk,
                         context=context, structure=structure)
    output_protocol = output_protocol_for_backend(backend)
    identity = {"engine_revision": ENGINE_REVISION, "output_protocol": output_protocol, "source": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                          "source_name": source_name, "task": task.model_dump(mode="json"),
                          "backend": backend_identity, "backend_name": backend.name,
                          "generation": generation.model_dump(mode="json"),
                          "policy": options.model_dump(exclude={"max_model_calls"}),
                          "max_chars": max_chars, "table_rows_per_chunk": table_rows_per_chunk,
                          "context": (context or ContextOptions()).model_dump(),
                          "structure": structure.model_dump(mode="json") if structure is not None else None}
    transport = {key: getattr(backend, key, None) for key in (
        "timeout", "read_timeout", "tls_mode", "max_attempts")} if builtin else {}
    legacy_compatibility = None
    if builtin and resume_from is not None:
        from .execution_migration import Dev19Migration
        legacy_compatibility = Dev19Migration(backend, task, generation, identity)
    return ExecutionSession(text, chunks, options, digest(identity), trace=trace, resume_from=resume_from,
                            format_retries=format_retries, output_protocol=output_protocol, structure=structure,
                            transport_settings=transport, legacy_compatibility=legacy_compatibility)
