"""Offline, self-contained decision materials; preparation never applies decisions."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from .archive import create_archive
from .decisions import DecisionTask
from .documents import Document
from .evidence import link_candidates
from .execution import _read_state, _State, digest
from .identity import can_ground_fields
from .models import (Candidate, CandidateLink, ContractModel, DocumentChunk,
                     EvidenceRef, ExtractionResult, SourceSpan, TaskSpec)
from .storage import _load, _reject_constant, _unique_object


class PreparedDecisionTask(DecisionTask):
    batch_id: str
    kind: Literal["candidate_review", "empty_chunk_review"]
    allowed_actions: list[Literal["select", "reject", "review"]]

    @model_validator(mode="after")
    def verify_scope(self) -> PreparedDecisionTask:
        if self.evidence:
            raise ValueError("Prepared tasks share the bundle source; evidence must not be duplicated")
        if self.kind == "candidate_review":
            if len(self.candidate_ids) != 1 or self.allowed_actions != ["select", "reject", "review"]:
                raise ValueError("A candidate review has one candidate and three advisory actions")
        elif self.candidate_ids or self.allowed_actions != ["review"]:
            raise ValueError("An empty chunk permits review only and has no selectable candidate")
        return self


class DecisionBatch(ContractModel):
    batch_id: str
    chunk_id: str
    segments: list[SourceSpan]
    candidate_ids: list[str]
    task_ids: list[str]


def _fresh(value: Any) -> Any:
    """Revalidate nested mutable model instances instead of trusting their type."""
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return {key: _fresh(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_fresh(item) for item in value]
    return value


def _intervals(segments: list[SourceSpan]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for span in sorted(segments, key=lambda item: (item.start, item.end)):
        if merged and span.start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], span.end))
        else:
            merged.append((span.start, span.end))
    return merged


def _verify_segments(segments: list[SourceSpan], length: int) -> None:
    if not segments or any(span.end <= span.start or span.end > length for span in segments):
        raise ValueError("Chunk segments must be nonempty intervals inside the original document")
    if any(left.end > right.start for left, right in zip(segments, segments[1:])):
        raise ValueError("Segments within a chunk must follow source order without overlap")


def _evidence_in(value: Any):
    if isinstance(value, EvidenceRef):
        yield value
    elif isinstance(value, BaseModel):
        for field in type(value).model_fields:
            yield from _evidence_in(getattr(value, field))
    elif isinstance(value, dict):
        for item in value.values():
            yield from _evidence_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from _evidence_in(item)


def _verify_candidate_scope(candidate: Candidate, chunks: dict[str, list[SourceSpan]]) -> None:
    if (not candidate.chunk_ids or len(set(candidate.chunk_ids)) != len(candidate.chunk_ids)
            or any(key not in chunks for key in candidate.chunk_ids)):
        raise ValueError("Every candidate must reference unique final leaf chunks")
    if len(candidate.chunk_ids) > 1 and not can_ground_fields(candidate.fields):
        raise ValueError("Unresolved candidates cannot have been deduplicated across chunks")
    for key in candidate.chunk_ids:
        spans = _intervals(chunks[key])
        for evidence in _evidence_in(candidate):
            for match in evidence.matches:
                if not any(start <= match.start and match.end <= end for start, end in spans):
                    raise ValueError("Candidate evidence must be inside every associated final leaf chunk")


def _materials(document: Document, extraction: ExtractionResult,
               chunks: list[tuple[str, list[SourceSpan]]]) -> tuple[list[DecisionBatch], list[PreparedDecisionTask]]:
    seed = digest({"document": document.model_dump(), "extraction": extraction.model_dump()})
    batches, tasks = [], []
    assigned: set[str] = set()
    for chunk_id, segments in chunks:
        batch_id = "batch-" + digest([seed, chunk_id, [span.model_dump() for span in segments]])[:24]
        candidate_ids = [item.candidate_id for item in extraction.candidates if chunk_id in item.chunk_ids]
        owned = [key for key in candidate_ids if key not in assigned]
        local_tasks = []
        for candidate_id in owned:
            local_tasks.append(PreparedDecisionTask(
                task_id="decision-" + digest([seed, "candidate_review", candidate_id])[:24],
                batch_id=batch_id, kind="candidate_review", candidate_ids=[candidate_id],
                allowed_actions=["select", "reject", "review"],
            ))
        if not candidate_ids:
            local_tasks.append(PreparedDecisionTask(
                task_id="decision-" + digest([seed, "empty_chunk_review", chunk_id])[:24],
                batch_id=batch_id, kind="empty_chunk_review", candidate_ids=[], allowed_actions=["review"],
            ))
        batches.append(DecisionBatch(batch_id=batch_id, chunk_id=chunk_id, segments=segments,
                                     candidate_ids=candidate_ids, task_ids=[item.task_id for item in local_tasks]))
        tasks.extend(local_tasks)
        assigned.update(owned)
    return batches, tasks


class DecisionBundle(ContractModel):
    decision_bundle_version: Literal["0.1"] = "0.1"
    analysis_package_version: str = Field(min_length=1)
    bundle_id: str
    document: Document
    extraction: ExtractionResult
    batches: list[DecisionBatch]
    tasks: list[PreparedDecisionTask]
    analysis_links: list[CandidateLink]

    @model_validator(mode="before")
    @classmethod
    def validate_fresh_inputs(cls, value: Any) -> Any:
        return _fresh(value)

    @model_validator(mode="after")
    def verify_materials(self) -> DecisionBundle:
        if self.extraction.schema_version != "0.3":
            raise ValueError("Decision bundle 0.1 supports extraction schema 0.3 only")
        # This verifies source identity, every original quote, field contracts,
        # candidate IDs, measurements and review references; accepts no facts.
        create_archive(self.document, self.extraction)
        chunks = {batch.chunk_id: batch.segments for batch in self.batches}
        if len(chunks) != len(self.batches) or len(chunks) != self.extraction.run.chunk_count:
            raise ValueError("Final leaf chunks must be unique and agree with the extraction run")
        for segments in chunks.values():
            _verify_segments(segments, self.document.character_count)
        coverage = _intervals([span for segments in chunks.values() for span in segments])
        expected_coverage = [(0, self.document.character_count)] if self.document.character_count else []
        if coverage != expected_coverage:
            raise ValueError("Final leaf chunks must cover the complete original document")
        for candidate in self.extraction.candidates:
            _verify_candidate_scope(candidate, chunks)
        expected_batches, expected_tasks = _materials(self.document, self.extraction, list(chunks.items()))
        if self.batches != expected_batches or self.tasks != expected_tasks:
            raise ValueError("Decision batches and tasks must cover each candidate once and every empty chunk")
        if self.analysis_links != link_candidates(self.extraction.candidates, self.extraction.task):
            raise ValueError("Analysis links must be derived from the unchanged extraction snapshot")
        expected_id = "decision-bundle-" + digest(self.model_dump(exclude={"bundle_id"}))[:24]
        if self.bundle_id != expected_id:
            raise ValueError("Decision bundle content checksum does not match its ID")
        return self


class _PlannedChunk(ContractModel):
    chunk_id: str
    character_count: int = Field(ge=0)
    character_target_exceeded: bool
    segments: list[SourceSpan]


class _ChunkPlan(ContractModel):
    chunks: list[_PlannedChunk]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_bytes().decode("utf-8-sig"),
                      object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def _source_file(directory: Path, relative: str) -> Path:
    target = (directory / relative).resolve()
    if not target.is_relative_to(directory) or not target.is_file():
        raise ValueError(f"Required decision source is missing or outside the run: {relative}")
    return target


def _final_leaves(state: _State, plan: _ChunkPlan, length: int) -> list[str]:
    roots = [chunk.chunk_id for chunk in plan.chunks]
    if roots != state.roots or len(set(roots)) != len(roots):
        raise ValueError("Checkpoint roots must match the saved chunk plan")
    planned = {chunk.chunk_id: chunk for chunk in plan.chunks}
    pending = [(key, 0) for key in roots]
    visited: set[str] = set()
    leaves = []
    while pending:
        key, depth = pending.pop(0)
        if (key in visited or key not in state.nodes
                or re.fullmatch(r"chunk-[0-9]+(?:\.[0-9]+)*", key) is None):
            raise ValueError("Checkpoint chunk tree has duplicate, missing or unsafe node IDs")
        node = state.nodes[key]
        visited.add(key)
        _verify_segments(node.segments, length)
        if node.depth != depth or node.calls < 1:
            raise ValueError("Checkpoint chunk depth or call reservation is invalid")
        if key in planned:
            root = planned[key]
            if node.segments != root.segments or root.character_count != sum(s.end - s.start for s in root.segments):
                raise ValueError("Checkpoint root segments must match the saved chunk plan")
        if node.status == "split":
            if (len(node.children) < 2 or node.cache_hash is not None
                    or node.children != [f"{key}.{number}" for number in range(1, len(node.children) + 1)]
                    or any(child not in state.nodes for child in node.children)):
                raise ValueError("Checkpoint split parent has invalid children")
            children = [span for child in node.children for span in state.nodes[child].segments]
            if _intervals(children) != _intervals(node.segments):
                raise ValueError("Checkpoint children must preserve all parent source positions")
            parent_size = sum(span.end - span.start for span in node.segments)
            if any(sum(span.end - span.start for span in state.nodes[child].segments) >= parent_size
                   for child in node.children):
                raise ValueError("Checkpoint split children must be smaller than their parent")
            pending[0:0] = [(child, depth + 1) for child in node.children]
        elif node.status != "completed" or node.children or not node.cache_hash:
            raise ValueError("Decision preparation requires every final leaf to be completed")
        else:
            leaves.append(key)
    if visited != set(state.nodes):
        raise ValueError("Checkpoint contains unreachable chunk nodes")
    if (sum(node.calls for node in state.nodes.values()) != state.calls_used
            or state.calls_used > state.max_model_calls or state.response_count > state.calls_used
            or state.request_attempts < state.response_count or any(value < 0 for value in state.usage.values())):
        raise ValueError("Checkpoint call ledger is inconsistent")
    return leaves


def _prepare_decision_bundle(run_dir: str | Path) -> DecisionBundle:
    """Read a successful trace's whitelist only; never read prior-run history paths."""
    from . import __version__

    root = Path(run_dir).resolve()
    if not root.is_dir():
        raise ValueError("Decision preparation requires a successful run directory")
    directory = (root / "diagnostics").resolve() if (root / "diagnostics").is_dir() else root
    if not directory.is_relative_to(root):
        raise ValueError("Diagnostics must stay inside the supplied run directory")
    manifest = _read_json(_source_file(directory, "manifest.json"))
    if not isinstance(manifest, dict) or manifest.get("trace_schema_version") != "0.3":
        raise ValueError("Decision preparation supports diagnostic schema 0.3 only")
    if manifest.get("status") != "succeeded":
        raise ValueError("Decision preparation requires a succeeded run, not partial or failed results")
    required = ["document.json", "task.json", "result.json", "chunk_plan.json", "execution/checkpoint.json"]
    artifacts = manifest.get("artifacts")
    if (not isinstance(artifacts, list) or any(not isinstance(item, str) for item in artifacts)
            or not set(required).issubset(artifacts)):
        raise ValueError("The successful run manifest does not contain complete decision diagnostics")
    document = Document.model_validate(_read_json(_source_file(directory, "document.json")))
    extraction = ExtractionResult.model_validate(_read_json(_source_file(directory, "result.json")))
    task = TaskSpec.model_validate(_read_json(_source_file(directory, "task.json")))
    if task != extraction.task:
        raise ValueError("Saved task and extraction snapshot disagree")
    if manifest.get("run") != extraction.run.model_dump() or manifest.get("package_version") != extraction.package_version:
        raise ValueError("Run manifest and extraction snapshot disagree")
    plan = _ChunkPlan.model_validate(_read_json(_source_file(directory, "chunk_plan.json")))
    checkpoint_path = _source_file(directory, "execution/checkpoint.json")
    try:
        state = _read_state(_read_json(checkpoint_path))
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("Decision source checkpoint checksum or format is invalid") from exc
    leaves = _final_leaves(state, plan, document.character_count)
    if (len(leaves) != extraction.run.chunk_count or state.output_protocol != extraction.run.output_protocol
            or state.response_count != extraction.run.response_count
            or state.request_attempts != extraction.run.request_attempts or state.usage != extraction.run.usage
            or state.model != extraction.run.model):
        raise ValueError("Checkpoint completion ledger and extraction run disagree")
    chunks = []
    for key in leaves:
        relative = f"chunks/{key}.json"
        if relative not in manifest["artifacts"]:
            raise ValueError("The run manifest is missing a completed leaf chunk")
        chunk = DocumentChunk.model_validate(_read_json(_source_file(directory, relative)))
        if (chunk.chunk_id != key or chunk.segments != state.nodes[key].segments
                or chunk.text != "".join(document.text[span.start:span.end] for span in chunk.segments)):
            raise ValueError("Saved leaf chunk must match checkpoint positions and exact original text")
        chunks.append((key, chunk.segments))
    batches, tasks = _materials(document, extraction, chunks)
    payload = {"decision_bundle_version": "0.1", "analysis_package_version": __version__,
               "document": document.model_dump(),
               "extraction": extraction.model_dump(), "batches": [batch.model_dump() for batch in batches],
               "tasks": [task.model_dump() for task in tasks],
               "analysis_links": [link.model_dump() for link in link_candidates(extraction.candidates, extraction.task)]}
    payload["bundle_id"] = "decision-bundle-" + digest(payload)[:24]
    return DecisionBundle.model_validate(payload)


