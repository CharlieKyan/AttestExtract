"""Bounded search/read decisions with source-grounded, transactional proposals.

Display labels may compose information; quotations must be exact substrings of
passages actually supplied to the model. Structural checks do not establish the
semantic truth of a proposed identity, relation or target-field match.
"""

from __future__ import annotations

from copy import deepcopy
from collections import Counter
import json
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from .execution import digest
from .json_validation import JSONValidationError, load_json
from .models import ContractModel


class VerificationValidationError(ValueError):
    """A classified rejection; details are private, summary contains no source IDs."""

    def __init__(self, message, *, code, phase, category="model_response", **details):
        super().__init__(message)
        self.code = code
        self.details = {"category": category, "phase": phase, "code": code, **details}
        self.summary = {key: value for key, value in self.details.items()
                        if key in {"category", "phase", "code", "expected_count", "returned_count",
                                   "missing_count", "duplicate_count", "unexpected_count", "reference_count"}}


def _reject(message, *, code="invalid_decision", phase="decision", **details):
    return VerificationValidationError(message, code=code, phase=phase, **details)


def _state_error(message):
    return VerificationValidationError(message, code="verification_state_error",
                                       phase="internal_state", category="internal_state")


def target_observation_ids(batch):
    """One authoritative target list for request scope and decision validation."""
    ids = [item.get("observation_id") for item in batch]
    if any(not isinstance(key, str) or not key.strip() for key in ids) or len(set(ids)) != len(ids):
        raise _state_error("Batch observation identifiers must be nonempty and unique.")
    return ids


class Evidence(ContractModel):
    passage_id: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=1, max_length=6000)
    occurrence: int | None = Field(default=None, ge=0)


class ProposedObject(ContractModel):
    object_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=500)
    kind: Literal["type", "instance", "sample", "group", "model", "other"]
    scope: str = Field(max_length=2000)
    is_table_subject: bool
    mentions: list[Evidence] = Field(min_length=1, max_length=12)


class ProposedRelation(ContractModel):
    source_id: str = Field(min_length=1, max_length=100)
    target_id: str = Field(min_length=1, max_length=100)
    predicate: Literal["located_in", "part_of", "sample_of", "instance_of", "member_of", "same_as", "different_from", "other"]
    evidence: list[Evidence] = Field(min_length=1, max_length=12)


class Binding(ContractModel):
    observation_id: str = Field(min_length=1, max_length=100)
    subject_id: str | None
    status: Literal["supported", "unknown"]
    evidence: list[Evidence] = Field(max_length=12)
    reason: str = Field(min_length=1, max_length=4000)
    target_match: Literal["supported", "unsupported", "unknown"]
    property_name: str | None = Field(default=None, max_length=500)
    statistic: str | None = Field(default=None, max_length=500)


class Step(ContractModel):
    action: Literal["search", "read", "decide"]
    question: str = Field(max_length=4000)
    query: str | None = Field(default=None, max_length=256)
    search_offset: int = Field(default=0, ge=0)
    passage_ids: list[str] = Field(default_factory=list, max_length=3)
    neighbors: int = Field(default=0, ge=0, le=1)
    objects: list[ProposedObject] = Field(default_factory=list, max_length=48)
    relations: list[ProposedRelation] = Field(default_factory=list, max_length=96)
    bindings: list[Binding] = Field(default_factory=list, max_length=48)

    @model_validator(mode="after")
    def check_action(self):
        if self.action != "decide" and (self.objects or self.relations or self.bindings):
            raise _reject("Search/read steps cannot submit objects, relations or bindings.", phase="action")
        if self.action == "search":
            if self.query is None or not self.query.strip() or self.passage_ids or self.neighbors:
                raise _reject("Search requires a nonempty query and no read arguments.", phase="action")
        elif self.action == "read":
            if self.query is not None or self.search_offset or not self.passage_ids:
                raise _reject("Read requires passage IDs and no search arguments.", phase="action")
            if len(set(self.passage_ids)) != len(self.passage_ids) or any(not key.strip() for key in self.passage_ids):
                raise _reject("Read passage IDs must be nonempty and distinct.", phase="action")
        elif self.query is not None or self.search_offset or self.passage_ids or self.neighbors:
            raise _reject("Decide cannot also perform search or read operations.", phase="action")
        return self


