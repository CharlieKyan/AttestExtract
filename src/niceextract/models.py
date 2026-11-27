"""Small, versioned contracts shared by extraction and decision backends."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Protocol, get_args

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr,
    model_validator, model_serializer,
)


Scalar = StrictStr | StrictInt | StrictFloat | StrictBool | None
OutputProtocol = Literal["flat-v1", "native-v1"]
NameHandling = Literal["as_reported", "compare_names", "related", "uncertain"]
StatementBasis = Literal["reported", "inferred", "unknown"]
ValueRole = Literal["label", "description", "quantity", "unknown"]


def _read_value_role(value):
    return value if value in get_args(ValueRole) else "unknown"


# This optional interpretation is independent of the original value and its evidence.
ModelValueRole = Annotated[ValueRole, BeforeValidator(_read_value_role)]
ReportedUse = Literal["measured", "model_input", "process_setting", "derived_setting",
                      "boundary_condition", "classification_threshold", "sensitivity_input_range", "unspecified"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class RelationCitation(ContractModel):
    quote: str = Field(min_length=1)
    ref: str | None


class RowRelation(ContractModel):
    """A directed first-pass proposal from this observation's entity to a row."""

    target: str = Field(min_length=1)
    predicate: Literal["part_of", "sample_of", "member_of", "located_in"]
    field_applicability: Literal["included", "excluded", "uncertain"]
    evidence: list[RelationCitation]
    target_basis: StatementBasis | None = None

    @model_serializer(mode="wrap")
    def _serialize_relation(self, handler):
        result = handler(self)
        if self.target_basis is None:
            result.pop("target_basis", None)
        return result


class RowRelationAlternatives(ContractModel):
    """Explicit alternatives for one observation, never multiple measurements."""

    alternatives: list[RowRelation] = Field(min_length=2)

    @model_validator(mode="after")
    def distinct_targets(self):
        if len({item.target.strip() for item in self.alternatives}) != len(self.alternatives):
            raise ValueError("Each alternative must identify a distinct source target.")
        return self


RowRelationProposal = RowRelation | RowRelationAlternatives


class ExplainedRowRelation(ContractModel):
    """The model's account of a specific owner's connection to a proposed row."""

    target: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    field_applicability: Literal["included", "excluded", "uncertain"]
    evidence: list[RelationCitation]
    target_basis: StatementBasis = "unknown"
    target_contains_direct: bool | None = None
    target_source_name: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def nonblank(self):
        if not self.target.strip() or not self.explanation.strip():
            raise ValueError("The target and explanation must contain text.")
        return self

    @model_serializer(mode="wrap")
    def _serialize_explanation(self, handler):
        result = handler(self)
        if self.target_source_name is None:
            result.pop("target_source_name", None)
        return result


class RowRelationIssue(ContractModel):
    status: Literal["malformed"] = "malformed"
    reason: str
    raw: Any


class LocatedRelationCitation(RelationCitation):
    start: int | None
    end: int | None


class RowRelationReceipt(RowRelation):
    alternative: bool = False
    status: Literal["located", "unresolved"]
    evidence: list[LocatedRelationCitation]

    @model_validator(mode="after")
    def check_locations(self):
        _check_relation_citations(self.evidence, self.status)
        return self


class ExplainedRelationReceipt(ExplainedRowRelation):
    alternative: bool = False
    status: Literal["located", "unresolved"]
    evidence: list[LocatedRelationCitation]

    @model_validator(mode="after")
    def check_locations(self):
        _check_relation_citations(self.evidence, self.status)
        return self


def _check_relation_citations(evidence, status):
    for citation in evidence:
        if (citation.start is None) != (citation.end is None):
            raise ValueError("Relation citation offsets must be paired.")
        if citation.start is not None and (citation.start < 0 or citation.end - citation.start != len(citation.quote)):
            raise ValueError("Relation citation offsets must enclose its original quote.")
    if (status == "located") != (bool(evidence) and all(c.start is not None for c in evidence)):
        raise ValueError("Relation location status must match its citations.")


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


class TaskExampleExtraction(ContractModel):
    """One user-labelled observation; source quotations remain unchanged."""

    field: str = Field(min_length=1)
    value: str = Field(min_length=1)
    entity: str | None = None
    unit: str | None = None
    conditions: str | None = None
    quote: str = ""
    quotes: dict[str, str] = Field(default_factory=dict)


