"""Three synthetic, manually specified D1 archives; no expert data or inference."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Literal

from pydantic import Field, TypeAdapter

from .archive import CandidateReview, ExtractionArchive, create_archive
from .backends import ReplayBackend
from .documents import Document
from .models import ContractModel, ExtractionResult, TaskSpec
from .pipeline import extract


_NAMES = ("attribute", "observation", "relation")


class _FixtureReview(ContractModel):
    candidate_index: int = Field(ge=0)
    status: Literal["accepted", "rejected", "needs_review", "unreviewed"]
    reviewer: str | None = None
    rationale: str = ""
    origin: Literal["synthetic_fixture"] = "synthetic_fixture"
    annotation_ref: str | None = None


def d1_example_names() -> tuple[str, ...]:
    """Return the names of the packaged, synthetic D1 examples."""
    return _NAMES


def _fixture_reviews(result: ExtractionResult, payload: object) -> list[CandidateReview]:
    """Resolve explicit fixture indices without silently skipping bad references."""
    specifications = TypeAdapter(list[_FixtureReview]).validate_python(payload)
    seen: set[int] = set()
    reviews = []
    for specification in specifications:
        index = specification.candidate_index
        if index >= len(result.candidates):
            raise ValueError("Fixture candidate_index is outside the extracted candidate list")
        if index in seen:
            raise ValueError("Fixture candidate_index must not be repeated")
        seen.add(index)
        reviews.append(CandidateReview(
            candidate_id=result.candidates[index].candidate_id,
            status=specification.status,
            reviewer=specification.reviewer,
            rationale=specification.rationale,
            origin=specification.origin,
            annotation_ref=specification.annotation_ref,
        ))
    return reviews


def load_d1_example(name: str) -> ExtractionArchive:
    """Replay one synthetic example and apply its explicit fixture review labels.

    The labels and expected semantic output are hand-written demonstration data,
    not real expert validation, live model predictions, or accuracy measurements.
    """
    if name not in _NAMES:
        raise ValueError(f"Unknown D1 example; choose one of {', '.join(_NAMES)}")
    resources = files("attestextract").joinpath("examples", "d1", name)
    # Decode bytes directly: evidence offsets refer to this exact source string.
    text = resources.joinpath("source.txt").read_bytes().decode("utf-8")
    task = TaskSpec.model_validate_json(resources.joinpath("task.json").read_text(encoding="utf-8"))
    response = json.loads(resources.joinpath("response.json").read_text(encoding="utf-8"))
    labels = json.loads(resources.joinpath("reviews.json").read_text(encoding="utf-8"))
    document = Document.from_text(text, source_name=f"synthetic-d1-{name}.txt")
    result = extract(document, task, backend=ReplayBackend([response]))
    return create_archive(document, result, reviews=_fixture_reviews(result, labels))