def parse_step(text: str) -> Step:
    """Parse the exact response; duplicate keys and undeclared fields fail."""
    try:
        payload = load_json(text)
    except json.JSONDecodeError as exc:
        raise _reject("Verification response is not valid JSON.", code="invalid_analysis_json",
                      phase="json_syntax", line=exc.lineno, column=exc.colno) from None
    except JSONValidationError as exc:
        raise _reject("Verification response violates the JSON contract.", code="invalid_analysis_json",
                      phase="json_syntax", diagnostic=exc.diagnostic) from None
    except ValueError:
        # Python's integer-length guard is also a response decoding rejection.
        raise _reject("Verification response exceeds supported JSON decoding limits.",
                      code="invalid_analysis_json", phase="json_syntax") from None
    try:
        return Step.model_validate(payload)
    except ValidationError as exc:
        for item in exc.errors(include_input=False, include_url=False):
            cause = item.get("ctx", {}).get("error")
            if isinstance(cause, VerificationValidationError):
                raise cause from None
        # Do not include raw input values or exception context in diagnostics.
        errors = [{"path": list(item["loc"]), "code": item["type"]}
                  for item in exc.errors(include_input=False, include_context=False, include_url=False)]
        raise _reject("Verification response does not match the action contract.",
                      code="invalid_analysis_contract", phase="response_contract", errors=errors) from None


def _quoted_evidence(item: Evidence, source_text, source_index, visible, entries):
    from .analysis import _evidence

    try:
        passage = source_index.get(item.passage_id)
    except ValueError:
        raise _reject("Evidence references an unknown source passage.", code="invalid_evidence",
                      phase="evidence", passage_id=item.passage_id) from None
    start, end = passage["start"], passage["end"]
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source_text)
            or source_text[start:end] != passage["text"]):
        raise _state_error("Indexed passage does not match the original source.")
    positions, cursor = [], 0
    while (cursor := passage["text"].find(item.quote, cursor)) >= 0:
        positions.append(start + cursor)
        cursor += 1
    if not positions or len(positions) > 1 and item.occurrence is None:
        raise _reject("Evidence is absent or ambiguous within its passage; specify occurrence when repeated.",
                      code="invalid_evidence", phase="evidence", passage_id=item.passage_id)
    occurrence = 0 if item.occurrence is None else item.occurrence
    if occurrence >= len(positions):
        raise _reject("Evidence occurrence is outside its passage.", code="invalid_evidence", phase="evidence",
                      passage_id=item.passage_id)
    quote_start = positions[occurrence]
    quote_end = quote_start + len(item.quote)
    shown = False
    for fragment in visible:
        if fragment.get("passage_id") != item.passage_id:
            continue
        left, right = fragment.get("start"), fragment.get("end")
        if (type(left) is not int or type(right) is not int or not start <= left < right <= end
                or source_text[left:right] != fragment.get("text")):
            raise _state_error("Visible passage does not match its original indexed range.")
        if left <= quote_start < quote_end <= right:
            shown = True
    if not shown:
        raise _reject("Evidence was not contained in a source fragment supplied to the model.",
                      code="invalid_evidence", phase="evidence", passage_id=item.passage_id)
    return _evidence(source_text, {"quote": item.quote, "start": quote_start, "end": quote_end}, entries)