class TaskInputExample(ContractModel):
    text: str = Field(min_length=1)
    extractions: list[TaskExampleExtraction]


class FieldTypePlan(ContractModel):
    kind: Literal["text", "numeric", "mixed", "unknown"]
    shapes: list[Literal["scalar", "range", "bound"]] = Field(default_factory=list)
    source: Literal["user", "model"] = "user"

    @model_validator(mode="after")
    def check_shapes(self):
        if self.kind in ("numeric", "mixed") and "shapes" not in self.model_fields_set:
            self.shapes = ["scalar", "range", "bound"]
        if self.kind in ("numeric", "mixed") and not self.shapes:
            raise ValueError("Numeric and mixed fields need at least one numeric shape.")
        if self.kind in ("text", "unknown") and self.shapes or len(self.shapes) != len(set(self.shapes)):
            raise ValueError("Shapes must be distinct and apply only to numeric or mixed fields.")
        return self


class SimpleTaskInput(ContractModel):
    """The complete target list and optional user-labelled examples."""

    prompt_description: str = Field(min_length=1)
    field_list: dict[str, str] = Field(min_length=1)
    examples: list[TaskInputExample] = Field(default_factory=list)
    field_types: dict[str, FieldTypePlan] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def _serialize_inputs(self, handler):
        result = handler(self)
        if not self.field_types:
            result.pop("field_types", None)
        return result


class TaskInputMaterials(SimpleTaskInput):
    """Versioned inputs bound to a deterministically compiled task."""

    input_version: Literal["0.1"] = "0.1"
    compiler_revision: Literal["simple-observation-v1"] = "simple-observation-v1"


class TaskSpec(ContractModel):
    name: str = Field(min_length=1)
    description: str = ""
    record_types: dict[str, RecordSpec] = Field(min_length=1)
    input_materials: TaskInputMaterials | None = None
    field_types: dict[str, FieldTypePlan] = Field(default_factory=dict)

    row_subject: str | None = Field(default=None, min_length=1, max_length=1000)
    row_relation_protocol: Literal["evidence-v1", "explanation-v1"] | None = None
    statement_protocol: Literal["source-v1", "source-v2"] | None = None

    def typed_value(self, record_type: str, field: str) -> bool:
        return field == "value" and record_type in self.field_types

    @model_validator(mode="after")
    def check_names(self) -> TaskSpec:
        if not self.name.strip() or any(not name.strip() for name in self.record_types):
            raise ValueError("Task and record type names must not be blank")
        if self.row_relation_protocol and (not self.row_subject or self.input_materials is None):
            raise ValueError("Row relation evidence requires a field_list task with a row subject.")
        if self.statement_protocol and self.input_materials is None:
            raise ValueError("Source statements require a field_list task.")
        if self.field_types and (not self.input_materials or self.field_types.keys() - self.record_types.keys()):
            raise ValueError("Field types must refer to declared field_list fields.")
        if self.row_relation_protocol == "explanation-v1" and self.statement_protocol != "source-v2":
            raise ValueError("Open row explanations require source-v2.")
        if self.input_materials is not None:
            from .task_input import validate_compiled_task
            validate_compiled_task(self)
        return self

    @model_serializer(mode="wrap")
    def _serialize_task(self, handler):
        result = handler(self)
        if self.input_materials is None:
            result.pop("input_materials", None)
        if not self.field_types:
            result.pop("field_types", None)
        if self.row_subject is None:
            result.pop("row_subject", None)
        if self.row_relation_protocol is None:
            result.pop("row_relation_protocol", None)
        if self.statement_protocol is None:
            result.pop("statement_protocol", None)
        return result


class SourceSpan(ContractModel):
    """Half-open Unicode character offsets in the original input string."""

    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def check_order(self) -> SourceSpan:
        if self.end < self.start:
            raise ValueError("Span end must be at least start")
        return self


class ContextOptions(ContractModel):
    """Explicit context policy; none preserves the pre-D4 request scope."""

    mode: Literal["none", "structure", "neighbors"] = "none"
    neighbor_chars: int = Field(default=500, ge=0, le=5000)


class ContextSegment(ContractModel):
    role: Literal["heading", "table_header", "table_row_header", "table_caption", "table_footnote",
                  "neighbor_before", "neighbor_after"]
    span: SourceSpan


class ContextPassage(ContextSegment):
    quote: str


