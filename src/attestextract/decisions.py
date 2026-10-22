"""Contracts for future bounded decisions. No Jev adapter is implemented."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field, model_validator

from .models import ContractModel


class DecisionTask(ContractModel):
    task_id: str
    candidate_ids: list[str]
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_candidates(self) -> DecisionTask:
        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("Candidate IDs must be unique within a decision task")
        return self


class DecisionResult(ContractModel):
    task_id: str
    action: Literal["select", "reject", "review"]
    candidate_id: str | None = None


class DecisionBackend(Protocol):
    def decide(self, task: DecisionTask) -> DecisionResult: ...


def validate_decision(task: DecisionTask, result: DecisionResult) -> DecisionResult:
    """Validate an adapter response before any caller applies a decision."""
    if result.task_id != task.task_id:
        raise ValueError("Decision task ID does not match")
    if result.action == "select":
        if result.candidate_id not in task.candidate_ids:
            raise ValueError("A decision can only select an existing candidate")
    elif result.candidate_id is not None:
        raise ValueError("Only a select decision may specify a candidate ID")
    return result
