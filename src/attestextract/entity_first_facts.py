"""Source-bound decisions made at the point of a two-pass attribute assignment."""

import re
from typing import Literal

from pydantic import Field

from .admission_tables import _Grid, _axis, _captions, _group_parameter, _group_property, _in_header_band, _scope_guard
from .admission_text import _NON_MEASUREMENT, _decision, _locate, _unit_scope_unclear, _value_span
from .entity_first_protocol import (
    Citation, Entity, _assertion_text, _attested_unit, _cell, _contained, _local_assertion,
    _name_span, _one_statement, _other_owner_in, _potential_denial, _table_qualifier,
    primary_window, table_aligned,
)
from .execution import digest
from .models import ContractModel


class V3Fact(ContractModel):
    """A source observation has a subject mention independently of a registered row."""

    field: str
    value: str = Field(min_length=1, description="Original value including any range or bound.")
    value_source: Citation
    property_source: Citation
    source_subject: str | None = Field(description="Verbatim local object or complete group name, even without a row ID.")
    subject_source: Citation | None
    subject_qualifier: str | None = Field(default=None, description="Verbatim same-role distinguishing qualifier, when stated for the subject.")
    target_id: str | None = Field(description="An ID shown in this request, if the observation describes that same object.")
    nomination_key: str | None = Field(default=None, description="Same-answer nomination key for a newly named row.")
    link_source: Citation | None = Field(description="Local assertion linking subject, property and value, or a subject cell.")
    fit: Literal["supported", "unsupported", "unknown"]
    fit_source: Citation | None = Field(description="Evidence for the property as defined by the task, with its qualifiers.")
    purpose: Literal["measurement", "model_input", "classification_threshold", "aggregate", "example", "unspecified"]
    unit: str | None = None
    conditions: str | None = None
    statistic: str | None = None
    qualifier_sources: list[Citation] = Field(default_factory=list, max_length=12)
    members: list[str] = Field(default_factory=list, max_length=96, description="Verbatim member names of a collection.")
    reason: str = Field(min_length=1, max_length=1500)


class V3Assignments(ContractModel):
    nominations: list[Entity] = Field(max_length=48)
    facts: list[V3Fact] = Field(max_length=96)
    review_regions: list[Citation] = Field(default_factory=list, max_length=24)


def _names(obj):
    return [obj["label"], *(a["name"] for a in obj["aliases"])]


def _target(fact, subject, roster, shown_ids, nomination_ids, proof):
    choices = [o for o in roster.values() if subject and subject["quote"] in _names(o)]
    candidates = [{"id": o["object_id"], "label": o["label"], "qualifier": o.get("qualifier"),
                   "status": "proposed", "evidence": o["evidence"]} for o in choices]
    selected = fact.target_id if fact.target_id in shown_ids else None
    if fact.nomination_key and not fact.target_id:
        selected = nomination_ids.get(fact.nomination_key)
    obj = roster.get(selected)
    if fact.target_id and fact.nomination_key:
        obj = None
    if not obj or obj not in choices:
        return _decision("unknown", "target_identity_pending"), candidates
    if fact.subject_qualifier and obj.get("qualifier") and (
            not any(fact.subject_qualifier in s["quote"] for s in proof)
            or not any(obj["qualifier"] in s["quote"] for s in proof)):
        return _decision("unknown", "subject_qualifier_requires_review"), candidates
    others = [o for o in choices if o["object_id"] != selected]
    if others:
        qualified = [o for o in choices if o.get("qualifier") and
                     any(o["qualifier"] in s["quote"] for s in proof)]
        if qualified != [obj]:
            return _decision("unknown", "competing_subject_identities"), candidates
    if (fact.members or fact.purpose == "aggregate") and obj["kind"] != "group":
        return _decision("unknown", "collection_not_individual"), candidates
    target = _decision("supported", "selected_document_object", obj["evidence"], obj["label"], selected, obj["scope"])
    target["alias_names"] = _names(obj)[1:]
    target["binding_evidence"] = proof
    return target, candidates


def finalize_target(obs, roster):
    """A later homonym can challenge an earlier selection; reuse that claim's original scope proof."""
    selected = obs["target"].get("id")
    if not selected:
        return False
    claim = V3Fact.model_validate(obs["model_claim"])
    target, candidates = _target(claim, obs["local_mention"], roster, {selected},
                                 {claim.nomination_key: selected}, obs["target"]["binding_evidence"])
    obs["candidates"] = candidates
    obs["target"] = target
    if target["status"] == "supported":
        return False
    obs.update(owner_id=None, qualified=False, eligible=False, reason=target["reason"])
    obs["direct"] = {**obs["direct"], "id": None}
    return True


