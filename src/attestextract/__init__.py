"""AttestExtract: schema-driven extraction with source-linked evidence."""

from .decisions import DecisionBackend, DecisionResult, DecisionTask, validate_decision
from .models import (
    BackendResponse, Candidate, EvidenceRef, ExtractionBackend, ExtractionRequest, GenerationOptions,
    ExtractionResult, FieldSpec, RecordSpec, TaskSpec,
)
from .pipeline import extract, load_task
from .documents import Document, DocumentMetadata
from .archive import CandidateReview, ExtractionArchive, FinalRecord, create_archive
from .storage import load_archive, load_document, save_archive, save_document
from .diagnostics import RunTrace
from .config import BackendConfig, resolve_backend_config
from .registry import BackendRegistry, default_registry

__version__ = "0.1.0.dev8"

__all__ = [
    "__version__", "extract", "load_task", "TaskSpec", "RecordSpec", "FieldSpec",
    "ExtractionBackend", "ExtractionRequest", "BackendResponse", "ExtractionResult",
    "Candidate", "EvidenceRef", "DecisionBackend", "DecisionTask", "DecisionResult",
    "validate_decision",
    "Document", "DocumentMetadata", "CandidateReview", "FinalRecord", "ExtractionArchive",
    "create_archive", "save_archive", "load_archive", "save_document", "load_document",
    "RunTrace",
    "GenerationOptions", "BackendConfig", "resolve_backend_config", "BackendRegistry", "default_registry",
]
