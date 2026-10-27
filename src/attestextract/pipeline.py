"""Schema-driven extraction, strict source grounding, and review diagnostics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any

from pydantic import Field, StrictStr, ValidationError

from .chunking import chunk_document
from .errors import InvalidResponseError, TaskValidationError
from .diagnostics import RunTrace
from .documents import Document
from .identity import grounded_candidate_id
from .prompting import build_messages, build_output_schema
from .validation import contract_details, details, issue
from .models import (
    Candidate, CheckResult, ContractModel, DocumentChunk, EvidenceRef,
    ExtractionBackend, ExtractionRequest, ExtractionResult, FieldValue,
    ReviewItem, RunInfo, Scalar, SourceInfo, SourceSpan, TaskSpec,
)


class _RawField(ContractModel):
    value: Scalar
    quote: StrictStr = ""


class _RawRecord(ContractModel):
    record_type: StrictStr
    fields: dict[str, _RawField]


class _RawResponse(ContractModel):
    records: list[_RawRecord]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys are not allowed")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON numbers are not allowed")


def _load_json(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def load_task(path: str | Path) -> TaskSpec:
    """Read and validate a UTF-8 JSON task specification."""
    try:
        return TaskSpec.model_validate(_load_json(Path(path).read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeError, ValueError) as exc:
        raise TaskValidationError("Could not read a valid UTF-8 JSON task specification") from exc


def _response_error(
    message: str, diagnostic: dict[str, Any], *, trace: RunTrace | None,
    chunk_id: str | None,
) -> InvalidResponseError:
    first = diagnostic["errors"][0] if diagnostic["errors"] else None
    if first:
        message += (f" ({diagnostic['error_count']} error(s); first: {first['path']}; "
                    f"expected {first['expected']}, got {first['actual_type']}).")
    artifact = None
    if trace is not None and chunk_id is not None:
        artifact = f"validation/{chunk_id}.errors.json"
        trace.write_json(artifact, diagnostic)
        trace.describe_artifact(artifact, "validation_errors")
        trace.event("validation", "rejected", chunk_id=chunk_id,
                    phase=diagnostic["phase"], error_count=diagnostic["error_count"],
                    path=first["path"] if first else "$", details=artifact)
    return InvalidResponseError(message, validation_details=diagnostic, validation_artifact=artifact)


def _parse_response(
    text: str, *, trace: RunTrace | None = None, chunk_id: str | None = None,
) -> _RawResponse:
    text = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", text, flags=re.IGNORECASE)
    if fence:
        text = fence.group(1)
    message = "Model output must match the requested JSON records contract"
    try:
        payload = _load_json(text)
    except json.JSONDecodeError as exc:
        diagnostic = details("json_syntax", [issue(
            (), "invalid_json", "valid JSON", text, "Invalid JSON syntax; positions refer to the unfenced text.",
            line=exc.lineno, column=exc.colno,
        )])
        raise _response_error(message, diagnostic, trace=trace, chunk_id=chunk_id) from None
    except (ValueError, TypeError):
        diagnostic = details("json_syntax", [issue(
            (), "invalid_json", "JSON without duplicate keys or non-finite numbers", text,
            "JSON contains a duplicate key, a non-finite number, or invalid structure.",
        )])
        raise _response_error(message, diagnostic, trace=trace, chunk_id=chunk_id) from None
    try:
        return _RawResponse.model_validate(payload)
    except ValidationError as exc:
        raise _response_error(message, contract_details(exc, payload), trace=trace, chunk_id=chunk_id) from None


def _ground_quote(text: str, chunk: DocumentChunk, quote: str) -> EvidenceRef:
    if not quote.strip():
        return EvidenceRef(quote=quote, status="missing")
    found: set[tuple[int, int]] = set()
    for segment in chunk.segments:
        start = segment.start
        while True:
            index = text.find(quote, start, segment.end)
            if index < 0:
                break
            found.add((index, index + len(quote)))
            start = index + 1
    spans = [SourceSpan(start=start, end=end) for start, end in sorted(found)]
    if len(spans) == 1:
        return EvidenceRef(
            quote=quote, status="matched", start=spans[0].start,
            end=spans[0].end, matches=spans,
        )
    return EvidenceRef(
        quote=quote, status="ambiguous" if spans else "unmatched", matches=spans,
    )


def _request(task: TaskSpec, chunk: DocumentChunk) -> ExtractionRequest:
    return ExtractionRequest(chunk_id=chunk.chunk_id, messages=build_messages(task, chunk),
                             output_schema=build_output_schema(task))


def _check_task_records(raw: _RawResponse, task: TaskSpec, *, trace: RunTrace | None,
                        chunk_id: str) -> None:
    errors = []
    for index, record in enumerate(raw.records):
        if record.record_type not in task.record_types:
            errors.append(issue(("records", index, "record_type"), "undeclared_record_type",
                                "declared record type", record.record_type, "Model returned an undeclared record type."))
            continue
        spec = task.record_types[record.record_type]
        for name, field in record.fields.items():
            location = ("records", index, "fields", name)
            if name not in spec.fields:
                errors.append(issue(location, "undeclared_field", "declared field", field.model_dump(),
                                    "Model returned an undeclared field."))
            elif not _value_has_type(field.value, spec.fields[name].type):
                errors.append(issue((*location, "value"), "incorrect_field_type",
                                    spec.fields[name].type + " or null", field.value,
                                    "Model returned a value with an incorrect task field type."))
    if errors:
        diagnostic = details("task_schema", errors)
        raise _response_error(errors[0]["message"], diagnostic, trace=trace, chunk_id=chunk_id)


def _value_has_type(value: Scalar, field_type: str) -> bool:
    if value is None:
        return True
    accepted = {
        "string": (str,), "integer": (int,),
        "number": (int, float), "boolean": (bool,),
    }
    # bool is an int subclass in Python; task fields treat them separately.
    return type(value) in accepted[field_type]


def extract(
    text: str | Document,
    task: TaskSpec | dict[str, Any],
    *,
    backend: ExtractionBackend,
    source_name: str | None = None,
    max_chunk_chars: int = 6000,
    table_rows_per_chunk: int = 12,
    trace: RunTrace | None = None,
) -> ExtractionResult:
    """Extract candidates. Matching evidence does not establish factual validity.

    Failure of any chunk raises an explicit exception; a partial run is never
    returned as a successful empty extraction. Backends receive source text.
    An optional trace records diagnostics; its owner calls ``finish`` after
    successfully saving the result. Failures mark the trace failed immediately.
    """
    try:
        return _extract(
            text, task, backend=backend, source_name=source_name,
            max_chunk_chars=max_chunk_chars, table_rows_per_chunk=table_rows_per_chunk,
            trace=trace,
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
) -> ExtractionResult:
    started = time.perf_counter()
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
    chunks = chunk_document(
        text, max_chars=max_chunk_chars, table_rows_per_chunk=table_rows_per_chunk,
    )
    if trace is not None:
        trace.write_json("chunk_plan.json", {"chunks": [
            {"chunk_id": chunk.chunk_id, "character_count": len(chunk.text),
             "segments": [segment.model_dump() for segment in chunk.segments]}
            for chunk in chunks
        ]})
        trace.event("chunking", "completed", chunk_count=len(chunks))
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    candidates: list[Candidate] = []
    review: list[ReviewItem] = []
    # Deduplicate only when *all* supplied fields have an exact original span.
    # Unlocated/ambiguous candidates retain their chunk and occurrence identity.
    dedup: dict[str, Candidate] = {}
    response_count = 0
    request_attempts = 0
    usage: dict[str, int] = {}
    model: str | None = None
    for chunk in chunks:
        request = _request(spec, chunk)
        if trace is not None:
            trace.write_json(f"chunks/{chunk.chunk_id}.json", chunk.model_dump(mode="json"))
            trace.event("model_request", "started", chunk_id=chunk.chunk_id)
            prepare = getattr(backend, "prepare_payload", None)
            request_kind = "http_json_body" if callable(prepare) else "extraction_request"
            payload = prepare(request) if callable(prepare) else request.model_dump(mode="json")
            request_path = f"requests/{chunk.chunk_id}.request.json"
            trace.write_json(request_path, payload)
            trace.describe_artifact(request_path, request_kind)
            trace.event("model_request", "prepared", chunk_id=chunk.chunk_id, kind=request_kind, artifact=request_path)
            trace.write_text(f"prompts/{chunk.chunk_id}.system.txt", request.messages[0]["content"])
            trace.write_json(f"prompts/{chunk.chunk_id}.task.json", spec.model_dump(mode="json"))
            trace.write_text(f"prompts/{chunk.chunk_id}.source.txt", chunk.text)
            for suffix, role in (("system.txt", "system_prompt"), ("task.json", "task_specification"),
                                 ("source.txt", "source_chunk")):
                trace.describe_artifact(f"prompts/{chunk.chunk_id}.{suffix}", role)
        response = backend.generate(request)
        if trace is not None:
            trace.write_text(f"responses/{chunk.chunk_id}.txt", response.text)
            trace.write_json(f"responses/{chunk.chunk_id}.meta.json", {
                "model": response.model, "attempts": response.attempts,
                "usage": response.usage, "elapsed_seconds": response.elapsed_seconds,
            })
            trace.describe_artifact(f"responses/{chunk.chunk_id}.meta.json", "response_metadata")
            trace.describe_artifact(f"responses/{chunk.chunk_id}.txt", "model_output_text")
            trace.event("model_response", "completed", chunk_id=chunk.chunk_id, attempts=response.attempts)
            trace.event("validation", "started", chunk_id=chunk.chunk_id)
        response_count += 1
        request_attempts += response.attempts
        model = response.model or model
        for name, count in response.usage.items():
            usage[name] = usage.get(name, 0) + count
        raw = _parse_response(response.text, trace=trace, chunk_id=chunk.chunk_id)
        if trace is not None:
            trace.event("validation", "json_parsed", chunk_id=chunk.chunk_id, record_count=len(raw.records))
        _check_task_records(raw, spec, trace=trace, chunk_id=chunk.chunk_id)
        for ordinal, record in enumerate(raw.records):
            if trace is not None:
                trace.event("validation", "record_started", chunk_id=chunk.chunk_id, ordinal=ordinal)
            record_spec = spec.record_types[record.record_type]
            fields: dict[str, FieldValue] = {}
            checks = [CheckResult(code="schema_passed", passed=True)]
            pending: list[tuple[str, str | None, str]] = []
            for name, raw_field in record.fields.items():
                if trace is not None:
                    trace.event("validation", "field_started", chunk_id=chunk.chunk_id, ordinal=ordinal, field=name)
                evidence = _ground_quote(text, chunk, raw_field.quote)
                if trace is not None:
                    trace.event("grounding", "field_checked", chunk_id=chunk.chunk_id, ordinal=ordinal,
                                field=name, status=evidence.status, matches=len(evidence.matches))
                fields[name] = FieldValue(value=raw_field.value, evidence=evidence)
                checks.append(CheckResult(
                    code="evidence_matched", passed=evidence.status == "matched", field=name,
                ))
                if evidence.status != "matched":
                    pending.append((
                        f"evidence_{evidence.status}", name,
                        "Evidence could not be located uniquely in this chunk's original source segments.",
                    ))
                if raw_field.value is None:
                    pending.append(("missing_value", name, "The field has no reported value."))
            expected = {
                name for name, field in record_spec.fields.items() if field.required
            } | set(record_spec.identity_fields) | set(record_spec.condition_fields)
            for name in sorted(expected):
                if name not in fields or fields[name].value is None:
                    checks.append(CheckResult(code="required_field_present", passed=False, field=name))
                    pending.append(("missing_required_field", name, "A required identity, condition, or task field is missing."))
            if not fields:
                pending.append(("empty_record", None, "The record contains no extracted fields."))
            stable_fields = {
                name: value.model_dump() for name, value in sorted(fields.items())
            }
            identity: dict[str, Any] = {
                "source": source_hash, "record_type": record.record_type, "fields": stable_fields,
            }
            can_dedup = bool(fields) and all(
                field.evidence.status == "matched" for field in fields.values()
            )
            if not can_dedup:
                identity.update(chunk_id=chunk.chunk_id, ordinal=ordinal)
            if can_dedup:
                identifier = grounded_candidate_id(source_hash, record.record_type, fields)
            else:
                encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False)
                identifier = "candidate-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
            if can_dedup and identifier in dedup:
                prior = dedup[identifier]
                if chunk.chunk_id not in prior.chunk_ids:
                    prior.chunk_ids.append(chunk.chunk_id)
                if trace is not None:
                    trace.event("deduplication", "merged", chunk_id=chunk.chunk_id, candidate_id=identifier)
                continue
            candidate = Candidate(
                candidate_id=identifier, record_type=record.record_type, fields=fields,
                chunk_ids=[chunk.chunk_id], checks=checks, needs_review=bool(pending),
            )
            candidates.append(candidate)
            if can_dedup:
                dedup[identifier] = candidate
            review.extend(ReviewItem(candidate_id=identifier, code=code, field=field, message=message)
                          for code, field, message in pending)
            if trace is not None:
                trace.event("deduplication", "retained", chunk_id=chunk.chunk_id,
                            candidate_id=identifier, needs_review=bool(pending), review_count=len(pending))
        if trace is not None:
            chunk_candidates = [candidate for candidate in candidates if chunk.chunk_id in candidate.chunk_ids]
            chunk_ids = {candidate.candidate_id for candidate in chunk_candidates}
            chunk_reviews = [item for item in review if item.candidate_id in chunk_ids]
            trace.write_json(f"candidates/{chunk.chunk_id}.json", {
                "candidates": [candidate.model_dump(mode="json") for candidate in chunk_candidates],
                "review": [item.model_dump(mode="json") for item in chunk_reviews],
            })
            trace.event("chunk", "completed", chunk_id=chunk.chunk_id,
                        candidate_count=len(chunk_candidates), review_count=len(chunk_reviews))
    if trace is not None:
        trace.event("extraction", "completed", candidate_count=len(candidates), review_count=len(review))
    return ExtractionResult(
        task=spec,
        source=SourceInfo(name=source_name, sha256=source_hash, character_count=len(text), document_id=document.document_id),
        candidates=candidates, review=review,
        run=RunInfo(
            backend=backend.name, mode="replay" if backend.name == "replay" else "live",
            chunk_count=len(chunks), response_count=response_count,
            candidate_count=len(candidates), request_attempts=request_attempts,
            model=model, usage=usage, elapsed_seconds=time.perf_counter() - started,
        ),
    )
