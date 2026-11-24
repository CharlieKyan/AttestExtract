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
                                   "missing_count", "duplicate_count", "unexpected_count", "reference_count", "ambiguity_count"}}


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
    object_id: str = Field(min_length=1, max_length=100,
        description="New local identifier, for example o1. Bindings may reference it in this same response; the framework creates the stable ID.")
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
    subject_id: str | None = Field(description="Direct owner: an existing object ID or an object_id declared in this response. Required and non-null for supported ownership; null for unknown ownership.")
    status: Literal["supported", "unknown"] = Field(description="Ownership judgment only. supported requires a declared non-null subject_id and nonempty source evidence. unknown requires subject_id=null; it does not mean the observed value is absent.")
    evidence: list[Evidence] = Field(max_length=12,
        description="Exact source support for this ownership judgment. Must be nonempty when status=supported; unknown may retain available evidence.")
    reason: str = Field(min_length=1, max_length=4000)
    target_match: Literal["supported", "unsupported", "unknown"] = Field(description="Independent judgment of whether the observation fits the requested field definition. May be supported even when ownership status is unknown.")
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


def _response_property_names() -> set[str]:
    """Known protocol names only; never derive diagnostic visibility from a reply."""
    schema = Step.model_json_schema()
    names = set(schema.get("properties", {}))
    for definition in schema.get("$defs", {}).values():
        names.update(definition.get("properties", {}))
    return names


def parse_step(text: str) -> Step:
    """Parse the exact response; duplicate keys and undeclared fields fail."""
    known_keys = _response_property_names()
    try:
        payload = load_json(text, allowed_keys=known_keys)
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
        errors = [{"path": [part if isinstance(part, int) or part in known_keys else "<undeclared>"
                            for part in item["loc"]], "code": item["type"]}
                  for item in exc.errors(include_input=False, include_context=False, include_url=False)]
        raise _reject("Verification response does not match the action contract.",
                      code="invalid_analysis_contract", phase="response_contract", errors=errors) from None


def _context(source_text, passage, start, end, visible):
    """Display-only original context; never part of evidence or object identity."""
    left, right = max(passage["start"], start - 160), min(passage["end"], end + 160)
    return {"start": left, "end": right, "text": source_text[left:right],
            "model_visible": any(item["start"] <= left and right <= item["end"] for item in visible)}


def _quoted_evidence(item: Evidence, source_text, source_index, visible, entries,
                     *, path, ambiguities, contexts):
    from .analysis import _evidence

    try:
        passage = source_index.get(item.passage_id)
    except ValueError:
        raise _reject("Evidence references an unknown source passage.", code="unknown_passage",
                      phase="evidence", path=path, passage_id=item.passage_id) from None
    start, end = passage["start"], passage["end"]
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source_text)
            or source_text[start:end] != passage["text"]):
        raise _state_error("Indexed passage does not match the original source.")
    fragments = []
    for fragment in visible:
        if fragment.get("passage_id") != item.passage_id:
            continue
        left, right = fragment.get("start"), fragment.get("end")
        if (type(left) is not int or type(right) is not int or not start <= left < right <= end
                or source_text[left:right] != fragment.get("text")):
            raise _state_error("Visible passage does not match its original indexed range.")
        fragments.append(fragment)
    positions, cursor = [], 0
    while (cursor := passage["text"].find(item.quote, cursor)) >= 0:
        positions.append(start + cursor)
        cursor += 1
    matches = [{"start": left, "end": left + len(item.quote), "occurrence": number}
               for number, left in enumerate(positions)
               if any(part["start"] <= left and left + len(item.quote) <= part["end"] for part in fragments)]
    details = {"path": path, "passage_id": item.passage_id,
               "total_match_count": len(positions), "visible_match_count": len(matches)}
    if not positions:
        raise _reject("Evidence quote does not occur in its source passage.", code="quote_not_found",
                      phase="evidence", **details)
    if item.occurrence is not None and item.occurrence >= len(positions):
        raise _reject("Evidence occurrence is outside its passage.", code="occurrence_out_of_range",
                      phase="evidence", **details)
    if item.occurrence is not None:
        matches = [match for match in matches if match["occurrence"] == item.occurrence]
    if not matches:
        raise _reject("Evidence was not contained in a source fragment supplied to the model.",
                      code="not_visible", phase="evidence", **details)
    if len(matches) > 1:
        ambiguity = {**details, "quote": item.quote, "matches": [
            {**match, "context": _context(source_text, passage, match["start"], match["end"], fragments)}
            for match in matches]}
        ambiguities.append(ambiguity)
        # This token exists only in the discarded validation workspace. It
        # denotes alternatives, never a selected occurrence or stored evidence.
        return "unresolved-" + digest({"passage": item.passage_id, "quote": item.quote,
                                       "matches": matches})[:24]
    match = matches[0]
    key = _evidence(source_text, {"quote": item.quote, "start": match["start"], "end": match["end"]}, entries)
    contexts[key] = {"evidence_id": key, "passage_id": item.passage_id,
                     **_context(source_text, passage, match["start"], match["end"], fragments)}
    return key


