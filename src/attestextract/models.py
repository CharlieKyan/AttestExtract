"""Small, versioned contracts shared by extraction and decision backends."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import (
    BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr,
    model_validator,
)


Scalar = StrictStr | StrictInt | StrictFloat | StrictBool | None


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class FieldSpec(ContractModel):
    type: Literal["string", "integer", "number", "boolean"]
    description: str = ""
    required: bool = False
    required_when: dict[str, list[Scalar]] = Field(default_factory=dict)
    not_applicable_when: dict[str, list[Scalar]] = Field(default_factory=dict)


class MeasurementSpec(ContractModel):
    value_field: str
    unit_field: str
    condition_fields: list[str] = Field(default_factory=list)


class RecordSpec(ContractModel):
    description: str = ""
    fields: dict[str, FieldSpec] = Field(min_length=1)
    identity_fields: list[str] = Field(default_factory=list)
    condition_fields: list[str] = Field(default_factory=list)
    measurement_groups: dict[str, MeasurementSpec] = Field(default_factory=dict)
    comparison_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_field_names(self) -> RecordSpec:
        if any(not name.strip() for name in self.fields):
            raise ValueError("Field names must not be blank")
        for names in (self.identity_fields, self.condition_fields, self.comparison_fields):
            if len(names) != len(set(names)):
                raise ValueError("Identity and condition fields must not contain duplicates")
            if set(names) - self.fields.keys():
                raise ValueError("Identity and condition fields must reference declared fields")
        for name, field in self.fields.items():
            for rule in (field.required_when, field.not_applicable_when):
                if set(rule) - self.fields.keys() or name in rule or any(not values for values in rule.values()):
                    raise ValueError("Field applicability rules need other declared fields and nonempty value lists")
                for reference, values in rule.items():
                    types = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,)}
                    if any(type(value) not in types[self.fields[reference].type]
                           or (isinstance(value, str) and not value.strip()) for value in values):
                        raise ValueError("Applicability predicates must be nonempty values of the referenced field type")
        if self.comparison_fields and not self.identity_fields:
            raise ValueError("Candidate associations require declared identity fields")
        for name, group in self.measurement_groups.items():
            references = [group.value_field, group.unit_field, *group.condition_fields]
            if not name.strip() or len(references) != len(set(references)) or set(references) - self.fields.keys():
                raise ValueError("Measurement groups need distinct declared value, unit and condition fields")
        return self


class TaskSpec(ContractModel):
    name: str = Field(min_length=1)
    description: str = ""
    record_types: dict[str, RecordSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def check_names(self) -> TaskSpec:
        if not self.name.strip() or any(not name.strip() for name in self.record_types):
            raise ValueError("Task and record type names must not be blank")
        return self


class SourceSpan(ContractModel):
    """Half-open Unicode character offsets in the original input string."""

    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def check_order(self) -> SourceSpan:
        if self.end < self.start:
            raise ValueError("Span end must be at least start")
        return self


class DocumentChunk(ContractModel):
    chunk_id: str
    text: str
    segments: list[SourceSpan]


class ExtractionRequest(ContractModel):
    messages: list[dict[str, str]]
    chunk_id: str
    output_schema: dict[str, Any] | None = None
    format_attempt: int = Field(default=1, ge=1, le=2)

    @property
    def artifact_stem(self) -> str:
        """Keep the first response's paths stable and preserve regeneration logs."""
        return self.chunk_id if self.format_attempt == 1 else f"{self.chunk_id}.format-{self.format_attempt}"


class BackendResponse(ContractModel):
    text: str
    model: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    attempts: int = Field(default=1, ge=1)
    elapsed_seconds: float = Field(default=0, ge=0)


class ExtractionBackend(Protocol):
    name: str

    def generate(self, request: ExtractionRequest) -> BackendResponse: ...


class EvidenceRef(ContractModel):
    quote: str
    status: Literal["matched", "ambiguous", "unmatched", "missing"]
    start: int | None = None
    end: int | None = None
    matches: list[SourceSpan] = Field(default_factory=list)


class FieldValue(ContractModel):
    value: Scalar
    evidence: EvidenceRef


class MeasurementInput(ContractModel):
    context_quote: str
    value_text: str
    unit_text: str = ""
    unit_quote: str = ""


class MeasurementEvidence(ContractModel):
    context: EvidenceRef
    value: EvidenceRef
    unit: EvidenceRef
    unit_context: EvidenceRef | None = None
    unit_source: Literal["context", "table_header"]
    condition_fields: list[str]
    issues: list[str] = Field(default_factory=list)
    semantic_status: Literal["not_verified"] = "not_verified"


class CheckResult(ContractModel):
    code: str
    passed: bool
    field: str | None = None


class Candidate(ContractModel):
    candidate_id: str
    record_type: str
    fields: dict[str, FieldValue]
    chunk_ids: list[str]
    checks: list[CheckResult]
    needs_review: bool
    field_status: dict[str, Literal["reported", "not_reported", "not_applicable", "required_missing"]] = Field(default_factory=dict)
    measurements: dict[str, MeasurementEvidence] = Field(default_factory=dict)


class ReviewItem(ContractModel):
    candidate_id: str
    code: str
    field: str | None = None
    message: str
    severity: Literal["review", "info"] = "review"


class CandidateLink(ContractModel):
    kind: Literal["possible_repeat", "possible_conflict", "insufficient_context"]
    candidate_ids: list[str]
    compared_fields: list[str]
    differing_fields: list[str] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)


class SourceInfo(ContractModel):
    name: str | None = None
    sha256: str
    character_count: int
    document_id: str | None = None


class RunInfo(ContractModel):
    backend: str
    mode: Literal["live", "replay"]
    chunk_count: int
    response_count: int
    candidate_count: int
    request_attempts: int
    model: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    elapsed_seconds: float


class ExtractionResult(ContractModel):
    schema_version: Literal["0.1", "0.2"] = "0.2"
    package_version: str = "0.1.0.dev7"
    task: TaskSpec
    source: SourceInfo
    candidates: list[Candidate]
    review: list[ReviewItem]
    links: list[CandidateLink] = Field(default_factory=list)
    run: RunInfo
