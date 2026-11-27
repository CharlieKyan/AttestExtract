"""Bounded search/read decisions with source-grounded, transactional proposals.

Display labels may compose information; quotations must be exact substrings of
passages actually supplied to the model. Structural checks do not establish the
semantic truth of a proposed identity, relation or target-field match.
"""

from __future__ import annotations

from copy import deepcopy
from collections import Counter
import json
import re
from typing import Literal

from pydantic import Field, PrivateAttr, ValidationError, model_validator

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


class SourceCitation(ContractModel):
    evidence_id: str = Field(min_length=1, max_length=100)


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


class NameResolution(ContractModel):
    """One mention in this observation, interpreted against a declared object."""
    relation: Literal["local_identity", "reusable_alias", "related", "different", "uncertain"]
    mention: Evidence
    target_id: str = Field(min_length=1, max_length=100)
    target_mention: Evidence
    evidence: list[Evidence] = Field(min_length=1, max_length=8)
    scope_evidence: list[Evidence] = Field(max_length=6)
    competitor_ids: list[str] = Field(max_length=12)
    reason: str = Field(min_length=1, max_length=2000)


class ObservationBinding(ContractModel):
    observation_id: str = Field(min_length=1, max_length=100)
    subject_id: str | None = Field(description="Direct owner: an existing object ID or an object_id declared in this response. Required and non-null for supported ownership; null for unknown ownership.")
    status: Literal["supported", "unknown"] = Field(description="Ownership judgment only. supported requires a declared non-null subject_id and nonempty source evidence. unknown requires subject_id=null; it does not mean the observed value is absent.")
    evidence: list[Evidence] = Field(max_length=12,
        description="Exact source support for this ownership judgment. Must be nonempty when status=supported; unknown may retain available evidence.")
    reason: str = Field(min_length=1, max_length=4000)
    target_match: Literal["supported", "unsupported", "unknown"] = Field(description="Independent judgment of whether the observation fits the requested field definition. May be supported even when ownership status is unknown.")
    property_name: str | None = Field(default=None, max_length=500)
    statistic: str | None = Field(default=None, max_length=500)


class Binding(ObservationBinding):
    name_resolution: NameResolution | None = None


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


class UnifiedRelation(ContractModel):
    source_kind: Literal["object", "mention"] = Field(default="object",
        description="A mention uses the observation_id of this occurrence. An object uses a declared object_id.")
    source_id: str = Field(min_length=1, max_length=100)
    target_id: str = Field(min_length=1, max_length=100)
    predicate: Literal["local_identity", "reusable_alias", "related", "uncertain", "different_from",
                       "located_in", "part_of", "sample_of", "instance_of", "member_of", "same_as", "other"]
    evidence: list[Evidence] = Field(min_length=1, max_length=12)
    mention: Evidence | None = None
    target_mention: Evidence | None = None
    scope_evidence: list[Evidence] = Field(default_factory=list, max_length=6)
    competitor_ids: list[str] = Field(default_factory=list, max_length=12)
    reason: str | None = Field(default=None, min_length=1, max_length=2000)


class ObjectWithCitations(ProposedObject):
    mentions: list[Evidence | SourceCitation] = Field(min_length=1, max_length=12)


class BindingWithCitations(ObservationBinding):
    evidence: list[Evidence | SourceCitation] = Field(max_length=12)


class RelationWithCitations(UnifiedRelation):
    evidence: list[Evidence | SourceCitation] = Field(min_length=1, max_length=12)
    mention: Evidence | SourceCitation | None = None
    target_mention: Evidence | SourceCitation | None = None
    scope_evidence: list[Evidence | SourceCitation] = Field(default_factory=list, max_length=6)


class UnifiedStep(Step):
    """Model-facing protocol. Rejected optional rows retain their original wire position."""

    objects: list[ObjectWithCitations] = Field(default_factory=list, max_length=48)
    relations: list[RelationWithCitations] = Field(default_factory=list, max_length=144)
    bindings: list[BindingWithCitations] = Field(default_factory=list, max_length=48)
    _relation_inputs: list | None = PrivateAttr(default=None)
    _relation_positions: list[int] = PrivateAttr(default_factory=list)
    _invalid_rows: dict[int, VerificationValidationError] = PrivateAttr(default_factory=dict)