def _threshold_field(definition):
    # A threshold is a valid property when the requested field itself names that quantity.
    leading = re.split(r"[,;:.，；。：\n]", definition, maxsplit=1)[0]
    if _potential_denial(leading, "") or re.search(r"\b(?:exclude|omit|excluding|except)\b|排除|不含", leading, re.I):
        return False
    return bool(re.search(r"\b(?:thresholds?|cutoffs?|acceptance limits?)\b|阈值|临界值|分类界限", leading, re.I))


def _table_context(source, table, fact, definition, subject, value, prop, link, fit_span, qualifiers):
    if table is None or not (subject and value and prop and link):
        return False, "table_relation_unverified", prop
    a, v, h = (_cell(table, s) for s in (subject, value, prop))
    if not (a and v and h):
        return False, "table_cell_unlocated", prop
    grid = _Grid(source, table, {"windows": []})
    prop = grid.whole(h)
    if grid.text(v) != value["quote"] or grid.text(a) != subject["quote"]:
        return False, "partial_table_cell", prop
    aligned = table_aligned(table, source, subject, value, prop)
    role = next((c for c in table.cells if c.row == h.row and c.column == a.column
                 or c.column == h.column and c.row == a.row), None)
    axis = _axis(role, a) if role else None
    group_pair = False
    if fact.members and not aligned:
        group_role = next((c for c in table.cells if c.row == a.row and c.column + 1 == a.column), None)
        titles = " ".join(span["quote"] for span in _captions(table, source))
        group_pair = bool(group_role and _group_parameter(grid, group_role, a, h, v)
                          and _group_property(prop["quote"], fact.unit, definition, titles))
    if not (aligned or group_pair) or _cell(table, link) not in (a, h):
        return False, "table_relation_unverified", prop
    if fact.purpose == "measurement" and _NON_MEASUREMENT.search(prop["quote"]):
        return True, "measurement_scope_requires_review", prop
    guard = _scope_guard(table, source, {"purpose": "measurement" if fact.purpose == "measurement" else "unspecified"})
    if guard == "classification_scope_requires_review" and fact.purpose == "classification_threshold" and _threshold_field(definition):
        guard = None
    if guard:
        return True, guard, prop
    captions = _captions(table, source)
    for span in [fit_span, *qualifiers]:
        if span and span in qualifiers and any(_contained(span, caption) for caption in captions):
            continue
        if not span or not (c := _cell(table, span)):
            return True, "field_evidence_unlocated", prop
        in_band = axis and _in_header_band(role, c, v, axis) and table_aligned(table, source, subject, value, span)
        if c not in (h, v) and not in_band and not _table_qualifier(table, source, span, subject, value, prop):
            return True, "qualifier_outside_header_path", prop
    # A merged property heading can have distinct statistic leaves along either axis.
    if axis:
        for cell in table.cells:
            if (getattr(cell, axis) > getattr(h, axis) and _in_header_band(role, cell, v, axis)
                    and grid.text(cell) and grid.text(cell) not in (fact.statistic, fact.conditions, fact.unit)):
                return True, "statistic_or_condition_missing", prop
    _, slash, suffix = grid.text(h).partition("/")
    if slash and suffix.strip() != fact.unit:
        return True, "unit_differs_from_header", prop
    # A slash in a column header separates the label and its unit. Elsewhere preserve compound-unit checks.
    if not slash and not _attested_unit(fact.unit, [prop, fit_span, value, *qualifiers], source):
        return True, "unit_not_attested", prop
    return True, None, prop


def _prose_context(source, block, fact, roster, subject, value, prop, link, fit_span, qualifiers):
    linked = bool(link and _one_statement(link) and prop and value and
                  all(_contained(s, link) for s in (prop, value)))
    direct = bool(linked and subject and _contained(subject, link))
    if direct and not fact.members:
        others = {k: o for k, o in roster.items() if subject["quote"] not in _names(o)}
        if link["quote"].count(prop["quote"]) > 1 or _other_owner_in(link["quote"], [subject["quote"]], others):
            direct = bool(_local_assertion(link, subject, prop, value, [subject["quote"]], others))
    if not linked or not fit_span or not all(s and _contained(s, link) for s in [fit_span, *qualifiers]):
        return direct, "field_evidence_outside_assertion"
    assertion = _assertion_text(source, block, link)
    if _potential_denial(assertion, "") or fact.purpose == "measurement" and _NON_MEASUREMENT.search(assertion):
        return direct, "assertion_scope_requires_review"
    if not _attested_unit(fact.unit, [link], source) or _unit_scope_unclear(source, fact, value, link, prop):
        return direct, "unit_scope_requires_review"
    return direct, None