def _check_visible_source(source_text, source_index, visible_passages):
    """Corruption is global even when a rejected proposal never cites that range."""
    for fragment in visible_passages:
        try:
            passage = source_index.get(fragment["passage_id"])
            start, end = passage["start"], passage["end"]
            left, right = fragment["start"], fragment["end"]
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(source_text)
                    or source_text[start:end] != passage["text"]):
                raise _state_error("Indexed passage does not match the original source.")
            if (type(left) is not int or type(right) is not int
                    or not start <= left < right <= end
                    or source_text[left:right] != fragment["text"]):
                raise _state_error("Visible passage does not match its original indexed range.")
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, VerificationValidationError):
                raise
            raise _state_error("Visible passage cannot be checked against the source index.") from None


def apply_decision(step: Step, *, source_text, source_hash, batch, objects, relations,
                   entries, visible_passages, source_index, reference_ids=(), evidence_contexts=None,
                   review_items=None):
    """Assess a complete decision before publishing changes to caller state.

    A supplied review_items list enables proposal isolation. Only explicit object
    references propagate rejection; semantic dependence on an unreferenced relation
    cannot be inferred by this validator. The default retains strict rejection.
    Return objects, relations, binding_updates, evidence_entries. Raw observations
    stay immutable. Even partial mode never commits through a global state/scope error.
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
    _check_visible_source(source_text, source_index, visible_passages)
    new_objects, new_relations, new_entries = deepcopy(objects), deepcopy(relations), deepcopy(entries)
    known = {item["object_id"] for item in objects}
    prior_objects = {item["object_id"]: item for item in objects}
    if len(known) != len(objects):
        raise _state_error("Existing object identifiers are not unique.")
    identifiers = [item.object_id for item in step.objects]
    if any(not key.strip() for key in identifiers):
        raise _reject("Object local identifiers must not be blank.")
    if len(set(identifiers)) != len(identifiers) or set(identifiers) & known:
        raise _reject("New objects cannot replace existing IDs or repeat a local ID.")
    local, rejected_objects = {}, set()
    declared, declared_relations = set(), set()
    ambiguities, contexts, reviews = [], {}, []
    partial = review_items is not None

    def problem(exc, issues, path):
        if exc.details["category"] == "internal_state" or not partial:
            raise exc
        issues.append({**exc.details, "path": exc.details.get("path", path), "message": str(exc)})

    def evidence(items, path, issues):
        # Stage evidence separately: a rejected proposal must not enlarge the
        # accepted registry or its display contexts, even when some quotes pass.
        candidate_entries = deepcopy(new_entries)
        candidate_contexts, keys = {}, set()
        for number, item in enumerate(items):
            quote_path = f"{path}[{number}]"
            before = len(ambiguities)
            try:
                key = _quoted_evidence(item, source_text, source_index, visible_passages, candidate_entries,
                    path=quote_path, ambiguities=ambiguities, contexts=candidate_contexts)
                if partial and len(ambiguities) > before:
                    issues.append({"category": "model_response", "phase": "evidence", "code": "quote_ambiguous",
                                   "message": "Evidence has multiple visible source positions.", **ambiguities[-1]})
                else:
                    keys.add(key)
            except VerificationValidationError as exc:
                problem(exc, issues, quote_path)
        return sorted(keys), candidate_entries, candidate_contexts

    def retain_evidence(staged):
        new_entries.update(staged[1])
        contexts.update(staged[2])

    def review(kind, path, identifier, item, issues, dependencies=()):
        value = {"proposal_type": kind, "path": path, "proposal_id": identifier,
                 "observation_ids": [identifier] if kind == "binding" else [],
                 "dependencies": list(dict.fromkeys(dependencies)), "code": issues[0]["code"],
                 "issues": issues, "raw_proposal": item.model_dump(mode="json")}
        reviews.append(value)

    for number, item in enumerate(step.objects):
        path, issues = f"objects[{number}]", []
        if not item.name.strip() or not item.object_id.strip():
            problem(_reject("Object names and local identifiers must not be blank."), issues, path)
        staged = evidence(item.mentions, path + ".mentions", issues)
        if issues:
            rejected_objects.add(item.object_id)
            review("object", path, item.object_id, item, issues)
            continue
        mentions = staged[0]
        key = "object-" + digest({"source": source_hash, "name": item.name, "kind": item.kind,
                                  "scope": item.scope, "mentions": mentions})[:24]
        if key in declared or key in local or key in set(identifiers):
            raise _reject("Decision repeats the same object under multiple local IDs.")
        declared.add(key)
        value = {"object_id": key, "name": item.name, "kind": item.kind,
                 "scope": item.scope, "is_table_subject": item.is_table_subject,
                 "mention_evidence_ids": mentions, "semantic_status": "model_proposed"}
        if key in prior_objects and any(prior_objects[key].get(field) != expected for field, expected in value.items()):
            problem(_reject("Object re-declaration disagrees with the existing identity or table membership."), issues, path)
            rejected_objects.add(item.object_id)
            review("object", path, item.object_id, item, issues)
            continue
        local[item.object_id] = key
        if key not in prior_objects:
            known.add(key)
            new_objects.append(value)
        retain_evidence(staged)

    def endpoint(key, issues, path, dependencies):
        if key in rejected_objects:
            dependencies.append(key)
            problem(_reject("The referenced object proposal was not applied.", code="dependency_rejected",
                            phase="object_reference", object_id=key), issues, path)
            return None
        resolved = local.get(key, key)
        if resolved not in known:
            problem(_reject("A proposal references an undeclared object.",
                            code="invalid_object_reference" if partial else "invalid_decision",
                            phase="object_reference", object_id=key), issues, path)
            return None
        return resolved

    relation_keys = {item["relation_id"] for item in relations}
    for number, item in enumerate(step.relations):
        path, issues, dependencies = f"relations[{number}]", [], []
        source = endpoint(item.source_id, issues, path + ".source_id", dependencies)
        target = endpoint(item.target_id, issues, path + ".target_id", dependencies)
        if source is not None and source == target:
            problem(_reject("A relation must connect distinct objects."), issues, path)
        staged = evidence(item.evidence, path + ".evidence", issues)
        if issues:
            review("relation", path, path, item, issues, dependencies)
            continue
        value = {"source_id": source, "target_id": target, "predicate": item.predicate,
                 "evidence_ids": staged[0]}
        key = "relation-" + digest(value)[:24]
        if key in declared_relations:
            problem(_reject("A relation is repeated in the decision."), issues, path)
            review("relation", path, path, item, issues)
            continue
        declared_relations.add(key)
        if key not in relation_keys:
            relation_keys.add(key)
            new_relations.append({"relation_id": key, **value, "semantic_status": "model_proposed"})
        retain_evidence(staged)

    by_id = {item["observation_id"]: item for item in batch}
    updates = {}
    for number, item in enumerate(step.bindings):
        path, issues, dependencies = f"bindings[{number}]", [], []
        if not item.reason.strip():
            problem(_reject("A binding must explain its proposed attribution or uncertainty.", phase="binding"), issues, path)
        if item.status == "unknown" and item.subject_id is not None:
            problem(_reject("Unknown attribution must keep subject_id null.", code="invalid_binding_status",
                            phase="binding", observation_id=item.observation_id), issues, path)
        if item.status == "supported" and (item.subject_id is None or not item.evidence):
            problem(_reject("Supported attribution requires a declared subject and source evidence.",
                            code="invalid_binding_status", phase="binding", observation_id=item.observation_id), issues, path)
        subject = None if item.subject_id is None else endpoint(item.subject_id, issues, path + ".subject_id", dependencies)
        staged = evidence(item.evidence, path + ".evidence", issues)
        blockers = deepcopy(by_id[item.observation_id].get("blockers", []))
        if issues:
            review("binding", path, item.observation_id, item, issues, dependencies)
            blockers.append({"code": "proposal_rejected", "message": "This ownership proposal was not applied; inspect the original response and validation issues."})
            updates[item.observation_id] = {
                "subject_id": None, "binding_status": "review_required", "binding_evidence_ids": [],
                "reason": "Framework validation rejected this proposal; this is not a model judgment of unknown ownership.",
                "target_match": "unknown", "property_name": None, "statistic": None, "blockers": blockers}
            continue
        if item.target_match != "supported":
            code = "target_field_" + item.target_match
            if not any(isinstance(old, dict) and old.get("code") == code for old in blockers):
                blockers.append({"code": code, "message": "Model did not support mapping this observation to the requested field definition."})
        updates[item.observation_id] = {
            "subject_id": subject, "binding_status": item.status,
            "binding_evidence_ids": staged[0], "reason": item.reason,
            "target_match": item.target_match, "property_name": item.property_name,
            "statistic": item.statistic, "blockers": blockers}
        retain_evidence(staged)
    if ambiguities and not partial:
        raise _reject("Evidence has multiple visible source positions; the whole decision requires review.",
                      code="quote_ambiguous", phase="evidence", ambiguity_count=len(ambiguities), ambiguities=ambiguities)
    # Objects affect only explicitly referencing bindings. A relation cannot be
    # cited by a binding in this protocol, so its rejection does not establish
    # additional dependencies by proximity or by sharing an otherwise valid owner.
    for item in reviews:
        if item["proposal_type"] == "object":
            item["observation_ids"] = [binding.observation_id for binding in step.bindings
                if binding.subject_id == item["proposal_id"] and updates[binding.observation_id]["binding_status"] == "review_required"]
    if evidence_contexts is not None:
        evidence_contexts.update(contexts)
    if review_items is not None:
        review_items.extend(reviews)
    return new_objects, new_relations, updates, new_entries