def apply_decision(step: Step, *, source_text, source_hash, batch, objects, relations,
                   entries, visible_passages, source_index, reference_ids=()):
    """Validate a whole decision before publishing any changes to caller state.

Return ``objects, relations, binding_updates, evidence_entries``. Original
observations stay immutable; field-match reservations are additional blockers.
"""
    if step.action != "decide":
        raise _state_error("Only a decide step can apply semantic proposals.")
    expected = set(target_observation_ids(batch))
    supplied = [item.observation_id for item in step.bindings]
    counts = Counter(supplied)
    missing = sorted(expected - set(supplied))
    duplicate = sorted(key for key, count in counts.items() if count > 1)
    unexpected = sorted(set(supplied) - expected)
    references = sorted(set(unexpected) & set(reference_ids))
    if missing or duplicate or unexpected:
        raise _reject(
            f"Decide must cover every batch observation exactly once, with no foreign IDs: "
            f"expected {len(expected)}, returned {len(supplied)}; missing {len(missing)}, "
            f"duplicate {len(duplicate)}, unexpected {len(unexpected)} ({len(references)} read-only references).",
            code="binding_scope_mismatch", phase="binding_scope", expected_count=len(expected),
            returned_count=len(supplied), missing_count=len(missing), duplicate_count=len(duplicate),
            unexpected_count=len(unexpected), reference_count=len(references), missing_ids=missing,
            duplicate_ids=duplicate, unexpected_ids=unexpected, reference_ids=references,
            foreign_ids=sorted(set(unexpected) - set(reference_ids)))
    new_objects, new_relations, new_entries = deepcopy(objects), deepcopy(relations), deepcopy(entries)
    known = {item["object_id"] for item in objects}
    prior_objects = {item["object_id"]: item for item in objects}
    if len(known) != len(objects):
        raise _state_error("Existing object identifiers are not unique.")
    local = {}
    declared = set()

    def evidence(items):
        return sorted({_quoted_evidence(item, source_text, source_index, visible_passages, new_entries)
                       for item in items})

    for item in step.objects:
        if not item.name.strip() or not item.object_id.strip():
            raise _reject("Object names and local identifiers must not be blank.")
        if item.object_id in local or item.object_id in known:
            raise _reject("New objects cannot replace existing IDs or repeat a local ID.")
        mentions = evidence(item.mentions)
        key = "object-" + digest({"source": source_hash, "name": item.name, "kind": item.kind,
                                  "scope": item.scope, "mentions": mentions})[:24]
        if key in declared or key in local:
            raise _reject("Decision repeats the same object under multiple local IDs.")
        declared.add(key)
        local[item.object_id] = key
        value = {"object_id": key, "name": item.name, "kind": item.kind,
                 "scope": item.scope, "is_table_subject": item.is_table_subject,
                 "mention_evidence_ids": mentions, "semantic_status": "model_proposed"}
        if key in prior_objects:
            # A bounded context may omit a previously registered object. Exact
            # re-declaration is idempotent; matching names alone never merge.
            if any(prior_objects[key].get(field) != expected for field, expected in value.items()):
                raise _reject("Object re-declaration disagrees with the existing identity or table membership.")
            continue
        known.add(key)
        new_objects.append(value)

    def endpoint(key):
        resolved = local.get(key, key)
        if resolved not in known:
            raise _reject("A proposal references an undeclared object.")
        return resolved

    relation_keys = {item["relation_id"] for item in relations}
    declared_relations = set()
    for item in step.relations:
        source, target = endpoint(item.source_id), endpoint(item.target_id)
        if source == target:
            raise _reject("A relation must connect distinct objects.")
        value = {"source_id": source, "target_id": target, "predicate": item.predicate,
                 "evidence_ids": evidence(item.evidence)}
        key = "relation-" + digest(value)[:24]
        if key in declared_relations:
            raise _reject("A relation is repeated in the decision.")
        declared_relations.add(key)
        if key in relation_keys:
            continue
        relation_keys.add(key)
        new_relations.append({"relation_id": key, **value, "semantic_status": "model_proposed"})

    by_id = {item["observation_id"]: item for item in batch}
    updates = {}
    for item in step.bindings:
        if not item.reason.strip():
            raise _reject("A binding must explain its proposed attribution or uncertainty.", phase="binding")
        if item.status == "unknown" and item.subject_id is not None:
            raise _reject("Unknown attribution must keep subject_id null.", code="invalid_binding_status",
                          phase="binding", observation_id=item.observation_id)
        if item.status == "supported" and (item.subject_id is None or not item.evidence):
            raise _reject("Supported attribution requires a declared subject and source evidence.",
                          code="invalid_binding_status", phase="binding", observation_id=item.observation_id)
        subject = None if item.subject_id is None else endpoint(item.subject_id)
        blockers = deepcopy(by_id[item.observation_id].get("blockers", []))
        if item.target_match != "supported":
            code = "target_field_" + item.target_match
            if not any(isinstance(old, dict) and old.get("code") == code for old in blockers):
                blockers.append({"code": code, "message": "Model did not support mapping this observation to the requested field definition."})
        updates[item.observation_id] = {
            "subject_id": subject, "binding_status": item.status,
            "binding_evidence_ids": evidence(item.evidence), "reason": item.reason,
            "target_match": item.target_match, "property_name": item.property_name,
            "statistic": item.statistic, "blockers": blockers,
        }
    return new_objects, new_relations, updates, new_entries
