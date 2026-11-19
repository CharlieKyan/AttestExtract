"""Validate supplemental model proposals against the exact packet that was sent."""

import re
from typing import Literal

from pydantic import Field

from .dataset import _POLICY_COLUMNS
from .dataset_supplement_packs import _NUMBER, _valid
from .json_validation import load_json
from .models import ContractModel


class _Quote(ContractModel):
    start: int = Field(strict=True, ge=0)
    end: int = Field(strict=True, gt=0)
    quote: str = Field(min_length=1)


class _Applicability(ContractModel):
    description: str = Field(min_length=1)
    source: _Quote


class _Decision(ContractModel):
    item_id: str
    axis: Literal["direct", "target", "fit", "missing"]
    status: Literal["supported", "unsupported", "unknown"]
    reason: str = Field(min_length=1)
    evidence: list[_Quote]
    endpoint_id: str | None = None
    predicate: Literal["direct", "same_as", "part_of", "sample_of", "member_of",
                       "instance_of", "located_in", "used_for"] | None = None
    field: str | None = None
    entity: str | None = None
    value: str | None = None
    unit: str | None = None
    conditions: str | None = None
    purpose: str | None = None
    statistic: str | None = None
    applicability: _Applicability | None = None


class Review(ContractModel):
    decisions: list[_Decision] = Field(min_length=1)


def _overlap(left, right):
    return left["start"] < right["end"] and right["start"] < left["end"]


def _quotes_contain(quotes, literal):
    if not literal:
        return False
    if _NUMBER.fullmatch(literal):
        return any(literal in _NUMBER.findall(q["quote"]) for q in quotes)
    return any(literal in q["quote"] for q in quotes)


def _check_endpoint(endpoint, quotes):
    if endpoint is None or not any(
        _overlap(q, m) and endpoint["label"] in q["quote"]
        for q in quotes for m in endpoint["mentions"]
    ):
        raise ValueError("endpoint_not_grounded_in_shown_mention")


def _existing(item, answer, quotes, conflicts, verified_direct):
    if answer.axis not in item["axes"]:
        raise ValueError("axis_outside_review_scope")
    if answer.status == "unknown":
        return
    local = [q for q in quotes if any(_overlap(q, s) for s in item["value_evidence"])]
    if not _quotes_contain(local, str(item["value"])):
        raise ValueError("value_not_grounded_in_this_observation")
    if answer.axis == "fit":
        if answer.endpoint_id or answer.predicate or answer.field != item["field"]:
            raise ValueError("unexpected_field_or_endpoint")
        if answer.status == "supported" and (item["blockers"] or "prior_decision" in item["prior"]["fit"]):
            raise ValueError("unresolved_original_blockers")
        return
    endpoint = item["endpoints"].get(answer.endpoint_id)
    _check_endpoint(endpoint, quotes)
    if answer.axis == "direct" and answer.predicate != "direct":
        raise ValueError("invalid_direct_predicate")
    if answer.axis == "target":
        prior = item["prior"]["direct"]
        direct_id = prior["id"] if prior["status"] == "supported" else verified_direct.get(answer.item_id)
        if direct_id is None:
            raise ValueError("direct_object_requires_resolution_first")
        _check_endpoint(item["endpoints"].get(direct_id), quotes)
        if answer.predicate is None or (answer.predicate == "direct" and endpoint["id"] != direct_id):
            raise ValueError("missing_or_inconsistent_relation")
        if (answer.status == "supported" and answer.predicate == "same_as"
                and frozenset((direct_id, endpoint["id"])) in conflicts):
            raise ValueError("conflicting_identity_evidence")


def _missing(pack, answer, quotes):
    if answer.axis != "missing":
        raise ValueError("invalid_gap_decision_axis")
    if answer.status != "supported":
        return
    if answer.field not in pack["task"]["fields"] or not answer.value or not answer.entity:
        raise ValueError("missing_fact_requires_declared_field_value_and_subject")
    if not any(_overlap(q, cue) and cue["quote"] == answer.value for q in quotes for cue in pack["uncovered"]):
        raise ValueError("proposed_value_not_at_uncovered_occurrence")
    for literal in (answer.entity, answer.value, answer.unit, answer.conditions, answer.statistic):
        if literal is not None and not _quotes_contain(quotes, literal):
            raise ValueError("proposed_fact_not_grounded_in_source")
    for rule, allowed in pack["task"].get("field_policies", {}).get(answer.field, {}).items():
        if getattr(answer, _POLICY_COLUMNS[rule]) not in allowed:
            raise ValueError("missing_fact_violates_field_policy")