class FilingLink(ContractModel):
    """Model explanation for filing one particular reading under a declared row object."""

    source_kind: Literal["observation"]
    source_id: str = Field(min_length=1, max_length=100, description="The observation_id being filed.")
    target_id: str = Field(min_length=1, max_length=100, description="The object_id of the requested row subject.")
    explanation: str = Field(min_length=1, max_length=2000)
    field_applicability: Literal["included", "excluded", "uncertain"]
    target_basis: Literal["reported", "inferred", "unknown"]
    evidence: list[Evidence | SourceCitation] = Field(min_length=1, max_length=12)
    target_contains_direct: bool | None = None


class RepeatedReading(ContractModel):
    source_kind: Literal["repeat"]
    source_id: str = Field(min_length=1, max_length=100, description="A repeated observation in this decision.")
    target_id: str = Field(min_length=1, max_length=100, description="The same observation in the batch or the displayed references.")
    explanation: str = Field(min_length=1, max_length=2000)
    evidence: list[Evidence | SourceCitation] = Field(min_length=2, max_length=12)


class FilingStep(UnifiedStep):
    relations: list[FilingLink | RepeatedReading | RelationWithCitations] = Field(default_factory=list, max_length=144)


def _response_property_names(step_type=Step) -> set[str]:
    """Known protocol names only; never derive diagnostic visibility from a reply."""
    schema = step_type.model_json_schema()
    names = set(schema.get("properties", {}))
    for definition in schema.get("$defs", {}).values():
        names.update(definition.get("properties", {}))
    return names


def unified_response_property_names() -> set[str]:
    return _response_property_names(UnifiedStep)


def _decode_step(text, known_keys):
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
    return payload


def _contract_error(exc, known_keys):
    for item in exc.errors(include_input=False, include_url=False):
        cause = item.get("ctx", {}).get("error")
        if isinstance(cause, VerificationValidationError):
            return cause
    errors = [{"path": [part if isinstance(part, int) or part in known_keys else "<undeclared>"
                        for part in item["loc"]], "code": item["type"]}
              for item in exc.errors(include_input=False, include_context=False, include_url=False)]
    return _reject("Verification response does not match the action contract.",
                   code="invalid_analysis_contract", phase="response_contract", errors=errors)


def parse_step(text: str) -> Step:
    """Legacy strict response reader."""
    known_keys = _response_property_names()
    payload = _decode_step(text, known_keys)
    try:
        return Step.model_validate(payload)
    except ValidationError as exc:
        raise _contract_error(exc, known_keys) from None


def parse_unified_step(text: str, *, statement_protocol=None) -> UnifiedStep:
    contract = FilingStep if statement_protocol == "source-v2" else UnifiedStep
    known_keys = _response_property_names(contract)
    payload = _decode_step(text, known_keys)
    rows = payload.get("relations") if isinstance(payload, dict) and payload.get("action") == "decide" else None
    try:
        if not isinstance(rows, list) or len(rows) > 144:
            return contract.model_validate(payload)
        # Validate required bindings and the entire envelope before isolating
        # individual optional relations. Never salvage partial JSON or a tool action.
        step = contract.model_validate({**payload, "relations": []})
    except ValidationError as exc:
        raise _contract_error(exc, known_keys) from None
    step._relation_inputs = rows
    for number, row in enumerate(rows):
        try:
            types = {"observation": FilingLink, "repeat": RepeatedReading} if contract is FilingStep else {}
            source_kind = row.get("source_kind") if isinstance(row, dict) else None
            relation_type = types.get(source_kind, RelationWithCitations) if isinstance(source_kind, str) else RelationWithCitations
            relation = relation_type.model_validate(row)
        except ValidationError as exc:
            step._invalid_rows[number] = _contract_error(exc, known_keys)
        else:
            step._relation_positions.append(number)
            step.relations.append(relation)
    return step


def _context(source_text, passage, start, end, visible):
    """Display-only original context; never part of evidence or object identity."""
    left, right = max(passage["start"], start - 160), min(passage["end"], end + 160)
    return {"start": left, "end": right, "text": source_text[left:right],
            "model_visible": any(item["start"] <= left and right <= item["end"] for item in visible)}


