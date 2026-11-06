"""Schema-driven extraction, strict source grounding, and review diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any

from pydantic import Field, StrictStr, ValidationError

from .chunking import chunk_document
from .contextual_chunking import coverage_report
from .errors import BackendError, ConfigurationError, InvalidResponseError, TaskValidationError
from .diagnostics import RunTrace
from .documents import Document
from .execution import ExecutionSession, check_resume_paths, prepare_execution
from .identity import can_ground_fields, grounded_candidate_id
from .evidence import ground_quote as _ground_quote, check_measurement, is_missing, link_candidates, rule_matches
from .json_validation import JSONValidationError, load_json as _load_json
from .prompting import build_messages, build_output_schema
from .structure_diagnostics import diagnose_structure
from .output_protocol import output_protocol_for_backend
from .schema_diagnostics import assess_schema_compliance
from .validation import contract_details, details, error_path, issue
from .models import (
    BackendResponse, Candidate, CheckResult, ContextOptions, ContextPassage, ContractModel, DocumentChunk, EvidenceRef,
    ExtractionBackend, ExtractionRequest, ExtractionResult, FieldValue,
    ExecutionOptions, GenerationOptions, MeasurementInput, OutputProtocol, ReviewItem, RunInfo, Scalar, SourceInfo, TaskSpec,
)


class _RawField(ContractModel):
    value: Scalar
    quote: StrictStr = ""


class _RawRecord(ContractModel):
    record_type: StrictStr
    fields: dict[str, _RawField]
    measurements: dict[str, MeasurementInput] = Field(default_factory=dict)


class _RawResponse(ContractModel):
    records: list[_RawRecord]


class _FlatRecord(ContractModel):
    record_type: StrictStr
    values: dict[str, Scalar]
    quotes: dict[str, StrictStr]
    measurements: dict[str, MeasurementInput] = Field(default_factory=dict)


class _FlatResponse(ContractModel):
    records: list[_FlatRecord]


def load_task(path: str | Path) -> TaskSpec:
    """Read and validate a UTF-8 JSON task specification."""
    try:
        return TaskSpec.model_validate(_load_json(Path(path).read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeError, ValueError) as exc:
        raise TaskValidationError("Could not read a valid UTF-8 JSON task specification") from exc


def _response_error(
    message: str, diagnostic: dict[str, Any], *, trace: RunTrace | None,
    chunk_id: str | None, format_attempt: int = 1,
) -> InvalidResponseError:
    first = diagnostic["errors"][0] if diagnostic["errors"] else None
    if first:
        if first["code"] == "invalid_json" and "line" in first:
            raw_line, raw_column = first["raw_line"], first["raw_column"]
            position = f"original response line {raw_line}, column {raw_column}"
            if (raw_line, raw_column) != (first["line"], first["column"]):
                position += f" (unfenced JSON line {first['line']}, column {first['column']})"
            message = (f"Model returned invalid JSON at {position}: "
                       f"{first['message']}. No response text was repaired.")
        else:
            message += (f" ({diagnostic['error_count']} error(s); first: {first['path']}; "
                        f"expected {first['expected']}, got {first['actual_type']}).")
    artifact = None
    if trace is not None and chunk_id is not None:
        stem = chunk_id if format_attempt == 1 else f"{chunk_id}.format-{format_attempt}"
        if trace.directory is not None:
            artifact = f"validation/{stem}.errors.json"
            trace.write_json(artifact, diagnostic)
            trace.describe_artifact(artifact, "validation_errors")
        trace.event("validation", "rejected", chunk_id=chunk_id, format_attempt=format_attempt,
                    phase=diagnostic["phase"], error_count=diagnostic["error_count"],
                    line=first.get("raw_line") if first else None, column=first.get("raw_column") if first else None,
                    structure=first.get("structure") if first else None,
                    path=first["path"] if first else "$", details=artifact)
    return InvalidResponseError(message, validation_details=diagnostic, validation_artifact=artifact)


def _parse_response(
    text: str, *, trace: RunTrace | None = None, chunk_id: str | None = None,
    format_attempt: int = 1, allowed_keys: set[str] | None = None,
    output_protocol: OutputProtocol = "flat-v1",
) -> _RawResponse:
    original_text = text
    if output_protocol not in ("flat-v1", "native-v1"):
        raise ConfigurationError("Unsupported extraction output protocol.")
    if allowed_keys is not None:
        allowed_keys = allowed_keys | {"values", "quotes"}
    offset = len(text) - len(text.lstrip())
    text = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", text, flags=re.IGNORECASE)
    if fence:
        offset += fence.start(1)
        text = fence.group(1)
    message = "Model output must match the requested JSON records contract"
    try:
        payload = _load_json(text, allowed_keys=allowed_keys)
    except json.JSONDecodeError as exc:
        position = offset + exc.pos
        # JSONDecodeError.msg is decoder-authored; never include its input document.
        diagnostic = details("json_syntax", [issue(
            (), "invalid_json", "valid JSON", text, exc.msg,
            line=exc.lineno, column=exc.colno, offset=exc.pos,
            raw_offset=position, raw_line=original_text.count("\n", 0, position) + 1,
            raw_column=position - original_text.rfind("\n", 0, position),
        )])
        diagnostic["position_basis"] = "line/column: stripped, unfenced JSON; raw_*: original response; offsets: zero-based Unicode"
        known_keys = {"records", "record_type", "fields", "values", "quotes", "value", "quote", "measurements",
                      "context_quote", "value_text", "unit_text", "unit_quote"}
        structure = diagnose_structure(text, exc.pos, allowed_keys=known_keys | (allowed_keys or set()))
        if structure is not None:
            diagnostic["errors"][0]["structure"] = structure
        raise _response_error(message, diagnostic, trace=trace, chunk_id=chunk_id,
                              format_attempt=format_attempt) from None
    except JSONValidationError as exc:
        raise _response_error(message, exc.diagnostic, trace=trace, chunk_id=chunk_id,
                              format_attempt=format_attempt) from None
    except (ValueError, TypeError):
        diagnostic = details("json_syntax", [issue(
            (), "invalid_json", "JSON without duplicate keys or non-finite numbers", text,
            "JSON contains a duplicate key, a non-finite number, or invalid structure.",
        )])
        raise _response_error(message, diagnostic, trace=trace, chunk_id=chunk_id,
                              format_attempt=format_attempt) from None
    try:
        if output_protocol == "native-v1":
            return _RawResponse.model_validate(payload)
        flat = _FlatResponse.model_validate(payload)
    except ValidationError as exc:
        raise _response_error(message, contract_details(exc, payload, allowed_keys=allowed_keys), trace=trace, chunk_id=chunk_id,
                              format_attempt=format_attempt) from None
    records = []
    for index, record in enumerate(flat.records):
        orphaned = record.quotes.keys() - record.values.keys()
        if orphaned:
            errors = [issue(("records", index, "quotes", name if allowed_keys is None or name in allowed_keys else "<undeclared>"),
                            "orphan_quote", "a corresponding values key", record.quotes[name],
                            "A quote has no corresponding value field.") for name in sorted(orphaned)]
            raise _response_error(message, details("records_contract", errors), trace=trace,
                                  chunk_id=chunk_id, format_attempt=format_attempt)
        item = {"record_type": record.record_type, "fields": {
            name: {"value": value, "quote": record.quotes.get(name, "")}
            for name, value in record.values.items()}}
        # Absence is meaningful: an undeclared empty measurements object is invalid.
        if "measurements" in record.model_fields_set:
            item["measurements"] = record.measurements
        records.append(_RawRecord.model_validate(item))
    return _RawResponse(records=records)


def _request(task: TaskSpec, chunk: DocumentChunk, generation: GenerationOptions,
             output_protocol: OutputProtocol = "flat-v1") -> ExtractionRequest:
    return ExtractionRequest(chunk_id=chunk.chunk_id, output_protocol=output_protocol,
                             messages=build_messages(task, chunk, output_protocol=output_protocol),
                             output_schema=build_output_schema(task, output_protocol=output_protocol),
                             generation=generation.model_copy(deep=True))


def _check_task_records(raw: _RawResponse, task: TaskSpec, *, trace: RunTrace | None,
                        chunk_id: str, format_attempt: int = 1, output_protocol: OutputProtocol = "flat-v1") -> None:
    errors = []
    for index, record in enumerate(raw.records):
        if record.record_type not in task.record_types:
            errors.append(issue(("records", index, "record_type"), "undeclared_record_type",
                                "declared record type", record.record_type, "Model returned an undeclared record type."))
            continue
        spec = task.record_types[record.record_type]
        if (record.measurements.keys() - spec.measurement_groups.keys()
                or ("measurements" in record.model_fields_set and not spec.measurement_groups)):
            errors.append(issue(("records", index, "measurements"), "undeclared_measurement",
                                "declared measurement groups", record.measurements,
                                "Model returned an undeclared measurement group."))
        for name, field in record.fields.items():
            container = "values" if output_protocol == "flat-v1" else "fields"
            location = ("records", index, container, name)
            if name not in spec.fields:
                errors.append(issue(("records", index, container, "<undeclared>"), "undeclared_field", "declared field", field.model_dump(),
                                    "Model returned an undeclared field."))
            elif not _value_has_type(field.value, spec.fields[name].type):
                errors.append(issue(location if output_protocol == "flat-v1" else (*location, "value"), "incorrect_field_type",
                                    spec.fields[name].type + " or null", field.value,
                                    "Model returned a value with an incorrect task field type."))
    if errors:
        diagnostic = details("task_schema", errors)
        raise _response_error(errors[0]["message"], diagnostic, trace=trace, chunk_id=chunk_id,
                              format_attempt=format_attempt)


def _value_has_type(value: Scalar, field_type: str) -> bool:
    if value is None:
        return True
    accepted = {
        "string": (str,), "integer": (int,),
        "number": (int, float), "boolean": (bool,),
    }
    # bool is an int subclass in Python; task fields treat them separately.
    return type(value) in accepted[field_type]


def _format_feedback(diagnostic: dict[str, Any], task: TaskSpec,
                     output_protocol: OutputProtocol = "flat-v1") -> list[dict[str, Any]]:
    """Send bounded structural hints without reflecting undeclared property names."""
    known_paths = {"$", "records", "records[0]", "records[0].record_type", "records[0].measurements"}
    containers = ("values", "quotes") if output_protocol == "flat-v1" else ("fields",)
    known_paths.update(error_path(("records", 0, name)) for name in containers)
    for record in task.record_types.values():
        for name in record.fields:
            if output_protocol == "flat-v1":
                for container in ("values", "quotes"):
                    known_paths.add(error_path(("records", 0, container, name)))
            else:
                for suffix in ((), ("value",), ("quote",)):
                    known_paths.add(error_path(("records", 0, "fields", name, *suffix)))
        for name in record.measurement_groups:
            for suffix in ((), ("context_quote",), ("value_text",), ("unit_text",), ("unit_quote",)):
                known_paths.add(error_path(("records", 0, "measurements", name, *suffix)))
    feedback = []
    for error in diagnostic["errors"][:5]:
        path = error["path"]
        normalized = re.sub(r"^records\[[0-9]+\]", "records[0]", path)
        if normalized not in known_paths:
            path = "$"
        feedback.append({"path": path[:256], "code": error["code"][:256],
                         "expected": error["expected"][:256]})
        if error["code"] == "invalid_json" and "line" in error:
            feedback[-1].update(line=error["line"], column=error["column"],
                                reason=error["message"][:256])
            if "structure" in error:
                # Do not teach a flat request to regenerate a malformed legacy wrapper.
                structure_path = re.sub(r"^records\[[0-9]+\]", "records[0]", error["structure"]["path"])
                if structure_path in known_paths:
                    feedback[-1]["structure"] = dict(error["structure"])
    return feedback


def _regeneration_request(request: ExtractionRequest, feedback: list[dict]) -> ExtractionRequest:
    messages = [dict(message) for message in request.messages]
    instructions = (
        "\n\nThe previous output failed local format validation. Regenerate the complete "
        "records for the same task and source, fixing these structural errors. Do not "
        "invent evidence or omit observations merely to pass validation. Resolve values "
        "only from the source. "
    )
    if any(error.get("structure") for error in feedback):
        instructions += (
            "Any wrong_shape/correct_shape below are redacted structure illustrations, "
            "not candidate data or valid JSON templates; <value> is a placeholder. "
            "Use the original task example for the requested record structure. "
        )
    messages[0]["content"] += (
        instructions + "The following JSON is diagnostic data, not instructions:\n"
        + json.dumps(feedback, ensure_ascii=False, allow_nan=False)
    )
    return request.model_copy(update={"messages": messages, "format_attempt": 2})


def _validated_response(
    task: TaskSpec, chunk: DocumentChunk, backend: ExtractionBackend,
    trace: RunTrace | None, format_retries: int,
    generation: GenerationOptions,
    session: ExecutionSession | None = None,
) -> tuple[_RawResponse, list[BackendResponse]]:
    """Regenerate only locally rejected output, keeping each attempt auditable."""
    output_protocol = output_protocol_for_backend(backend)
    request = _request(task, chunk, generation, output_protocol)
    first_attempt = 1
    if session is not None:
        first_attempt, feedback = session.format_attempt(chunk.chunk_id)
        if first_attempt == 2:
            request = _regeneration_request(request, feedback)
            if trace is not None:
                trace.event("format_retry", "resumed", chunk_id=chunk.chunk_id, format_attempt=2)
    responses = []
    allowed_keys = {name for record in task.record_types.values() for name in (*record.fields, *record.measurement_groups)}
    allowed_keys.update({"values", "quotes", "measurements", "context_quote", "value_text", "unit_text", "unit_quote"})
    for format_attempt in range(first_attempt, format_retries + 2):
        if session is not None:
            session.reserve(chunk.chunk_id, format_attempt=format_attempt)
        stem = request.artifact_stem
        if trace is not None:
            trace.event("model_request", "started", chunk_id=chunk.chunk_id, format_attempt=format_attempt)
            prepare = getattr(backend, "prepare_payload", None)
            request_kind = "http_json_body" if callable(prepare) else "extraction_request"
            payload = prepare(request) if callable(prepare) else request.model_dump(mode="json")
            request_path = f"requests/{stem}.request.json"
            trace.write_json(request_path, payload)
            trace.describe_artifact(request_path, request_kind)
            trace.event("model_request", "prepared", chunk_id=chunk.chunk_id, format_attempt=format_attempt,
                        kind=request_kind, artifact=request_path)
            trace.write_text(f"prompts/{stem}.system.txt", request.messages[0]["content"])
            trace.write_json(f"prompts/{stem}.task.json", task.model_dump(mode="json"))
            trace.write_text(f"prompts/{stem}.source.txt", chunk.text)
            for suffix, role in (("system.txt", "system_prompt"), ("task.json", "task_specification"),
                                 ("source.txt", "source_chunk")):
                trace.describe_artifact(f"prompts/{stem}.{suffix}", role)
        # HTTP, envelope, refusal and truncation failures do not trigger a format retry.
        try:
            response = backend.generate(request)
        except BackendError as exc:
            if session is not None:
                session.backend_failed(exc)
            if trace is not None and format_attempt > 1:
                failed_stage = trace.stage
                trace.event("format_retry", "failed", chunk_id=chunk.chunk_id,
                            format_attempt=format_attempt, reason="backend_failure")
                trace.stage = failed_stage
            raise
        responses.append(response)
        if session is not None:
            session.observe(response)
        if trace is not None:
            trace.write_text(f"responses/{stem}.txt", response.text)
            trace.write_json(f"responses/{stem}.meta.json", {
                "model": response.model, "attempts": response.attempts,
                "usage": response.usage, "elapsed_seconds": response.elapsed_seconds,
                "format_attempt": format_attempt,
                "finish_reason": response.finish_reason, "request_id": response.request_id,
                "reported_model": response.reported_model,
            })
            trace.describe_artifact(f"responses/{stem}.meta.json", "response_metadata")
            trace.describe_artifact(f"responses/{stem}.txt", "model_output_text")
            compliance_path = f"schema/{stem}.compliance.json"
            if trace.directory is None or not (trace.directory / compliance_path).exists():
                trace.write_json(compliance_path, assess_schema_compliance(
                    response.text, request.output_schema, getattr(backend, "structured_output", "off")))
                trace.describe_artifact(compliance_path, "requested_schema_compliance")
            trace.event("model_response", "completed", chunk_id=chunk.chunk_id,
                        format_attempt=format_attempt, attempts=response.attempts,
                        usage=response.usage, elapsed_seconds=response.elapsed_seconds)
            trace.event("validation", "started", chunk_id=chunk.chunk_id, format_attempt=format_attempt)
        # Custom adapters can return normalized finish metadata themselves.
        # Never parse a partial completion, even if its prefix is valid JSON.
        if response.finish_reason not in (None, "stop"):
            if trace is not None:
                trace.stage = "model_response"
            raise InvalidResponseError("The model completion did not finish normally.",
                                       attempts=response.attempts,
                                       code="truncated_response" if response.finish_reason == "length" else "invalid_response")
        try:
            raw = _parse_response(response.text, trace=trace, chunk_id=chunk.chunk_id,
                                  format_attempt=format_attempt, allowed_keys=allowed_keys, output_protocol=output_protocol)
            if trace is not None:
                trace.event("validation", "json_parsed", chunk_id=chunk.chunk_id,
                            format_attempt=format_attempt, record_count=len(raw.records))
            _check_task_records(raw, task, trace=trace, chunk_id=chunk.chunk_id,
                                format_attempt=format_attempt, output_protocol=output_protocol)
        except InvalidResponseError as exc:
            feedback = _format_feedback(exc.validation_details, task, output_protocol)
            if session is not None:
                session.reject_format(chunk.chunk_id, feedback)
            if format_attempt > format_retries:
                if trace is not None and format_attempt > 1:
                    trace.event("format_retry", "failed", chunk_id=chunk.chunk_id,
                                format_attempt=format_attempt, details=exc.validation_artifact)
                    trace.stage = "validation"
                raise
            # Only bounded structural feedback is sent; never recycle invalid raw values.
            request = _regeneration_request(request, feedback)
            if trace is not None:
                trace.event("format_retry", "retry", chunk_id=chunk.chunk_id,
                            format_attempt=request.format_attempt, details=exc.validation_artifact)
            continue
        if trace is not None:
            trace.write_json(f"normalized/{stem}.json", {
                "output_protocol": output_protocol, "kind": "decoded_internal_records",
                "response": raw.model_dump(mode="json", exclude_unset=True),
            })
            trace.describe_artifact(f"normalized/{stem}.json", "decoded_internal_records")
            trace.event("validation", "passed", chunk_id=chunk.chunk_id, format_attempt=format_attempt)
            if format_attempt > 1:
                trace.event("format_retry", "succeeded", chunk_id=chunk.chunk_id, format_attempt=format_attempt)
        if session is not None:
            session.complete(chunk.chunk_id, response)
        return raw, responses
    raise AssertionError("Unreachable format retry state")  # pragma: no cover


def _validate_cached_responses(task: TaskSpec, session: ExecutionSession) -> dict:
    """Check every cached response before a live resume or an offline preview."""
    checked = {}
    allowed_keys = {name for record in task.record_types.values()
                    for name in (*record.fields, *record.measurement_groups)}
    protocol = session.state.output_protocol
    for key, response in session.cached.items():
        raw = _parse_response(response.text, output_protocol=protocol, allowed_keys=allowed_keys)
        _check_task_records(raw, task, trace=None, chunk_id=key, output_protocol=protocol)
        checked[key] = raw
    return checked


def _responses_for_chunks(task: TaskSpec, chunks: list[DocumentChunk], backend: ExtractionBackend,
                          trace: RunTrace | None, format_retries: int, generation: GenerationOptions,
                          session: ExecutionSession | None):
    if session is None:
        for chunk in chunks:
            if trace is not None:
                trace.write_json(f"chunks/{chunk.chunk_id}.json", chunk.model_dump(mode="json"))
            raw, responses = _validated_response(task, chunk, backend, trace, format_retries, generation)
            yield chunk, raw, responses
        return
    # Validate ALL reused content before making any new call, even when an
    # earlier pending leaf precedes a damaged cached leaf in document order.
    checked = _validate_cached_responses(task, session)
    session.event("started", **session.summary())
    queue = session.leaves()
    while queue:
        key = queue.pop(0)
        chunk = session.chunks[key]
        if trace is not None:
            trace.write_json(f"chunks/{chunk.chunk_id}.json", chunk.model_dump(mode="json"))
        if key in checked:
            session.reused += 1
            session.event("reused", chunk_id=key)
            yield chunk, checked[key], [session.cached[key]]
            continue
        try:
            # A saved truncation has already spent its call. Resume the split,
            # including when the truncated call was a format regeneration.
            if session.state.nodes[key].error_code == "truncated_response":
                if trace is not None:
                    trace.stage = "model_response"
                raise InvalidResponseError("Resume the saved truncated chunk by subdivision when possible.",
                                           attempts=0, code="truncated_response")
            raw, responses = _validated_response(task, chunk, backend, trace, format_retries, generation, session)
        except Exception as exc:
            failed_stage = trace.stage if trace is not None else None
            session.failed(key, exc)
            if isinstance(exc, BackendError) and exc.code == "truncated_response":
                children = session.subdivide(key)
                if children:
                    queue[0:0] = children
                    continue
                if trace is not None:
                    trace.stage = failed_stage
            saved_stage = trace.stage if trace is not None else None
            session.event("stopped", **session.summary())
            if trace is not None:
                trace.stage = saved_stage
            raise
        yield chunk, raw, responses
    session.save()
    session.event("finished", **session.summary())


def _selected_backend(
    model: str, *, env_file: str | Path | None, api_key: str | None,
    api_key_env: str | None, base_url: str | None, region: str | None,
    model_options: Mapping[str, Any] | None, generation: GenerationOptions | None,
    format_retries: int, trace: RunTrace | None,
) -> tuple[ExtractionBackend, GenerationOptions]:
    """Resolve the opt-in model shortcut without changing backend injection."""
    # Import lazily: the public package imports this module before the registry.
    from .envconfig import load_env_file
    from .providers import resolve_model_config, resolve_model_credentials
    from .registry import default_registry

    if trace is not None and api_key is not None:
        trace.add_secrets(api_key)
    if model_options is not None and not isinstance(model_options, Mapping):
        raise ConfigurationError("model_options must be a mapping of backend configuration overrides.")
    overrides = dict(model_options or {})
    allowed = {
        "base_url", "api_key_env", "timeout", "read_timeout", "tls_mode", "max_attempts", "structured_output",
        "generation", "provider_options", "output_token_parameter",
    }
    if set(overrides) - allowed:
        raise ConfigurationError(
            "Unsupported model_options field; select the model with model= and use format_retries= separately."
        )
    if generation is not None:
        configured_generation = overrides.get("generation", {})
        if not isinstance(configured_generation, Mapping):
            raise ConfigurationError("model_options generation must be a mapping.")
        overrides["generation"] = {
            **configured_generation, **generation.model_dump(exclude_none=True),
        }
    for name, value in (("api_key_env", api_key_env), ("base_url", base_url)):
        if value is not None:
            overrides[name] = value
    overrides["format_retries"] = format_retries
    environment = load_env_file(env_file) if env_file is not None else dict(os.environ)
    config = resolve_model_config(model, environment=environment, overrides=overrides, region=region)
    key = resolve_model_credentials(config, environment, api_key=api_key)
    if trace is not None:
        trace.add_secrets(key)
    backend = default_registry().create(config.adapter, config=config, api_key=key, trace=trace)
    if trace is not None:
        trace.write_json("backend.json", {
            "backend": backend.name, "adapter": config.adapter, "model": config.model,
            "model_id": model, "selection_mode": "preset", "timeout": config.timeout,
            "read_timeout": config.read_timeout,
            "max_attempts": config.max_attempts, "api_key_configured": bool(key),
            "env_file_used": env_file is not None, "structured_output": config.structured_output,
            "format_retries": config.format_retries,
            "generation": config.generation.model_dump(mode="json"),
            "output_token_parameter": config.output_token_parameter,
            "tls_mode": config.tls_mode,
        })
    return backend, config.generation


def extract(
    text: str | Document,
    task: TaskSpec | dict[str, Any],
    *,
    backend: ExtractionBackend | None = None,
    model: str | None = None,
    api_key: str | None = None,
    api_key_env: str | None = None,
    base_url: str | None = None,
    env_file: str | Path | None = None,
    region: str | None = None,
    model_options: Mapping[str, Any] | None = None,
    source_name: str | None = None,
    max_chunk_chars: int = 6000,
    table_rows_per_chunk: int = 12,
    trace: RunTrace | None = None,
    format_retries: int = 0,
    generation: GenerationOptions | None = None,
    execution: ExecutionOptions | None = None,
    resume_from: str | Path | None = None,
    backend_identity: str | None = None,
    context: ContextOptions | None = None,
) -> ExtractionResult:
    """Extract candidates. Matching evidence does not establish factual validity.

    Failure of any chunk raises an explicit exception; a partial run is never
    returned as a successful empty extraction. Backends receive source text.
    An optional trace records diagnostics; its owner calls ``finish`` after
    successfully saving the result. Failures mark the trace failed immediately.
    ``format_retries=1`` permits one additional generation for a locally invalid
    response per chunk. It can incur extra model charges and defaults to zero.

    Pass either an existing ``backend`` or ``model="provider:model-id"``. The
    latter resolves service presets and credentials, optionally from an explicit
    ``env_file``. ``model_options`` supplies advanced configuration overrides;
    explicit ``generation`` controls override only their non-None values. The
    backend path retains its existing defaults and does not read credentials.
    """
    if resume_from is not None and trace is not None and trace.directory is not None:
        check_resume_paths(resume_from, [trace.directory])
    try:
        if context is not None and not isinstance(context, ContextOptions):
            raise ConfigurationError("context must be a ContextOptions instance.")
        context = ContextOptions.model_validate(context.model_dump()) if context is not None else ContextOptions()
        if resume_from is not None and execution is None:
            execution = ExecutionOptions()
        if execution is not None:
            if not isinstance(execution, ExecutionOptions):
                raise ConfigurationError("execution must be an ExecutionOptions instance.")
            execution = ExecutionOptions.model_validate(execution.model_dump())
        if resume_from is not None and (trace is None or trace.directory is None):
            raise ConfigurationError("Resuming extraction needs a new persistent RunTrace directory.")
        if type(format_retries) is not int or format_retries not in (0, 1):
            raise ConfigurationError("format_retries must be 0 or 1.")
        if generation is not None and not isinstance(generation, GenerationOptions):
            raise ConfigurationError("generation must be a GenerationOptions instance or None.")
        try:
            options = GenerationOptions.model_validate(generation.model_dump()) if generation is not None else GenerationOptions()
        except ValidationError:
            raise ConfigurationError("generation contains invalid controls.") from None
        selection = (model, api_key, api_key_env, base_url, env_file, region, model_options)
        if backend is not None:
            if any(value is not None for value in selection):
                raise ConfigurationError("Pass either backend= or model= with its configuration, not both.")
        elif model is None:
            raise ConfigurationError("Provide backend= or a provider:model identifier in model=.")
        else:
            backend, options = _selected_backend(
                model, env_file=env_file, api_key=api_key, api_key_env=api_key_env,
                base_url=base_url, region=region, model_options=model_options,
                generation=options if generation is not None else None,
                format_retries=format_retries, trace=trace,
            )
        return _extract(
            text, task, backend=backend, source_name=source_name,
            max_chunk_chars=max_chunk_chars, table_rows_per_chunk=table_rows_per_chunk,
            trace=trace, format_retries=format_retries, generation=options,
            execution=execution, resume_from=resume_from, backend_identity=backend_identity, context=context,
        )
    except Exception as exc:
        if trace is not None:
            trace.fail(exc)
        raise


def _extract(
    text: str | Document,
    task: TaskSpec | dict[str, Any],
    *,
    backend: ExtractionBackend,
    source_name: str | None,
    max_chunk_chars: int,
    table_rows_per_chunk: int,
    trace: RunTrace | None,
    format_retries: int,
    generation: GenerationOptions,
    execution: ExecutionOptions | None = None,
    resume_from: str | Path | None = None,
    backend_identity: str | None = None,
    context: ContextOptions | None = None,
) -> ExtractionResult:
    started = time.perf_counter()
    output_protocol = output_protocol_for_backend(backend)
    if trace is not None:
        trace.write_json("output-protocol.json", {"output_protocol": output_protocol,
                         "legacy_offline_replay": output_protocol == "native-v1"})
    mode = getattr(backend, "mode", "replay" if backend.name == "replay" else "live")
    if mode not in ("live", "replay"):
        raise ConfigurationError("Backend mode must be 'live' or 'replay'.")
    if trace is not None:
        trace.event("document", "started")
    if isinstance(text, Document):
        document = Document.model_validate(text.model_dump())
        if source_name is not None and source_name != document.source_name:
            raise ValueError("source_name must agree with the supplied Document")
        source_name = document.source_name
        text = document.text
    elif isinstance(text, str):
        document = Document.from_text(
            text, source_name=source_name,
            representation="text/markdown" if source_name and source_name.lower().endswith(".md") else "text/plain",
        )
    else:
        raise TypeError("text must be a string or Document")
    context = context or ContextOptions()
    from .structure import build_structure
    structure = document.structure or build_structure(text)
    chunk_structure = structure if document.structure is not None or context.mode != "none" else None
    if trace is not None:
        trace.write_json("document.json", document.model_dump(mode="json"))
        trace.event("document", "completed", character_count=len(text), document_id=document.document_id)
        trace.event("configuration", "task_validation_started")
    try:
        spec = task if isinstance(task, TaskSpec) else TaskSpec.model_validate(task)
    except (ValidationError, TypeError) as exc:
        raise TaskValidationError("Task does not match the task specification contract") from exc
    if trace is not None:
        trace.write_json("task.json", spec.model_dump(mode="json"))
        trace.event("chunking", "started", max_chunk_chars=max_chunk_chars, table_rows_per_chunk=table_rows_per_chunk)
    session = None
    if execution is not None:
        session = prepare_execution(text, spec, backend, generation, execution, max_chars=max_chunk_chars,
                                    table_rows_per_chunk=table_rows_per_chunk, format_retries=format_retries,
                                    source_name=source_name, backend_identity=backend_identity, trace=trace,
                                    resume_from=resume_from, context=context, structure=chunk_structure)
        chunks = [session.chunks[key] for key in session.state.roots]
    else:
        chunks = chunk_document(text, max_chars=max_chunk_chars, table_rows_per_chunk=table_rows_per_chunk,
                                context=context, structure=chunk_structure)
    coverage = coverage_report(text, chunks, max_chars=max_chunk_chars)
    if trace is not None:
        trace.write_json("structure.json", structure.model_dump(mode="json"))
        trace.write_json("coverage.json", {"stage": "initial", **coverage})
        trace.write_json("context-policy.json", context.model_dump(mode="json"))
        trace.write_json("chunk_plan.json", {"chunks": [
            {"chunk_id": chunk.chunk_id, "character_count": len(chunk.text),
             "character_target_exceeded": len(chunk.text) > max_chunk_chars,
             "body_segments": [s.model_dump() for s in chunk.body_segments],
             "context_segments": [s.model_dump() for s in chunk.context_segments],
             "segments": [segment.model_dump() for segment in chunk.segments]}
            for chunk in chunks
        ]})
        trace.event("chunking", "completed", chunk_count=len(session.leaves()) if session else len(chunks),
                    oversized_chunks=sum(len(chunk.text) > max_chunk_chars for chunk in chunks))
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    candidates: list[Candidate] = []
    review: list[ReviewItem] = []
    # Deduplicate only when all reported values have an exact original span.
    # Unlocated/ambiguous candidates retain their chunk and occurrence identity.
    dedup: dict[str, Candidate] = {}
    response_count = 0
    request_attempts = 0
    usage: dict[str, int] = {}
    model: str | None = None
    for chunk, raw, responses in _responses_for_chunks(spec, chunks, backend, trace, format_retries, generation, session):
        response_count += len(responses)
        for response in responses:
            request_attempts += response.attempts
            model = response.model or model
            for name, count in response.usage.items():
                usage[name] = usage.get(name, 0) + count
        for ordinal, record in enumerate(raw.records):
            if trace is not None:
                trace.event("validation", "record_started", chunk_id=chunk.chunk_id, ordinal=ordinal)
            record_spec = spec.record_types[record.record_type]
            fields: dict[str, FieldValue] = {}
            checks = [CheckResult(code="schema_passed", passed=True)]
            pending: list[tuple[str, str | None, str, str]] = []
            for name, raw_field in record.fields.items():
                if trace is not None:
                    trace.event("validation", "field_started", chunk_id=chunk.chunk_id, ordinal=ordinal, field=name)
                evidence = _ground_quote(text, chunk, raw_field.quote, normalize=True, structure=structure)
                if trace is not None:
                    trace.event("grounding", "field_checked", chunk_id=chunk.chunk_id, ordinal=ordinal,
                                field=name, status=evidence.status, matches=len(evidence.matches))
                fields[name] = FieldValue(value=raw_field.value, evidence=evidence)
                if not is_missing(raw_field.value):
                    checks.append(CheckResult(code="evidence_matched", passed=evidence.status == "matched", field=name))
                if not is_missing(raw_field.value) and evidence.status != "matched":
                    pending.append((
                        f"evidence_{evidence.status}", name,
                        "Evidence could not be located uniquely in this chunk's original source segments.", "review",
                    ))
            expected = {
                name for name, field in record_spec.fields.items() if field.required
            } | set(record_spec.identity_fields) | set(record_spec.condition_fields)
            expected.update(name for name, field in record_spec.fields.items() if rule_matches(field.required_when, fields))
            field_status = {}
            for name, field_spec in record_spec.fields.items():
                empty = name not in fields or is_missing(fields[name].value)
                inapplicable = rule_matches(field_spec.not_applicable_when, fields)
                if not empty:
                    field_status[name] = "reported"
                elif name in expected:
                    field_status[name] = "required_missing"
                    checks.append(CheckResult(code="required_field_present", passed=False, field=name))
                    pending.append(("missing_required_field", name, "A required identity, condition, or task field is missing.", "review"))
                elif inapplicable:
                    field_status[name] = "not_applicable"
                else:
                    field_status[name] = "not_reported"
                    pending.append(("missing_value", name, "The model did not report this optional field; this does not establish absence in the source.", "info"))
            measurements = {}
            for name, measurement_spec in record_spec.measurement_groups.items():
                if name not in record.measurements:
                    pending.append(("measurement_missing", measurement_spec.value_field,
                                    "The configured measurement group was not supplied; no token positions were inferred.", "review"))
                    continue
                measurement = check_measurement(text, chunk, record.measurements[name], measurement_spec, fields,
                                                schema_version="0.4", normalize=True, structure=structure)
                measurements[name] = measurement
                for code in measurement.issues:
                    pending.append((code, measurement_spec.value_field,
                                    f"Measurement group {name} failed a mechanical check; inspect its context and token evidence.", "review"))
            if not any(not is_missing(field.value) for field in fields.values()):
                pending.append(("empty_record", None, "The record contains no extracted fields.", "review"))
            body_anchor = True
            if chunk.context_policy is not None and chunk.context_policy.mode != "none":
                eligible = set(fields) - set(record_spec.identity_fields) - set(record_spec.condition_fields)
                eligible = eligible or set(fields)
                body_anchor = any(
                    not is_missing(fields[name].value) and any(
                        body.start <= span.start and span.end <= body.end
                        for span in [*fields[name].evidence.matches, *(item.span for item in fields[name].evidence.source_matches)]
                        for body in chunk.body_segments)
                    for name in eligible)
                if measurements:
                    body_anchor = body_anchor and all(any(
                        body.start <= span.start and span.end <= body.end
                        for span in measurement.value.matches for body in chunk.body_segments)
                        for measurement in measurements.values())
                checks.append(CheckResult(code="body_evidence", passed=body_anchor))
                if not body_anchor:
                    pending.append(("context_only_candidate", None,
                                    "No observation evidence anchors this candidate in the owned body; auxiliary context is not an extraction target.", "review"))
            stable_fields = {
                name: value.model_dump() for name, value in sorted(fields.items())
            }
            identity: dict[str, Any] = {
                "source": source_hash, "record_type": record.record_type, "fields": stable_fields,
            }
            if measurements:
                identity["measurements"] = {name: group.model_dump() for name, group in sorted(measurements.items())}
            can_dedup = can_ground_fields(fields)
            if not can_dedup:
                identity.update(chunk_id=chunk.chunk_id, ordinal=ordinal)
            if can_dedup:
                identifier = grounded_candidate_id(source_hash, record.record_type, fields, measurements)
            else:
                encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False)
                identifier = "candidate-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
            if can_dedup and identifier in dedup:
                prior = dedup[identifier]
                for item in chunk.context_segments:
                    passage = ContextPassage(role=item.role, span=item.span, quote=text[item.span.start:item.span.end])
                    if passage not in prior.source_context:
                        prior.source_context.append(passage)
                if body_anchor and any(check.code == "body_evidence" and not check.passed for check in prior.checks):
                    prior.checks = [check for check in prior.checks if check.code != "body_evidence"]
                    prior.checks.append(CheckResult(code="body_evidence", passed=True))
                    review[:] = [item for item in review if not (item.candidate_id == identifier and item.code == "context_only_candidate")]
                    prior.needs_review = any(item.candidate_id == identifier and item.severity == "review" for item in review)
                if chunk.chunk_id not in prior.chunk_ids:
                    prior.chunk_ids.append(chunk.chunk_id)
                if trace is not None:
                    trace.event("deduplication", "merged", chunk_id=chunk.chunk_id, candidate_id=identifier)
                continue
            candidate = Candidate(
                candidate_id=identifier, record_type=record.record_type, fields=fields,
                chunk_ids=[chunk.chunk_id], checks=checks, needs_review=any(item[3] == "review" for item in pending),
                field_status=field_status, measurements=measurements,
                source_context=[ContextPassage(role=item.role, span=item.span, quote=text[item.span.start:item.span.end])
                                for item in chunk.context_segments],
            )
            candidates.append(candidate)
            if can_dedup:
                dedup[identifier] = candidate
            review.extend(ReviewItem(candidate_id=identifier, code=code, field=field, message=message, severity=severity)
                          for code, field, message, severity in pending)
            if trace is not None:
                trace.event("deduplication", "retained", chunk_id=chunk.chunk_id,
                            candidate_id=identifier, needs_review=candidate.needs_review, review_count=sum(item[3] == "review" for item in pending))
        if trace is not None:
            chunk_candidates = [candidate for candidate in candidates if chunk.chunk_id in candidate.chunk_ids]
            chunk_ids = {candidate.candidate_id for candidate in chunk_candidates}
            chunk_reviews = [item for item in review if item.candidate_id in chunk_ids]
            trace.write_json(f"candidates/{chunk.chunk_id}.json", {
                "candidates": [candidate.model_dump(mode="json") for candidate in chunk_candidates],
                "review": [item.model_dump(mode="json") for item in chunk_reviews],
            })
            trace.event("chunk", "completed", chunk_id=chunk.chunk_id,
                        candidate_count=len(chunk_candidates), review_count=sum(item.severity == "review" for item in chunk_reviews),
                        info_count=sum(item.severity == "info" for item in chunk_reviews))
    if trace is not None:
        trace.event("extraction", "completed", candidate_count=len(candidates),
                    review_count=sum(item.severity == "review" for item in review),
                    info_count=sum(item.severity == "info" for item in review))
    if session is not None:
        final_chunks = [session.chunks[key] for key in session.leaves()]
        final_coverage = coverage_report(text, final_chunks, max_chars=max_chunk_chars)
        if trace is not None:
            trace.write_json("coverage.json", {"stage": "final", **final_coverage})
        response_count = session.state.response_count
        request_attempts = session.state.request_attempts
        usage = session.state.usage
        model = session.state.model or model
    return ExtractionResult(
        task=spec,
        source=SourceInfo(name=source_name, sha256=source_hash, character_count=len(text), document_id=document.document_id),
        candidates=candidates, review=review, links=link_candidates(candidates, spec),
        run=RunInfo(
            backend=backend.name, mode=mode, output_protocol=output_protocol,
            chunk_count=len(session.leaves()) if session else len(chunks), response_count=response_count,
            candidate_count=len(candidates), request_attempts=request_attempts,
            model=model, usage=usage, elapsed_seconds=time.perf_counter() - started,
        ),
    )