def _scope(source, pack, answer, quotes, anchors, value):
    explicit = answer.applicability
    if explicit is None and answer.status != "supported":
        return [], None
    if explicit is not None:
        scope = explicit.source.model_dump()
        if (not _valid(source, scope) or not explicit.description.strip()
                or not any(w["start"] <= scope["start"] < scope["end"] <= w["end"] for w in pack["windows"])):
            raise ValueError("scope_not_in_shown_source")
        basis = "model_scoped"
    else:
        # An unambiguous local citation is sufficient on its own. Separated passages,
        # multi-row tables and multiple sentences require an explicit applicability claim.
        if len(quotes) != 1:
            raise ValueError("explicit_applicability_required")
        scope = quotes[0]
        content = scope["quote"].strip().rstrip(".!?。！？")
        if re.search(r"[\r\n。！？]|[.!?](?=\s)|<t[drh]\b", content, re.I):
            raise ValueError("explicit_applicability_required")
        basis = "single_cited_statement"
    intersections = [{"quote": source[max(scope["start"], a["start"], q["start"]):
                                      min(scope["end"], a["end"], q["end"])]}
                     for a in anchors for q in quotes]
    if not _quotes_contain(intersections, str(value)):
        raise ValueError("scope_outside_observation")
    return [{"start": scope["start"], "end": scope["end"]}], basis


def validate_response(source, pack, text, conflicts):
    answers = Review.model_validate(load_json(text)).decisions
    items = {item["item_id"]: item for item in pack.get("items", [])}
    expected = {(key, axis) for key, item in items.items() for axis in item["axes"]}
    seen = set()
    results = []
    verified_direct = {}
    for answer in sorted(answers, key=lambda item: item.axis != "direct"):
        if answer.status == "unknown" and any(x is not None for x in (
                answer.field, answer.entity, answer.value, answer.unit, answer.conditions, answer.statistic,
                answer.purpose, answer.endpoint_id, answer.predicate, answer.applicability)):
            raise ValueError("unknown_decision_must_not_assert_fact")
        if pack["kind"] == "existing_observation" and any(x is not None for x in (
                answer.entity, answer.value, answer.unit, answer.conditions, answer.purpose, answer.statistic)):
            raise ValueError("existing_observation_values_are_frozen")
        quotes = [quote.model_dump() for quote in answer.evidence]
        if answer.status != "unknown" and not quotes:
            raise ValueError("decision_requires_source_evidence")
        if any(not _valid(source, q) or not any(w["start"] <= q["start"] and q["end"] <= w["end"]
                                                for w in pack["windows"]) for q in quotes):
            raise ValueError("citation_not_in_shown_original_source")
        if pack["kind"] == "missing_candidate":
            if answer.item_id != pack["item_id"]:
                raise ValueError("unknown_gap_id")
            _missing(pack, answer, quotes)
            anchors = [cue for cue in pack["uncovered"] if cue["quote"] == answer.value]
            key = (answer.field, answer.value, answer.entity, answer.unit, answer.conditions,
                   tuple(sorted((q["start"], q["end"]) for q in quotes)))
        else:
            key = (answer.item_id, answer.axis)
            if key not in expected:
                raise ValueError("unknown_observation_or_axis")
            _existing(items[answer.item_id], answer, quotes, conflicts, verified_direct)
            anchors = items[answer.item_id]["value_evidence"]
            if answer.axis == "direct" and answer.status == "supported":
                verified_direct[answer.item_id] = answer.endpoint_id
        if key in seen:
            raise ValueError("duplicate_decision")
        seen.add(key)
        record = answer.model_dump()
        value = items[answer.item_id]["value"] if items else answer.value
        scope, basis = _scope(source, pack, answer, quotes, anchors, value)
        record.update(kind=pack["kind"], basis="semantic_model", scope=scope, scope_basis=basis)
        if items:
            endpoint = items[answer.item_id]["endpoints"].get(answer.endpoint_id)
            record["endpoint_label"] = endpoint["label"] if endpoint else None
        results.append(record)
    if expected and expected != seen:
        raise ValueError("incomplete_decisions_for_packet")
    return results