def source_citation_owners(batch, entries):
    """Only uniquely located first-pass evidence qualifies; evidence_ids can include alternatives."""
    owners = {}
    for obs in batch:
        positions = set()
        for source in obs.get("source_evidence", []):
            if source.get("location_status") not in {"located", "matched", "normalized"} or not source.get("span"):
                continue
            selected = source["span"]
            alternatives = [*source.get("matches", []), *(m["span"] for m in source.get("source_matches", []))]
            if all(span == selected for span in alternatives):
                positions.add((selected["start"], selected["end"]))
        if not positions:
            continue
        for key in obs.get("evidence_ids", []):
            span = entries[key]
            if (span["start"], span["end"]) in positions:
                owners.setdefault(key, []).append(obs["observation_id"])
    return owners


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


def _unified_relation_plan(step, expected):
    """Classify original positions without promoting weak links or choosing between competing decisions."""
    raw = step._relation_inputs
    if raw is None:
        raw = [row.model_dump(mode="json", exclude_unset=True) for row in step.relations]
    positions = step._relation_positions if step._relation_inputs is not None else range(len(raw))
    problems = dict(step._invalid_rows)
    mention_counts = Counter(row["source_id"] for row in raw
        if isinstance(row, dict) and row.get("source_kind") == "mention" and isinstance(row.get("source_id"), str))
    graph, names = [], {}
    name_predicates = {"local_identity", "reusable_alias", "related", "different_from", "uncertain"}
    for number, row in zip(positions, step.relations, strict=True):
        if isinstance(row, (FilingLink, RepeatedReading)):
            if row.source_id in expected and row.explanation.strip():
                graph.append((number, row))
                continue
            issue = "Filing must explain the association for a target observation in this decision."
        elif row.source_kind == "mention":
            if row.source_id not in expected:
                issue = "The mention must identify a target observation in this decision."
            elif mention_counts[row.source_id] > 1:
                issue = "Multiple mention decisions for one observation require review; no alternative was selected."
            elif row.predicate not in name_predicates:
                issue = "This predicate does not describe the interpretation of a single mention."
            elif row.mention is None or row.target_mention is None or len(row.evidence) > 8:
                issue = "A mention link requires both source and target quotations and up to eight evidence citations."
            else:
                names[row.source_id] = (number, row)
                continue
        elif row.predicate in {"related", "uncertain", "other"}:
            problems[number] = _reject("The relationship is unspecified; keep this pair for review.",
                                       code="relation_unspecified", phase="relation")
            continue
        elif row.predicate in {"local_identity", "reusable_alias"}:
            issue = "Local identity or an alias requires an anchored mention."
        elif row.scope_evidence or row.competitor_ids:
            issue = "An object link with mention-specific context needs review before document-wide reuse."
        else:
            graph.append((number, row))
            continue
        problems[number] = _reject(issue, code="invalid_relation_context", phase="relation")
    return raw, graph, names, problems


def _label_positions(spans, name):
    """Literal occurrences within accepted evidence; keep short Latin IDs separate."""
    from .dataset_identity import literal_name

    if not name:
        return set()
    pattern = re.compile(literal_name(name))
    return {(span["start"] + match.start(), span["start"] + match.end())
            for span in spans for match in pattern.finditer(span["quote"])}


def _endpoint_positions(obj, objects, distinguishing, proof, explicit, registry, local_positions):
    """An endpoint is a name occurrence in the proof, or an independently registered, explicitly cited mention."""
    # Explicit markers can narrow the proof to the relevant occurrence.
    search = proof if explicit is None else [explicit]
    if explicit is not None and not any(
            item["start"] <= explicit["start"] and explicit["end"] <= item["end"] for item in proof):
        return set()
    positions = _label_positions(search, obj["name"]) if distinguishing else set()
    registered = [registry[key] for key in obj["mention_evidence_ids"] if key in registry]
    if not distinguishing:
        positions = {position for position in _label_positions(search, obj["name"]) if any(
            item["start"] <= position[0] and position[1] <= item["end"] for item in registered)}
    if explicit is not None and explicit["evidence_id"] in obj["mention_evidence_ids"]:
        positions.add((explicit["start"], explicit["end"]))
    elif explicit is not None and any(char.isalpha() for char in explicit["quote"]):
        # A registered introductory sentence may surround the explicit original
        # handle S1. Check the exact occurrence and its link to the display label;
        # a number or an arbitrary word somewhere in that paragraph is insufficient.
        marker_name = explicit["quote"]
        def fits(candidate):
            return (_label_positions([{"quote": candidate["name"], "start": 0}], marker_name)
                    and any(registry[key]["start"] <= explicit["start"] and explicit["end"] <= registry[key]["end"]
                            for key in candidate["mention_evidence_ids"] if key in registry))
        if fits(obj) and not any(fits(other) for other in objects if other["object_id"] != obj["object_id"]):
            positions.add((explicit["start"], explicit["end"]))
    positions.update((start, end) for start, end in local_positions
                     if any(item["start"] <= start and end <= item["end"] for item in search))
    return positions