class DocumentChunk(ContractModel):
    chunk_id: str
    text: str
    segments: list[SourceSpan]
    body_segments: list[SourceSpan] = Field(default_factory=list)
    context_segments: list[ContextSegment] = Field(default_factory=list)
    context_policy: ContextOptions | None = None
    context_diagnostics: list[str] = Field(default_factory=list)


class GenerationOptions(ContractModel):
    """Portable generation controls; omitted values leave service defaults intact."""

    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, gt=0)


class ExecutionOptions(ContractModel):
    """Bounded generation invocations; HTTP retries remain adapter-controlled."""

    adaptive_chunking: bool = True
    max_model_calls: int | None = Field(default=None, gt=0)
    max_split_depth: int = Field(default=3, ge=0, le=8)
    min_chunk_chars: int = Field(default=500, ge=1)
    planning_output_tokens: int = Field(default=8192, gt=0)


class ExtractionRequest(ContractModel):
    messages: list[dict[str, str]]
    chunk_id: str
    output_schema: dict[str, Any] | None = None
    output_protocol: OutputProtocol = "flat-v1"
    format_attempt: int = Field(default=1, ge=1, le=2)
    generation: GenerationOptions = Field(default_factory=GenerationOptions)
    artifact_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+$", exclude=True)

    @property
    def artifact_stem(self) -> str:
        """Keep the first response's paths stable and preserve regeneration logs."""
        if self.artifact_id is not None:
            return self.artifact_id
        return self.chunk_id if self.format_attempt == 1 else f"{self.chunk_id}.format-{self.format_attempt}"


class BackendResponse(ContractModel):
    text: str
    model: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    attempts: int = Field(default=1, ge=1)
    elapsed_seconds: float = Field(default=0, ge=0)
    finish_reason: str | None = None
    request_id: str | None = None
    reported_model: str | None = None


class ExtractionBackend(Protocol):
    name: str

    def generate(self, request: ExtractionRequest) -> BackendResponse: ...


class EvidenceMatch(ContractModel):
    """An exact source slice retrieved through a conservative search view."""

    source_quote: str
    span: SourceSpan
    method: Literal["normalized"] = "normalized"
    rules: list[Literal["whitespace", "punctuation", "markdown_range"]] = Field(min_length=1)


class TableReference(ContractModel):
    table_id: str
    cell_id: str
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    rowspan: int = Field(ge=1)
    colspan: int = Field(ge=1)
    role: Literal["header", "row_header", "data"]


class EvidenceRef(ContractModel):
    quote: str
    status: Literal["matched", "normalized", "ambiguous", "unmatched", "missing"]
    start: int | None = None
    end: int | None = None
    matches: list[SourceSpan] = Field(default_factory=list)
    source_matches: list[EvidenceMatch] = Field(default_factory=list)
    table_refs: list[TableReference] = Field(default_factory=list)


class FieldValue(ContractModel):
    value: Scalar
    evidence: EvidenceRef


class MeasurementInput(ContractModel):
    context_quote: str
    value_text: str
    unit_text: str = ""
    unit_quote: str = ""


class RangeBinding(ContractModel):
    """Locally derived range syntax; every component retains its exact source span."""

    endpoints: list[EvidenceRef] = Field(min_length=2, max_length=2)
    separator: EvidenceRef
    units: list[EvidenceRef] = Field(min_length=1, max_length=2)
    unit_layout: Literal["shared", "per_endpoint", "table_header"]


class MeasurementEvidence(ContractModel):
    context: EvidenceRef
    value: EvidenceRef
    unit: EvidenceRef
    unit_context: EvidenceRef | None = None
    unit_source: Literal["context", "table_header"]
    condition_fields: list[str]
    issues: list[str] = Field(default_factory=list)
    semantic_status: Literal["not_verified"] = "not_verified"
    range_binding: RangeBinding | None = None


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
    source_context: list[ContextPassage] = Field(default_factory=list)
    name_handling: NameHandling | None = None
    row_relations: list[RowRelationReceipt | ExplainedRelationReceipt] = Field(default_factory=list)
    row_relation_issues: list[RowRelationIssue] = Field(default_factory=list)
    statement_basis: StatementBasis | None = None
    reported_use: ReportedUse | None = None
    source_property: str | None = Field(default=None, min_length=1)
    value_role: ValueRole | None = None

    @model_serializer(mode="wrap")
    def _serialize_candidate(self, handler):
        result = handler(self)
        if self.name_handling is None:
            result.pop("name_handling", None)
        if not self.row_relations:
            result.pop("row_relations", None)
        if not self.row_relation_issues:
            result.pop("row_relation_issues", None)
        for key in ("statement_basis", "reported_use", "source_property", "value_role"):
            if getattr(self, key) is None:
                result.pop(key, None)
        return result


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


