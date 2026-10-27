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


class RecordSpec(ContractModel):
    description: str = ""
    fields: dict[str, FieldSpec] = Field(min_length=1)
    identity_fields: list[str] = Field(default_factory=list)
    condition_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_field_names(self) -> RecordSpec:
        if any(not name.strip() for name in self.fields):
            raise ValueError("Field names must not be blank")
        for names in (self.identity_fields, self.condition_fields):
            if len(names) != len(set(names)):
                raise ValueError("Identity and condition fields must not contain duplicates")
            if set(names) - self.fields.keys():
                raise ValueError("Identity and condition fields must reference declared fields")
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


class ReviewItem(ContractModel):
    candidate_id: str
    code: str
    field: str | None = None
    message: str


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
    schema_version: Literal["0.1"] = "0.1"
    package_version: str = "0.1.0.dev3"
    task: TaskSpec
    source: SourceInfo
    candidates: list[Candidate]
    review: list[ReviewItem]
    run: RunInfo