def _bound_membership_mentions(source, target, proof, batch, updates, objects, registry, visible, parent_marker):
    """Locate a model-proposed membership in a clause whose parent is separately bound.

    A composite subject or a reference back to the parent can leave only one
    independently readable name in that clause. Require the accepted ownership
    of the clause and a distinct registered parent mention in the same displayed
    passage. Do not extrapolate from names occurring elsewhere in the document.
    """
    from .dataset_identity import literal_name

    names = {}
    target_citations = []
    for observation in batch:
        accepted = updates[observation["observation_id"]]
        owner, name = accepted["subject_id"], observation.get("entity")
        if accepted["binding_status"] != "supported" or not name:
            continue
        if re.search(literal_name(name), objects[owner]["name"]):
            names.setdefault(owner, set()).add(name)
            if owner == target:
                target_citations.extend(registry[key] for key in accepted["binding_evidence_ids"])
    parent_mentions = [registry[key] for key in objects[target]["mention_evidence_ids"]]
    if parent_marker is not None:
        parent_mentions = [span for span in parent_mentions if span == parent_marker]
    def distinctive(owner):
        return {name for name in names.get(owner, ()) if not any(
            other != owner and name in alternatives for other, alternatives in names.items())}
    child_names, parent_names = distinctive(source), distinctive(target)
    for clause in proof:
        # A target binding citing an unrelated clause (even in the same
        # paragraph) cannot supply the antecedent of this relation.
        if not any(item["start"] <= clause["start"] and clause["end"] <= item["end"]
                   for item in target_citations):
            continue
        children = set().union(*(_label_positions([clause], name) for name in child_names))
        if len(children) != 1:
            continue
        child, = children
        for fragment in visible:
            if not fragment["start"] <= clause["start"] < clause["end"] <= fragment["end"]:
                continue
            introductions = [span for span in parent_mentions
                if fragment["start"] <= span["start"] < span["end"] <= fragment["end"]
                and (span["end"] <= clause["start"] or span["start"] >= clause["end"])]
            parents = set().union(*(_label_positions(introductions, name) for name in parent_names))
            if parents:
                # Several earlier introductions of the same bound object are
                # equivalent evidence. Cite the closest, with stable tie-break.
                parent = min(parents, key=lambda pos: (abs(child[0] - pos[0]), pos))
                return child, parent, fragment
    return None