class GenerationAttempt(ContractModel):
    """One reserved generation, including failures whose billing is unknown."""

    attempt_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    chunk_id: str
    route_id: str
    status: Literal["reserved", "responded", "failed", "rejected", "completed"] = "reserved"
    error_code: str | None = None
    model: str | None = None
    reported_model: str | None = None
    request_id: str | None = None
    http_attempts: int = Field(default=0, ge=0)
    usage: dict[str, int] = Field(default_factory=dict)
    elapsed_seconds: float = Field(default=0, ge=0)
    outcome_unknown: bool = False
    response_received: bool = False
    format_attempt: int = Field(default=1, ge=1, le=2)


class ChunkProvenance(ContractModel):
    route_id: str
    attempt_id: str
    model: str | None = None
    reported_model: str | None = None


class RunInfo(ContractModel):
    backend: str
    mode: Literal["live", "replay"]
    output_protocol: OutputProtocol = "native-v1"
    chunk_count: int
    response_count: int
    candidate_count: int
    request_attempts: int
    model: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    elapsed_seconds: float
    routes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    chunk_provenance: dict[str, ChunkProvenance] = Field(default_factory=dict)
    generation_attempts: list[GenerationAttempt] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _serialize_run(self, handler):
        result = handler(self)
        if not self.routes:
            for name in ("routes", "chunk_provenance", "generation_attempts"):
                result.pop(name, None)
        return result


def _package_version() -> str:
    # Resolve at result creation: package exports import models during startup.
    from . import __version__
    return __version__


class TableExtractionResult(ContractModel):
    """One table or owned window's records, separate from prose post-processing."""

    schema_version: Literal["table-route-v3"] = "table-route-v3"
    processing_policy: Literal["observe_only"] = "observe_only"
    table_id: str
    chunk_id: str
    status: Literal["checked", "issues_found", "partial_format_error", "format_error"]
    candidates: list[dict[str, Any]]
    evidence: dict[str, Any]
    issues: list[dict[str, Any]]
    rejections: list[dict[str, Any]]
    parsed: dict[str, Any]
    response: BackendResponse
    semantic_verification: Literal["not_run"] = "not_run"


class ExtractionResult(ContractModel):
    schema_version: Literal["0.1", "0.2", "0.3", "0.4", "0.5", "0.6"] = "0.4"
    package_version: str = Field(default_factory=_package_version)
    task: TaskSpec
    source: SourceInfo
    candidates: list[Candidate]
    review: list[ReviewItem]
    links: list[CandidateLink] = Field(default_factory=list)
    run: RunInfo
    table_results: list[TableExtractionResult] = Field(default_factory=list)
    format_status: Literal["complete", "partial_format_error", "format_error"] = "complete"
    # Independent, versioned analysis product. The original extraction archive
    # remains byte-contract compatible; saved analysis lives in analysis.json.
    analysis: dict[str, Any] | None = Field(default=None, exclude=True)
    dataset: dict[str, Any] | None = Field(default=None, exclude=True)

    @model_serializer(mode="wrap")
    def _serialize_result(self, handler):
        result = handler(self)
        if self.schema_version != "0.6":
            result.pop("table_results", None)
            result.pop("format_status", None)
        return result

    @model_validator(mode="after")
    def _provenance_version(self):
        automatic = bool(self.run.routes or self.run.chunk_provenance or self.run.generation_attempts)
        if self.schema_version not in {"0.5", "0.6"} and automatic:
            raise ValueError("Automatic provenance requires result schema 0.5 or 0.6.")
        if (self.schema_version == "0.5" or automatic) and (not self.run.routes or self.run.backend != "automatic"
                                             or self.run.model is not None):
            raise ValueError("Result schema 0.5 requires per-chunk automatic provenance.")
        if bool(self.table_results) != (self.schema_version == "0.6"):
            raise ValueError("Table results require result schema 0.6 and at least one table.")
        if self.schema_version != "0.6" and self.format_status != "complete":
            raise ValueError("Partial table format status requires result schema 0.6.")
        return self
