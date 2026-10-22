"""AttestExtract: schema-driven extraction with source-linked evidence."""

from .decisions import DecisionBackend, DecisionResult, DecisionTask, validate_decision
from .models import (
    BackendResponse, Candidate, EvidenceRef, ExtractionBackend, ExtractionRequest,
    ExtractionResult, FieldSpec, RecordSpec, TaskSpec,
)
from .pipeline import extract, load_task

__version__ = "0.1.0.dev0"

__all__ = [
    "__version__", "extract", "load_task", "TaskSpec", "RecordSpec", "FieldSpec",
    "ExtractionBackend", "ExtractionRequest", "BackendResponse", "ExtractionResult",
    "Candidate", "EvidenceRef", "DecisionBackend", "DecisionTask", "DecisionResult",
    "validate_decision",
]