def assess_fact(fact, shown, block, source, document, fields, roster, shown_ids, nomination_ids, table=None):
    """Resolve one claim against only the original text/cells and the actual shown/locally nominated IDs."""
    link = _locate(fact.link_source, shown, source)
    value_context = _locate(fact.value_source, shown, source, link)
    value, rendered = _value_span(value_context, fact.value, source)
    if primary_window(fact.value_source.window) != block["block_id"]:
        value = None
    prop = _locate(fact.property_source, shown, source, link)
    subject = _locate(fact.subject_source, shown, source, link)
    if subject and fact.source_subject:
        try:
            subject = _name_span(subject, fact.source_subject, shown[fact.subject_source.window])
        except ValueError:
            subject = None
    else:
        subject = None
    fit_span = _locate(fact.fit_source, shown, source, link)
    quals = [_locate(c, shown, source, link) for c in fact.qualifier_sources]
    definition = fields.get(fact.field, "")
    if block["kind"] == "table":
        direct_ok, field_issue, prop = _table_context(source, table, fact, definition, subject, value, prop, link, fit_span, quals)
        scope = table.table_id if table else block["scope"]
        context = [prop, value, fit_span, *quals]
    else:
        direct_ok, field_issue = _prose_context(source, block, fact, roster, subject, value, prop, link, fit_span, quals)
        scope = block["scope"]
        context = [link]
    local_proof = [s for s in context if s]
    if any(term and not any(term in s["quote"] for s in local_proof) for term in (fact.statistic, fact.conditions)):
        field_issue = "qualifier_not_attested"
    if fact.purpose == "example" or fact.purpose == "classification_threshold" and not _threshold_field(definition):
        field_issue = "limited_purpose"
    if fact.fit == "supported" and _potential_denial(fact.reason, definition):
        field_issue = "model_reports_possible_contradiction"
    if rendered or not value or fact.field not in fields or not fact.value.strip():
        field_issue = "value_or_field_unverified"
    fit_status = "contradicted" if fact.fit == "unsupported" else (
        "supported" if fact.fit == "supported" and not field_issue else "unknown")
    fit = _decision(fit_status, field_issue or "reported_field_" + fact.fit, [prop, fit_span, *quals])
    # A name printed in the source remains inspectable even when its relation to the value is unclear.
    direct = _decision("supported" if direct_ok else "unknown",
                       "local_subject_attested" if direct_ok else "local_relation_pending",
                       [subject, link], subject["quote"] if subject else None, scope=scope)
    target, candidates = _target(fact, subject, roster, shown_ids, nomination_ids, local_proof)
    if direct_ok and target["status"] == "supported":
        direct["id"] = target["id"]
    eligible = bool(direct["id"] and fit_status == "supported")
    reason = next((d["reason"] for d in (direct, target, fit) if d["status"] != "supported"), "source_supported")
    anchored_members = [m for m in dict.fromkeys(fact.members) if m.strip() and
                        any(s and m in s["quote"] for s in (subject, link))]
    evidence = []
    for span in (value, value_context, subject, prop, link, fit_span, *quals):
        if span and span not in evidence:
            evidence.append(span)
    oid = "efobs-" + digest([document, value["start"], value["end"], fact.field])[:24] if value else None
    if table is not None and value:
        # In one grid a value cell belongs to one source observation even across clipped re-reads.
        cell = _cell(table, value)
        if cell:
            oid = "efobs-" + digest([document, table.table_id, cell.cell_id])[:24]
    return {"observation_id": oid, "field": fact.field, "value": fact.value, "unit": fact.unit,
            "conditions": fact.conditions, "statistic": fact.statistic, "purpose": fact.purpose,
            "direct": direct, "target": target, "fit": fit, "entity": direct["label"],
            "candidates": candidates, "evidence": evidence, "owner_source": subject, "local_mention": subject,
            "value_source": value, "property_source": prop, "source_property": prop["quote"] if prop else None,
            "block_id": block["block_id"], "source_kind": block["kind"], "table_id": table.table_id if table else None,
            "owner_id": target["id"], "qualified": eligible, "eligible": eligible, "owner_supported": direct_ok,
            "field_supported": fit_status == "supported", "field_fit": fact.fit, "members": anchored_members,
            "reason": reason, "model_reason": fact.reason, "evidence_issues": [], "model_claim": fact.model_dump(mode="json"),
            "record_group": [block["block_id"], subject, fact.purpose], "related": [], "row_ids": []}
