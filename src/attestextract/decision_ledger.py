"""Validate offline decision suggestions without applying or accepting them."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from .decision_bundle import DecisionBundle, PreparedDecisionTask
from .decisions import DecisionResult, validate_decision
from .evidence import is_missing, rule_matches
from .models import Candidate, ContractModel, ExtractionResult
from .storage import _reject_constant, _unique_object


class DecisionBlocker(ContractModel):
    """An existing unresolved check; no semantic truth is inferred here."""

    code: str
    candidate_id: str | None = None
    field: str | None = None


class DecisionLedgerEntry(ContractModel):
    response_index: int = Field(ge=0)
    task_id: str | None = None
    result: DecisionResult | None = None
    status: Literal["valid", "invalid"]
    code: str | None = None
    message: str
    recommendation: Literal["suggest_keep", "suggest_reject", "needs_review"] | None = None
    blocking_reasons: list[DecisionBlocker] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_result(self) -> DecisionLedgerEntry:
        if self.status == "valid":
            if self.result is None or self.task_id != self.result.task_id or self.code is not None:
                raise ValueError("A valid ledger entry requires a validated task result")
            expected = {"select": "suggest_keep", "reject": "suggest_reject", "review": "needs_review"}
            if self.recommendation != expected[self.result.action]:
                raise ValueError("A recommendation must preserve its original action")
        elif self.result is not None or self.recommendation is not None or self.code is None:
            raise ValueError("An invalid ledger entry cannot contain an accepted suggestion")
        return self


class DecisionLedger(ContractModel):
    decision_ledger_version: Literal["0.1"] = "0.1"
    bundle_id: str
    mode: Literal["simulation_replay"] = "simulation_replay"
    status: Literal["valid", "invalid", "incomplete"]
    entries: list[DecisionLedgerEntry]
    missing_task_ids: list[str]
    applied: Literal[False] = False
    created_final_records: Literal[0] = 0

    @model_validator(mode="after")
    def consistent_status(self) -> DecisionLedger:
        if [entry.response_index for entry in self.entries] != list(range(len(self.entries))):
            raise ValueError("Ledger entries must preserve response order")
        if len(self.missing_task_ids) != len(set(self.missing_task_ids)):
            raise ValueError("Missing task IDs must be unique")
        valid_ids = [entry.task_id for entry in self.entries if entry.status == "valid"]
        if len(valid_ids) != len(set(valid_ids)) or set(valid_ids) & set(self.missing_task_ids):
            raise ValueError("Valid results must be unique and cannot be marked missing")
        expected = "invalid" if any(entry.status == "invalid" for entry in self.entries) else (
            "incomplete" if self.missing_task_ids else "valid"
        )
        if self.status != expected:
            raise ValueError("Ledger status must reflect invalid and missing results")
        return self


def _response_envelope(value: object) -> dict:
    if (not isinstance(value, dict) or set(value) != {"bundle_id", "results"}
            or not isinstance(value["bundle_id"], str) or not value["bundle_id"].strip()
            or not isinstance(value["results"], list)):
        raise ValueError("Decision responses require an object with bundle_id and a results list")
    return value


def load_decision_responses(path: str | Path) -> dict:
    """Read strict JSON, rejecting duplicate keys and nonfinite numbers."""
    try:
        payload = json.loads(Path(path).read_bytes().decode("utf-8-sig"),
                             object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Decision responses must be valid UTF-8 JSON without duplicate keys or nonfinite numbers") from exc
    return _response_envelope(payload)


def _blocking_reasons(extraction: ExtractionResult, candidate: Candidate) -> list[DecisionBlocker]:
    """Expose unresolved mechanical checks, including acceptance prerequisites."""
    spec = extraction.task.record_types[candidate.record_type]
    reasons: list[DecisionBlocker] = []
    seen: set[tuple[str, str | None]] = set()

    def add(code: str, field: str | None = None) -> None:
        if (code, field) not in seen:
            seen.add((code, field))
            reasons.append(DecisionBlocker(code=code, candidate_id=candidate.candidate_id, field=field))

    required = {name for name, field in spec.fields.items() if field.required}
    required.update(spec.identity_fields)
    required.update(spec.condition_fields)
    required.update(name for name, field in spec.fields.items() if rule_matches(field.required_when, candidate.fields))
    for name in sorted(required):
        if name not in candidate.fields or is_missing(candidate.fields[name].value):
            add("required_missing", name)
    if not any(not is_missing(field.value) for field in candidate.fields.values()):
        add("no_located_observation")
    for name, field in candidate.fields.items():
        if not is_missing(field.value) and field.evidence.status != "matched":
            add("evidence_" + field.evidence.status, name)
    for name, status in candidate.field_status.items():
        if status == "required_missing":
            add("required_missing", name)
    if candidate.needs_review:
        add("candidate_needs_review")
    for check in candidate.checks:
        if not check.passed:
            add("check:" + check.code, check.field)
    for item in extraction.review:
        if item.candidate_id == candidate.candidate_id and item.severity == "review":
            add("review:" + item.code, item.field)
    for name in spec.measurement_groups:
        measurement = candidate.measurements.get(name)
        if measurement is None:
            add("measurement_missing", name)
        else:
            for issue in measurement.issues:
                add("measurement:" + issue, name)
    return reasons


def _validate_entry(index: int, raw: object, tasks: dict[str, PreparedDecisionTask],
                    counts: Counter, candidates: dict[str, Candidate],
                    extraction: ExtractionResult) -> DecisionLedgerEntry:
    task_id = raw.get("task_id") if isinstance(raw, dict) else None
    task = tasks.get(task_id) if isinstance(task_id, str) else None

    def invalid(code: str, message: str) -> DecisionLedgerEntry:
        # Never persist unrecognised IDs, extra fields or raw validation input.
        return DecisionLedgerEntry(response_index=index, task_id=task.task_id if task else None,
                                   status="invalid", code=code, message=message)

    if task is None:
        return invalid("unknown_task" if isinstance(task_id, str) else "invalid_result",
                       "The response must reference a task from this decision bundle.")
    if counts[task.task_id] > 1:
        return invalid("duplicate_result", "All responses for this repeated task are invalid; provide exactly one result.")
    if raw.get("action") not in ("select", "reject", "review"):
        return invalid("invalid_action", "The response action must be select, reject or review.")
    try:
        result = DecisionResult.model_validate(raw)
    except ValidationError:
        return invalid("invalid_result", "The response does not match the DecisionResult contract.")
    if result.action not in task.allowed_actions:
        return invalid("action_not_allowed", "This task does not permit the returned action.")
    if result.action == "select" and result.candidate_id not in task.candidate_ids:
        return invalid("invalid_candidate", "A selection must reference the candidate assigned to this task.")
    try:
        validate_decision(task, result)
    except ValueError:
        return invalid("invalid_decision", "The response violates the task decision contract.")
    blockers = [DecisionBlocker(code="coverage_not_verified")] if task.kind == "empty_chunk_review" else (
        _blocking_reasons(extraction, candidates[task.candidate_ids[0]])
    )
    return DecisionLedgerEntry(
        response_index=index, task_id=task.task_id, result=result, status="valid",
        message="Validated simulation/replay suggestion only; no extraction or review state was changed.",
        recommendation={"select": "suggest_keep", "reject": "suggest_reject", "review": "needs_review"}[result.action],
        blocking_reasons=blockers,
    )


def check_decisions(bundle: DecisionBundle, responses: dict) -> DecisionLedger:
    """Check offline suggestions; never construct final records or apply changes."""
    try:
        checked = DecisionBundle.model_validate(bundle.model_dump())
    except (AttributeError, ValueError, TypeError) as exc:
        raise ValueError("Invalid decision bundle; reload or regenerate it before checking responses") from exc
    payload = _response_envelope(responses)
    if payload["bundle_id"] != checked.bundle_id:
        raise ValueError("Decision responses reference a different bundle")
    tasks = {task.task_id: task for task in checked.tasks}
    candidates = {candidate.candidate_id: candidate for candidate in checked.extraction.candidates}
    counts = Counter(raw["task_id"] for raw in payload["results"]
                     if isinstance(raw, dict) and isinstance(raw.get("task_id"), str))
    entries = [_validate_entry(index, raw, tasks, counts, candidates, checked.extraction)
               for index, raw in enumerate(payload["results"])]
    received = {entry.task_id for entry in entries if entry.status == "valid"}
    missing = [task.task_id for task in checked.tasks if task.task_id not in received]
    status = "invalid" if any(entry.status == "invalid" for entry in entries) else "incomplete" if missing else "valid"
    return DecisionLedger(bundle_id=checked.bundle_id, status=status, entries=entries, missing_task_ids=missing)


def save_decision_ledger(ledger: DecisionLedger, path: str | Path) -> None:
    """Validate before exclusively creating a new artifact; never overwrite inputs."""
    checked = DecisionLedger.model_validate(ledger.model_dump())
    encoded = checked.model_dump_json(indent=2) + "\n"
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)


def decision_ledger_summary(ledger: DecisionLedger) -> dict:
    checked = DecisionLedger.model_validate(ledger.model_dump())
    valid = [entry for entry in checked.entries if entry.status == "valid"]
    return {
        "bundle_id": checked.bundle_id,
        "status": checked.status,
        "response_count": len(checked.entries),
        "valid_count": len(valid),
        "invalid_count": len(checked.entries) - len(valid),
        "missing_count": len(checked.missing_task_ids),
        "selected_count": sum(entry.result.action == "select" for entry in valid),
        "rejected_count": sum(entry.result.action == "reject" for entry in valid),
        "review_count": sum(entry.result.action == "review" for entry in valid),
        "blocked_selection_count": sum(entry.result.action == "select" and bool(entry.blocking_reasons) for entry in valid),
        "applied": False,
        "created_final_records": 0,
        "model_calls": 0,
    }