def prepare_decision_bundle(run_dir: str | Path) -> DecisionBundle:
    """Prepare offline materials, keeping original source text out of validation errors."""
    try:
        return _prepare_decision_bundle(run_dir)
    except ValidationError:
        raise ValueError("Decision source validation failed: document, extraction, task or chunk contracts are inconsistent.") from None


def load_decision_bundle(path: str | Path) -> DecisionBundle:
    try:
        return _load(path, DecisionBundle)
    except ValidationError:
        raise ValueError("Decision bundle validation failed: content, source positions or decision references are inconsistent.") from None


def save_decision_bundle(bundle: DecisionBundle, path: str | Path) -> None:
    """Validate before exclusively creating a new artifact; never overwrite a run."""
    try:
        checked = DecisionBundle.model_validate(bundle.model_dump())
    except ValidationError:
        raise ValueError("Decision bundle validation failed; no artifact was saved.") from None
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(checked.model_dump_json(indent=2) + "\n")


def decision_bundle_summary(bundle: DecisionBundle) -> dict[str, Any]:
    bundle = DecisionBundle.model_validate(bundle.model_dump())
    old_links = {tuple(link.candidate_ids): link for link in bundle.extraction.links}
    new_links = {tuple(link.candidate_ids): link for link in bundle.analysis_links}
    changed = sum(old_links.get(key) != new_links.get(key) for key in old_links.keys() | new_links.keys())
    reviewed = sum(candidate.needs_review for candidate in bundle.extraction.candidates)
    empty = sum(not batch.candidate_ids for batch in bundle.batches)
    return {"bundle_id": bundle.bundle_id, "analysis_package_version": bundle.analysis_package_version,
            "batch_count": len(bundle.batches),
            "candidate_count": len(bundle.extraction.candidates),
            "candidate_task_count": len(bundle.extraction.candidates), "empty_chunk_count": empty,
            "task_count": len(bundle.tasks), "review_candidate_count": reviewed,
            "unflagged_candidate_count": len(bundle.extraction.candidates) - reviewed,
            "analysis_link_change_count": changed, "model_calls": 0}