def apply_decision(step: Step, *, source_text, source_hash, batch, objects, relations,
                   entries, visible_passages, source_index, reference_ids=(), evidence_contexts=None,
                   review_items=None, shown_objects=None, citation_passages=None, accepted_edge_ids=None,
                   statement_protocol=None):
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
    unified = isinstance(step, UnifiedStep)
    citation_owners = source_citation_owners(batch, entries) if unified else {}
    if citation_passages is None:
        citation_passages = visible_passages
    elif any(fragment not in visible_passages for fragment in citation_passages):
        raise _state_error("Current-request citations must come from the displayed source.")
    offers = {offer["evidence_id"]: (offer, fragment) for fragment in citation_passages
              for offer in fragment.get("source_citations", [])}
    raw_relations, mention_links, invalid_rows = [], {}, {}
    if unified:
        raw_relations, graph, mention_links, invalid_rows = _unified_relation_plan(step, expected)
    else:
        graph = enumerate(step.relations)

    def problem(exc, issues, path):
        if exc.details["category"] == "internal_state" or not partial:
            raise exc
        issues.append({**exc.details, "path": exc.details.get("path", path), "message": str(exc)})

    def resolve_citation(key, observation_id, path, contexts, registered_marker_ids):
        from .analysis import _evidence

        if key not in offers and key in registered_marker_ids:
            entry = entries[key]
            left, right = entry["start"], entry["end"]
            span = {"start": left, "end": right, "quote": entry["quote"]}
            if source_text[left:right] != entry["quote"] or _evidence(source_text, span, {}) != key:
                raise _state_error("A registered endpoint citation disagrees with its original source.")
            for fragment in citation_passages:
                if fragment["start"] <= left < right <= fragment["end"]:
                    passage = source_index.get(fragment["passage_id"])
                    contexts[key] = {"evidence_id": key, "passage_id": passage["passage_id"],
                                     **_context(source_text, passage, left, right, [fragment])}
                    return key
        if (key not in offers or key not in citation_owners
                or observation_id is not None and observation_id not in citation_owners[key]):
            raise _reject("Evidence ID was not offered for this proposal in the current request.",
                          code="source_citation_unavailable", phase="evidence", path=path)
        offer, fragment = offers[key]
        entry = entries[key]
        left, right = entry["start"], entry["end"]
        if (offer["observation_ids"] != citation_owners[key] or (offer["start"], offer["end"]) != (left, right)
                or not fragment["start"] <= left < right <= fragment["end"]
                or fragment["text"][left-fragment["start"]:right-fragment["start"]] != entry["quote"]
                or source_text[left:right] != entry["quote"] or offer["quote"] != entry["quote"]
                or _evidence(source_text, {"start": left, "end": right, "quote": entry["quote"]}, {}) != key):
            raise _state_error("A displayed citation disagrees with its original source span.")
        passage = source_index.get(fragment["passage_id"])
        contexts[key] = {"evidence_id": key, "passage_id": passage["passage_id"],
                         **_context(source_text, passage, left, right, [fragment])}
        return key

    def evidence(items, path, issues, observation_id=None, registered_marker_ids=()):
        # Stage evidence separately: a rejected proposal must not enlarge the
        # accepted registry or its display contexts, even when some quotes pass.
        candidate_entries = deepcopy(new_entries)
        candidate_contexts, keys = {}, set()
        for number, item in enumerate(items):
            quote_path = f"{path}[{number}]"
            before = len(ambiguities)
            try:
                if isinstance(item, SourceCitation):
                    keys.add(resolve_citation(item.evidence_id, observation_id, quote_path,
                                              candidate_contexts, registered_marker_ids))
                    continue
                key = _quoted_evidence(item, source_text, source_index, visible_passages, candidate_entries,
                    path=quote_path, ambiguities=ambiguities, contexts=candidate_contexts)
                if unified and not partial and len(ambiguities) > before:
                    raise _reject("Evidence has multiple visible source positions; the decision requires review.",
                                  code="quote_ambiguous", phase="evidence",
                                  ambiguity_count=len(ambiguities), ambiguities=ambiguities)
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
        if unified and kind == "relation":
            number = int(path.removeprefix("relations[").removesuffix("]"))
            raw = raw_relations[number]
            observation_ids = []
            if isinstance(raw, dict) and raw.get("source_kind") in ("mention", "observation", "repeat"):
                source = raw.get("source_id")
                if isinstance(source, str) and source in expected:
                    observation_ids.append(source)
        else:
            raw = item.model_dump(mode="json")
            observation_ids = [identifier] if kind in {"binding", "name_resolution"} else []
        value = {"proposal_type": kind, "path": path, "proposal_id": identifier,
                 "observation_ids": observation_ids,
                 "dependencies": list(dict.fromkeys(dependencies)), "code": issues[0]["code"],
                 "issues": issues, "raw_proposal": raw}
        reviews.append(value)

    for number, exc in sorted(invalid_rows.items()):
        path, issues = f"relations[{number}]", []
        problem(exc, issues, path)
        review("relation", path, path, None, issues)

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

    relation_by_id = {item["relation_id"]: item for item in new_relations}
    object_by_id = {item["object_id"]: item for item in new_objects}
    name_counts = Counter(item["name"] for item in new_objects) if unified else {}
    identity_positions = {}

    def apply_graph_relation(number, item):
        path, issues, dependencies = f"relations[{number}]", [], []
        source = endpoint(item.source_id, issues, path + ".source_id", dependencies)
        target = endpoint(item.target_id, issues, path + ".target_id", dependencies)
        if unified and shown_objects is not None and any(
                key not in local and key not in shown_objects for key in (item.source_id, item.target_id)):
            problem(_reject("An object link endpoint was not shown in this request.",
                            phase="object_reference"), issues, path)
        if source is not None and source == target:
            problem(_reject("A relation must connect distinct objects."), issues, path)
        staged = evidence(item.evidence, path + ".evidence", issues)
        marker_evidence = {}
        if unified and not issues:
            proof = [staged[1][key] for key in staged[0]]
            positions = []
            parent_marker = None
            modern_membership = statement_protocol in {"source-v1", "source-v2"} and item.predicate in {"part_of", "sample_of", "member_of"}
            for object_id, marker, field, receipt_key in (
                    (source, item.mention, "mention", "source_mention_id"),
                    (target, item.target_mention, "target_mention", "target_mention_id")):
                explicit = None
                if marker is not None:
                    registered_ids = (object_by_id[object_id]["mention_evidence_ids"]
                                      if shown_objects and object_id in shown_objects else ())
                    anchors = evidence([marker], path + "." + field, issues, registered_marker_ids=registered_ids)
                    if anchors[0]:
                        explicit = anchors[1][anchors[0][0]]
                        marker_evidence[receipt_key] = anchors
                        if field == "target_mention":
                            parent_marker = explicit
                obj = object_by_id[object_id]
                positions.append(_endpoint_positions(obj, new_objects, name_counts[obj["name"]] == 1, proof,
                                     explicit, new_entries, identity_positions.get(object_id, ())))
            if modern_membership and len(marker_evidence) == 2 and not issues:
                # The response supplies the relational statement and both
                # anaphoric endpoints. A registered antecedent may be in a
                # different paragraph; keep it as a separate exact citation.
                for number_in_pair, (ident, field) in enumerate(((source, "source_mention_id"),
                                                                  (target, "target_mention_id"))):
                    if positions[number_in_pair]:
                        continue
                    anchors = marker_evidence[field]
                    key = anchors[0][0]
                    marker = anchors[1][key]
                    obj = object_by_id[ident]
                    grounded = _endpoint_positions(obj, new_objects, name_counts[obj["name"]] == 1,
                        [marker], marker, new_entries, identity_positions.get(ident, ()))
                    shares_marker = any(other["object_id"] != ident and other["name"] == obj["name"]
                                        and key in other["mention_evidence_ids"] for other in new_objects)
                    if grounded and not shares_marker:
                        positions[number_in_pair] = grounded
                        staged[0][:] = sorted(set([*staged[0], key]))
                        staged[1][key] = marker
                        staged[2].update(anchors[2])
            def ends_before(first, other):
                return first and other and min(end for _, end in first) <= max(start for start, _ in other)
            if modern_membership and object_by_id[source]["name"] == object_by_id[target]["name"]:
                registered_source = set(object_by_id[source]["mention_evidence_ids"])
                registered_target = set(object_by_id[target]["mention_evidence_ids"])
                if not registered_source - registered_target or not registered_target - registered_source:
                    problem(_reject("These same-name objects lack distinct registration evidence.",
                                    code="relation_endpoint_unanchored", phase="relation"), issues, path)
            if (not issues and len(marker_evidence) == 2 and item.predicate in {"part_of", "sample_of", "member_of"}
                    and bool(positions[0]) != bool(positions[1])):
                # The model can explicitly cite a registered antecedent to a
                # reference in its relation clause. Preserve both original
                # citations, even when the introductory name is outside it.
                missing = 0 if not positions[0] else 1
                identifier = (source, target)[missing]
                field = ("source_mention_id", "target_mention_id")[missing]
                anchors = marker_evidence[field]
                key = anchors[0][0]
                marker = anchors[1][key]
                registered_owners = [obj["object_id"] for obj in new_objects if key in obj["mention_evidence_ids"]]
                if (registered_owners == [identifier] and any(
                        fragment["start"] <= marker["start"] < marker["end"] <= fragment["end"]
                        and any(fragment["start"] <= part["start"] < part["end"] <= fragment["end"] for part in proof)
                        for fragment in citation_passages)):
                    positions[missing] = {(marker["start"], marker["end"])}
                    staged[0][:] = sorted(set([*staged[0], key]))
                    staged[1][key] = marker
                    staged[2].update(anchors[2])
            if not (ends_before(*positions) or ends_before(*reversed(positions))):
                located = None
                if modern_membership and not issues:
                    # A qualified noun phrase can identify a part and its
                    # parent simultaneously. Require two distinct grounded
                    # spellings, unique within the supplied proof and registry.
                    literals = []
                    for ident, choices in zip((source, target), positions, strict=True):
                        name = object_by_id[ident]["name"]
                        literals.append(choices & _label_positions(proof, name) if name_counts[name] == 1 else set())
                    if all(len(options) == 1 for options in literals):
                        child, parent = (next(iter(options)) for options in literals)
                        if child != parent and child[0] < parent[1] and parent[0] < child[1]:
                            fragment = next((p for p in visible_passages if p["start"] <= min(child[0], parent[0])
                                             and max(child[1], parent[1]) <= p["end"]), None)
                            if fragment:
                                located = child, parent, fragment
                if (not located and not issues and item.predicate in {"part_of", "sample_of", "member_of"}
                        and item.mention is None):
                    located = _bound_membership_mentions(source, target, proof, batch, updates,
                                                         object_by_id, staged[1], citation_passages, parent_marker)
                if located:
                    from .analysis import _evidence

                    child, parent, fragment = located
                    passage = source_index.get(fragment["passage_id"])
                    if parent_marker and parent_marker["evidence_id"] not in staged[0]:
                        key = parent_marker["evidence_id"]
                        staged[0].append(key)
                        staged[1][key] = parent_marker
                        staged[2].update(marker_evidence["target_mention_id"][2])
                    for field, (left, right) in (("source_mention_id", child), ("target_mention_id", parent)):
                        key = _evidence(source_text, {"start": left, "end": right,
                                        "quote": source_text[left:right]}, staged[1])
                        staged[2][key] = {"evidence_id": key, "passage_id": passage["passage_id"],
                                         **_context(source_text, passage, left, right, [fragment])}
                        if key not in staged[0]:
                            staged[0].append(key)
                        marker_evidence[field] = ([key], {}, {})
                    staged[0].sort()
                else:
                    problem(_reject("The cited relation evidence must identify two distinct endpoint mentions.",
                                    code="relation_endpoint_unanchored", phase="relation"), issues, path)
        if issues:
            review("relation", path, path, item, issues, dependencies)
            return
        value = {"source_id": source, "target_id": target, "predicate": item.predicate,
                 "evidence_ids": staged[0]}
        key = "relation-" + digest(value)[:24]
        if not unified and key in declared_relations:
            problem(_reject("A relation is repeated in the decision."), issues, path)
            review("relation", path, path, item, issues)
            return
        declared_relations.add(key)
        existing = relation_by_id.get(key)
        marker_ids = {field: receipt[0][0] for field, receipt in marker_evidence.items()}
        if existing is not None:
            if any(field in existing and existing[field] != marker for field, marker in marker_ids.items()):
                problem(_reject("Different occurrences were selected for the same relation endpoint.",
                                code="relation_anchor_conflict", phase="relation"), issues, path)
                review("relation", path, path, item, issues)
                return
            existing.update(marker_ids)
        else:
            edge = {"relation_id": key, **value, **marker_ids, "semantic_status": "model_proposed"}
            new_relations.append(edge)
            relation_by_id[key] = edge
        retain_evidence(staged)
        for receipt in marker_evidence.values():
            retain_evidence(receipt)
        if accepted_edge_ids is not None:
            accepted_edge_ids[number] = key
    if not unified:
        for number, item in graph:
            apply_graph_relation(number, item)

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
        staged = evidence(item.evidence, path + ".evidence", issues, observation_id=item.observation_id)
        blockers = deepcopy(by_id[item.observation_id].get("blockers", []))
        if issues:
            review("binding", path, item.observation_id, item, issues, dependencies)
            if unified and item.observation_id in mention_links:
                link_number, _ = mention_links[item.observation_id]
                link_path, link_issues = f"relations[{link_number}]", []
                problem(_reject("The binding for this mention was not applied.",
                                code="dependency_rejected", phase="binding"), link_issues, link_path)
                review("relation", link_path, link_path, None, link_issues, [item.observation_id])
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
        if unified and subject and item.status == "supported":
            # The binding connects this precise mention to its composed registry
            # label. It may support the graph only in the very same cited text.
            observed_name = by_id[item.observation_id].get("entity")
            if isinstance(observed_name, str):
                proof = [staged[1][key] for key in staged[0]]
                identity_positions.setdefault(subject, set()).update(_label_positions(proof, observed_name))
        if unified:
            if item.observation_id not in mention_links:
                continue
            link_number, name = mention_links[item.observation_id]
            path = f"relations[{link_number}]"
        else:
            name = item.name_resolution
            path += ".name_resolution"
        if name is None:
            continue
        target = endpoint(name.target_id, issues, path + ".target_id", dependencies)
        rivals = [endpoint(key, issues, path + ".competitor_ids", dependencies) for key in name.competitor_ids]
        if shown_objects is not None and any(
                key not in local and key not in shown_objects for key in [name.target_id, *name.competitor_ids]):
            problem(_reject("The selected name endpoint was not shown in this request.",
                            phase="name_resolution"), issues, path)
        parts = {key: evidence(quotes, path + "." + key, issues) for key, quotes in (
            ("mention", [name.mention]), ("target_mention", [name.target_mention]),
            ("evidence", name.evidence), ("scope_evidence", name.scope_evidence))}
        relation_type = name.predicate if unified else name.relation
        if relation_type == "different_from":
            relation_type = "different"
        reason = (name.reason or "Model-proposed mention link.") if unified else name.reason
        if (not reason.strip() or target in rivals or len(set(rivals)) != len(rivals)
                or (relation_type == "reusable_alias" and not name.scope_evidence)
                or (relation_type in {"related", "different"} and subject is not None and target == subject)):
            problem(_reject("Name judgment has inconsistent endpoints, scope or competitors.",
                            phase="name_resolution"), issues, path)
        if issues:
            if unified:
                review("relation", path, path, name, issues, dependencies)
            else:
                review("name_resolution", path, item.observation_id, name, issues, dependencies)
            continue
        for staged_part in parts.values():
            retain_evidence(staged_part)
        updates[item.observation_id]["name_resolution"] = {
            "observation_id": item.observation_id, "source_id": subject, "target_id": target,
            "relation": relation_type, "reason": reason, "competitor_ids": rivals,
            "semantic_status": "model_proposed",
            "mention_id": parts["mention"][0][0], "target_mention_id": parts["target_mention"][0][0],
            "evidence_ids": parts["evidence"][0], "scope_evidence_ids": parts["scope_evidence"][0]}
        if unified and relation_type in {"local_identity", "reusable_alias"} and not rivals:
            mention, label = (new_entries[parts[field][0][0]] for field in ("mention", "target_mention"))
            def contains_any(ids, anchor):
                return any(new_entries[key]["start"] <= anchor["start"] and anchor["end"] <= new_entries[key]["end"]
                           for key in ids)
            if (contains_any(object_by_id[target]["mention_evidence_ids"], label)
                    and all(contains_any(parts["evidence"][0], span) for span in (mention, label))):
                identity_positions.setdefault(target, set()).update((s["start"], s["end"]) for s in (mention, label))
    if unified:
        for number, item in graph:
            if isinstance(item, RepeatedReading):
                path, issues = f"relations[{number}]", []
                if item.target_id == item.source_id or item.target_id not in expected.union(reference_ids):
                    problem(_reject("The duplicate must reference a different displayed observation.", phase="relation"), issues, path)
                staged = evidence(item.evidence, path + ".evidence", issues)
                if len(staged[0]) < 2:
                    problem(_reject("Supply separate original citations for both observations.", phase="relation"), issues, path)
                if issues:
                    review("relation", path, path, item, issues)
                    continue
                retain_evidence(staged)
                updates[item.source_id].setdefault("reviewed_repeats", []).append({
                    "target_id": item.target_id, "explanation": item.explanation, "evidence_ids": staged[0]})
                continue
            if not isinstance(item, FilingLink):
                apply_graph_relation(number, item)
                continue
            path, issues, dependencies = f"relations[{number}]", [], []
            target = endpoint(item.target_id, issues, path + ".target_id", dependencies)
            if shown_objects is not None and item.target_id not in local and target not in shown_objects:
                problem(_reject("The filing target was not shown in this decision.", phase="object_reference"), issues, path)
            staged = evidence(item.evidence, path + ".evidence", issues, observation_id=item.source_id)
            if issues:
                review("relation", path, path, item, issues, dependencies)
                continue
            retain_evidence(staged)
            updates[item.source_id].setdefault("reviewed_row_relations", []).append({
                "target_id": target, "explanation": item.explanation, "field_applicability": item.field_applicability,
                "target_basis": item.target_basis, "target_contains_direct": item.target_contains_direct,
                "evidence_ids": staged[0]})
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
