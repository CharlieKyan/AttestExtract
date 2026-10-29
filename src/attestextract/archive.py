"""Separate extraction candidates, explicit review decisions, and final records."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .documents import Document
from .evidence import check_measurement, is_missing, rule_matches
from .identity import can_ground_fields, grounded_candidate_id
from .models import (
    Candidate, ContractModel, DocumentChunk, EvidenceRef, ExtractionResult, FieldValue,
    MeasurementInput,
)


class CandidateReview(ContractModel):
    candidate_id: str
    status: Literal["accepted", "rejected", "needs_review", "unreviewed"]
    reviewer: str | None = None
    rationale: str = ""
    origin: Literal["human", "synthetic_fixture", "system"] = "human"
    annotation_ref: str | None = None

    @model_validator(mode="after")
    def verify_attribution(self) -> CandidateReview:
        if self.origin == "system" and self.status in {"accepted", "rejected"}:
            raise ValueError("Structural checks cannot accept or reject a candidate as a reviewed fact")
        if self.status != "unreviewed" and self.origin != "system":
            if not self.reviewer or not self.reviewer.strip() or not self.rationale.strip():
                raise ValueError("A review decision requires a reviewer and rationale")
        return self


class FinalRecord(ContractModel):
    record_id: str
    document_id: str
    source_candidate_id: str
    status: Literal["accepted"] = "accepted"
    record_type: str
    fields: dict[str, FieldValue]


def _record_id(document_id: str, candidate_id: str) -> str:
    digest = hashlib.sha256(json.dumps([document_id, candidate_id]).encode("utf-8")).hexdigest()
    return "record-" + digest[:24]


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _validate_evidence(document: Document, evidence: EvidenceRef) -> None:
    coordinates = [(span.start, span.end) for span in evidence.matches]
    if len(coordinates) != len(set(coordinates)):
        raise ValueError("Evidence match coordinates must be unique")
    for start, end in coordinates:
        if end > len(document.text) or document.text[start:end] != evidence.quote:
            raise ValueError("Evidence coordinates must recover the exact document quote")
    if evidence.status == "matched":
        if not evidence.quote.strip() or evidence.start is None or evidence.end is None:
            raise ValueError("Matched evidence requires a nonempty quote and coordinates")
        if evidence.start < 0 or evidence.end <= evidence.start:
            raise ValueError("Evidence coordinates must be valid half-open intervals")
        if coordinates != [(evidence.start, evidence.end)]:
            raise ValueError("Matched evidence must have exactly one consistent source span")
    else:
        if evidence.start is not None or evidence.end is not None:
            raise ValueError("Unresolved evidence cannot have a guessed source span")
        if evidence.status == "ambiguous":
            if len(coordinates) < 2 or not evidence.quote.strip():
                raise ValueError("Ambiguous evidence requires multiple exact source matches")
        elif coordinates:
            raise ValueError("Missing or unmatched evidence cannot contain source matches")
        if evidence.status == "missing" and evidence.quote.strip():
            raise ValueError("Missing evidence must have an empty quote")


def _validate_measurements(document: Document, extraction: ExtractionResult, candidate: Candidate) -> None:
    spec = extraction.task.record_types[candidate.record_type]
    if candidate.measurements.keys() - spec.measurement_groups.keys():
        raise ValueError("Measurements must reference declared task groups")
    for name, measurement in candidate.measurements.items():
        contexts = [measurement.context]
        if measurement.unit_context is not None:
            contexts.append(measurement.unit_context)
        for evidence in [*contexts, measurement.value, measurement.unit]:
            _validate_evidence(document, evidence)
        if measurement.condition_fields != spec.measurement_groups[name].condition_fields:
            raise ValueError("Measurement conditions must preserve their task-declared references")
        if (measurement.unit_source == "table_header") != (measurement.unit_context is not None):
            raise ValueError("Table header units require separate header evidence")
        for token, context in ((measurement.value, measurement.context),
                               (measurement.unit, measurement.unit_context or measurement.context)):
            if token.status == "matched" and (
                context.status != "matched" or not context.start <= token.start < token.end <= context.end
            ):
                raise ValueError("Measurement tokens must lie within their exact context")
        if not measurement.issues:
            # Recheck the recorded contexts only; archive loading must not relocate
            # tokens to another occurrence elsewhere in the document.
            chunk = DocumentChunk(chunk_id="archive", text="", segments=[
                span for context in contexts for span in context.matches
            ])
            raw = MeasurementInput(
                context_quote=measurement.context.quote, value_text=measurement.value.quote,
                unit_text=measurement.unit.quote,
                unit_quote=measurement.unit_context.quote if measurement.unit_context else "",
            )
            checked = check_measurement(document.text, chunk, raw, spec.measurement_groups[name], candidate.fields)
            if checked.model_dump() != measurement.model_dump():
                raise ValueError("Measurement evidence does not pass its recorded mechanical checks")


def _validate_candidate(document: Document, extraction: ExtractionResult, candidate: Candidate) -> None:
    spec = extraction.task.record_types.get(candidate.record_type)
    if spec is None or candidate.fields.keys() - spec.fields.keys():
        raise ValueError("A candidate must use declared task record types and fields")
    types = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,)}
    for name, field in candidate.fields.items():
        if field.value is not None and type(field.value) not in types[spec.fields[name].type]:
            raise ValueError("A candidate field has an invalid task scalar type")
        _validate_evidence(document, field.evidence)
    if candidate.field_status.keys() - spec.fields.keys():
        raise ValueError("Field statuses must reference declared task fields")
    _validate_measurements(document, extraction, candidate)
    grounded = can_ground_fields(candidate.fields) if extraction.schema_version == "0.2" else (
        bool(candidate.fields) and all(field.evidence.status == "matched" for field in candidate.fields.values())
    )
    if grounded:
        expected_id = grounded_candidate_id(document.text_sha256, candidate.record_type, candidate.fields, candidate.measurements)
        if candidate.candidate_id != expected_id:
            raise ValueError("Candidate content changed; create a new candidate and review it separately")


def _can_accept(extraction: ExtractionResult, candidate: Candidate) -> bool:
    spec = extraction.task.record_types[candidate.record_type]
    # Legacy occurrence IDs cannot be recomputed when any field was unlocated.
    # Keep their former acceptance restriction instead of weakening old archives.
    if extraction.schema_version == "0.1" and any(is_missing(field.value) for field in candidate.fields.values()):
        return False
    required = {name for name, field in spec.fields.items() if field.required}
    required |= set(spec.identity_fields) | set(spec.condition_fields)
    required.update(name for name, field in spec.fields.items() if rule_matches(field.required_when, candidate.fields))
    return (
        can_ground_fields(candidate.fields)
        and not candidate.needs_review
        and all(name in candidate.fields and not is_missing(candidate.fields[name].value) for name in required)
        and all(name in candidate.measurements and not candidate.measurements[name].issues for name in spec.measurement_groups)
        and not any(item.candidate_id == candidate.candidate_id and item.severity == "review" for item in extraction.review)
    )


class ExtractionArchive(ContractModel):
    """A self-contained D1 snapshot; final records require explicit reviews.

    A human origin records the caller's attribution, not an independently
    authenticated identity. Synthetic fixtures are never labelled expert gold.
    """

    archive_schema_version: Literal["0.1"] = "0.1"
    document: Document
    extraction: ExtractionResult
    reviews: list[CandidateReview]
    records: list[FinalRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def verify_links(self) -> ExtractionArchive:
        source = self.extraction.source
        if source.sha256 != self.document.text_sha256 or source.character_count != self.document.character_count:
            raise ValueError("Extraction and document must reference identical source text")
        if source.document_id is not None and source.document_id != self.document.document_id:
            raise ValueError("Extraction document ID does not match")
        if source.name != self.document.source_name:
            raise ValueError("Extraction and document source names must agree")
        candidates = {item.candidate_id: item for item in self.extraction.candidates}
        if len(candidates) != len(self.extraction.candidates):
            raise ValueError("Candidate IDs must be unique")
        if self.extraction.run.candidate_count != len(candidates):
            raise ValueError("Run candidate count must agree with the candidate snapshot")
        for candidate in candidates.values():
            _validate_candidate(self.document, self.extraction, candidate)
        for item in self.extraction.review:
            if item.candidate_id not in candidates or (item.field is not None and item.field not in self.extraction.task.record_types[candidates[item.candidate_id].record_type].fields):
                raise ValueError("Extraction review items must reference existing candidates and task fields")
        for link in self.extraction.links:
            if len(link.candidate_ids) < 2 or len(link.candidate_ids) != len(set(link.candidate_ids)) or set(link.candidate_ids) - candidates.keys():
                raise ValueError("Candidate links must reference distinct existing candidates")
            names = link.compared_fields + link.differing_fields + link.unresolved_fields
            if any(set(names) - self.extraction.task.record_types[candidates[key].record_type].fields.keys()
                   for key in link.candidate_ids):
                raise ValueError("Candidate links must reference fields declared for their candidates")
        reviews = {item.candidate_id: item for item in self.reviews}
        if len(reviews) != len(self.reviews) or reviews.keys() != candidates.keys():
            raise ValueError("Each candidate needs exactly one review state")
        accepted = {key for key, review in reviews.items() if review.status == "accepted"}
        for key in accepted:
            if not _can_accept(self.extraction, candidates[key]):
                raise ValueError("Accepted candidates must pass required-field and exact-evidence checks")
        records = {item.source_candidate_id: item for item in self.records}
        if len(records) != len(self.records) or records.keys() != accepted:
            raise ValueError("Final records must correspond exactly to explicitly accepted candidates")
        for candidate_id, record in records.items():
            candidate = candidates[candidate_id]
            if record.document_id != self.document.document_id or record.record_id != _record_id(self.document.document_id, candidate_id):
                raise ValueError("Final record identity or document reference is invalid")
            if record.record_type != candidate.record_type or _canonical(record.model_dump()["fields"]) != _canonical(candidate.model_dump()["fields"]):
                raise ValueError("Final records must preserve their reviewed candidate fields and evidence")
        return self


def create_archive(
    document: Document, extraction: ExtractionResult,
    reviews: list[CandidateReview] | None = None,
) -> ExtractionArchive:
    """Create an archive; structural success alone never creates final records."""
    document = Document.model_validate(document.model_dump())
    extraction = ExtractionResult.model_validate(extraction.model_dump())
    explicit = [CandidateReview.model_validate(item.model_dump()) for item in (reviews or [])]
    by_id = {item.candidate_id: item for item in explicit}
    candidate_ids = {item.candidate_id for item in extraction.candidates}
    if len(by_id) != len(explicit) or by_id.keys() - candidate_ids:
        raise ValueError("Review IDs must be unique and refer to existing candidates")
    ordered_reviews = []
    records = []
    for candidate in extraction.candidates:
        review = by_id.get(candidate.candidate_id)
        if review is None:
            review = CandidateReview(
                candidate_id=candidate.candidate_id,
                status="needs_review" if candidate.needs_review else "unreviewed",
                origin="system", rationale="Unresolved structural or evidence checks." if candidate.needs_review else "No review decision has been supplied.",
            )
        ordered_reviews.append(review)
        if review.status == "accepted":
            records.append(FinalRecord(
                record_id=_record_id(document.document_id, candidate.candidate_id),
                document_id=document.document_id, source_candidate_id=candidate.candidate_id,
                record_type=candidate.record_type,
                fields={name: field.model_copy(deep=True) for name, field in candidate.fields.items()},
            ))
    return ExtractionArchive(document=document, extraction=extraction, reviews=ordered_reviews, records=records)
