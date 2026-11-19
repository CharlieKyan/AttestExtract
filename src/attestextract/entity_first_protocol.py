"""Model contracts and exact source checks for entity-first extraction."""

import re
from typing import Literal

from pydantic import Field

from .execution import digest
from .measurement_syntax import numeric_tokens
from .models import ContractModel


class Citation(ContractModel):
    window: str
    quote: str = Field(min_length=1)
    occurrence: int | None = Field(default=None, ge=0)


class Entity(ContractModel):
    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=500)
    mention: Citation
    qualification: Citation = Field(description="Source establishing this named object's task row role.")
    scope: str | None = Field(default=None, description="Verbatim disambiguating qualifier in qualification.")
    kind: Literal["instance", "group", "type", "model", "other"] = "other"
    aliases: list[Citation] = Field(default_factory=list, max_length=8,
        description="Explicit equivalence statements; each must quote the canonical and alternate names.")
    alias_names: list[str] = Field(default_factory=list, max_length=8)


class Inventory(ContractModel):
    objects: list[Entity] = Field(max_length=96)


class Fact(ContractModel):
    field: str
    value: str = Field(min_length=1)
    value_source: Citation = Field(description="Exact value (in primary block), not its containing paragraph.")
    property_source: Citation = Field(description="Actual property wording from the source, not a schema key.")
    owner: str | None = Field(description="A shown roster ID or a nomination key; null for details only.")
    owner_source: Citation | None = Field(description="Exact literal owner name/alias in this block or its context.")
    link_source: Citation | None = Field(description="Prose clause linking this owner to this value. For tables use the owner cell.")
    fit: Literal["supported", "unsupported", "unknown"]
    fit_source: Citation | None = Field(description="Evidence for required field qualifiers; null if absent.")
    direct: bool = Field(description="True only if this is the stated owner's own property at the requested grain.")
    unit: str | None = None
    conditions: str | None = None
    statistic: str | None = None
    qualifier_sources: list[Citation] = Field(default_factory=list, max_length=12)
    purpose: Literal["measurement", "model_input", "classification_threshold", "aggregate", "example", "unspecified"]
    reason: str = Field(min_length=1, max_length=1500)


class Assignments(ContractModel):
    nominations: list[Entity] = Field(max_length=48)
    facts: list[Fact] = Field(max_length=96)


class Confirmation(ContractModel):
    key: str
    status: Literal["new_object", "existing_object", "not_target", "uncertain"]
    existing_id: str | None = None
    evidence: Citation | None = None
    reason: str = Field(min_length=1, max_length=1500)


class Confirmations(ContractModel):
    decisions: list[Confirmation] = Field(max_length=48)


def windows(blocks):
    out = {}
    for block in blocks:
        key = block["block_id"]
        out[key] = (block["start"], block["text"])
        for i, context in enumerate(block["context"]):
            out[f"{key}/context/{i}"] = (context["start"], context["text"])
    return out


def locate(citation, shown):
    if citation is None or citation.window not in shown:
        raise ValueError("citation_window_missing")
    offset, text = shown[citation.window]
    pos = text.find(citation.quote)
    if pos < 0 or not citation.quote.strip():
        raise ValueError("citation_not_in_source")
    if citation.occurrence is None:
        if text.find(citation.quote, pos + 1) >= 0:
            raise ValueError("citation_not_unique")
    else:
        for _ in range(citation.occurrence):
            pos = text.find(citation.quote, pos + 1)
            if pos < 0:
                raise ValueError("citation_occurrence_missing")
    return {"start": offset + pos, "end": offset + pos + len(citation.quote), "quote": citation.quote}


def entity_record(proposal, shown, block_scope, document, stage):
    mention = locate(proposal.mention, shown)
    proof = locate(proposal.qualification, shown)
    if (proposal.label != mention["quote"] or proposal.label not in proof["quote"]
            or proposal.scope and proposal.scope not in proof["quote"]):
        raise ValueError("invalid_identity_evidence")
    aliases = []
    if len(proposal.alias_names) != len(proposal.aliases):
        raise ValueError("missing_alias_evidence")
    for name, citation in zip(proposal.alias_names, proposal.aliases):
        span = locate(citation, shown)
        if not name.strip() or name == proposal.label or not all(x in span["quote"] for x in [name, proposal.label]):
            raise ValueError("alias_not_grounded")
        aliases.append({"name": name, "evidence": span})
    key = digest([document, mention["start"], mention["end"], block_scope, proposal.scope])[:24]
    return {"object_id": "entity-" + key, "label": proposal.label, "scope": proposal.scope or block_scope,
            "section": block_scope,
            "qualifier": proposal.scope, "kind": proposal.kind,
            "evidence": [mention, proof], "aliases": aliases, "stage": stage, "basis": "model_proposed"}


def _cell(table, span):
    return next((c for c in table.cells if c.span.start <= span["start"] and span["end"] <= c.span.end), None)


def _panel_allows(table, source, a, v, h, horizontal):
    """Repeated owner-role headers or an empty separator identify a local panel.

    A repeated property alone says nothing about the owner (e.g. one subject with
    measurements before and after an event). Handle transposed axes identically.
    """
    axis, cross = ("column", "row") if horizontal else ("row", "column")
    cross_extent = "rowspan" if horizontal else "colspan"
    axis_extent = "colspan" if horizontal else "rowspan"
    def at(cell):
        return getattr(cell, axis)

    def text(cell):
        return source[cell.span.start:cell.span.end].strip()
    # Use the cited property's header band; a merged role title may span that band.
    band = sorted((c for c in table.cells if getattr(c, cross) <= getattr(h, cross)
                   < getattr(c, cross) + getattr(c, cross_extent)), key=at)
    role = next((c for c in band if at(c) <= at(a) < at(c) + getattr(c, axis_extent)), None)
    left, right = sorted((at(a), at(v)))
    for separator in band:
        if left < at(separator) < right and not text(separator):
            if all(not text(c) for c in table.cells if at(c) <= at(separator) < at(c) + getattr(c, axis_extent)):
                return False
    if role is None or not text(role):
        return True
    roles = [c for c in band if text(c) == text(role)]
    if len(roles) == 1:
        return True
    properties = [c for c in band if text(c) == text(h)]
    if len(roles) == len(properties) and text(role) != text(h):
        # Each role/property pair must occupy its own contiguous band, allowing
        # both owner-first and owner-last layouts. Interlaced pairs are ambiguous.
        pairs = [(min(at(x), at(y)), max(at(x), at(y))) for x, y in zip(roles, properties)]
        if all(end < next_start for (_, end), (next_start, _) in zip(pairs, pairs[1:])):
            return roles.index(role) == properties.index(h)
        return False
    if roles[0] == band[0]:
        # Owner-led panels can contain more than one property column or row.
        return max((at(c) for c in roles if at(c) <= at(v)), default=-1) == at(role)
    return False


def table_aligned(table, source, owner, value, prop):
    """Locate three cited cells in the same grid with a common scope on each axis."""
    if table is None:
        return False
    a, v, h = (_cell(table, span) for span in (owner, value, prop))
    if any(c is None for c in (a, v, h)) or len({a.cell_id, v.cell_id, h.cell_id}) != 3:
        return False
    def covers(head, data, axis, extent):
        return (getattr(head, axis) <= getattr(data, axis) and getattr(data, axis) + getattr(data, extent)
                <= getattr(head, axis) + getattr(head, extent))
    return ((covers(a, v, "row", "rowspan") and covers(h, v, "column", "colspan")
             and _panel_allows(table, source, a, v, h, True)) or
            (covers(a, v, "column", "colspan") and covers(h, v, "row", "rowspan")
             and _panel_allows(table, source, a, v, h, False)))


def _contained(span, outer):
    return outer["start"] <= span["start"] and span["end"] <= outer["end"]


def _table_qualifier(table, source, span, owner, value, prop):
    # Accept the fact's own cell or its applicable local property-axis header.
    if table is None:
        return False
    q, v, h, a = (_cell(table, s) for s in (span, value, prop, owner))
    if any(c is None for c in (q, v, h, a)):
        return False
    if q.cell_id in {v.cell_id, h.cell_id}:
        return True
    if not table_aligned(table, source, owner, value, span):
        return False
    if a.row <= v.row < a.row + a.rowspan:
        return q.row in table.header_rows or q.row <= h.row < v.row
    return q.column in table.row_header_columns or q.column <= h.column < v.column


def _one_statement(span):
    return not re.search(r"[;；。!?！？\n]|\.\s+(?=\S)", span["quote"].strip().rstrip(".;；。!?！？"))


def _other_owner_in(text, owner_names, choices):
    for candidate in choices.values():
        for name in [candidate["label"], *(a["name"] for a in candidate["aliases"])]:
            if any(name in label for label in owner_names):
                continue
            before = r"(?<![a-zA-Z0-9_])" if name[0].isascii() and name[0].isalnum() else ""
            after = r"(?![a-zA-Z0-9_])" if name[-1].isascii() and name[-1].isalnum() else ""
            if re.search(before + re.escape(name) + after, text):
                return True
    return False


def _local_assertion(link, owner, prop, value, owner_names, choices):
    if not link or not _one_statement(link) or not all(_contained(s, link) for s in (owner, prop, value)):
        return None
    # The unregistered second owner in a parallel assertion may be absent from the
    # whitelist. Always require positive assignment inside the value's own clause.
    text = link["quote"]
    cuts = list(re.finditer(r"(?<!\d),|,(?!\d)|，|\b(?:whereas|while|but)\b", text, re.I))
    if text.count(prop["quote"]) > 1:
        cuts.extend(re.finditer(r"\band\b", text, re.I))
    left = max([0, *(m.end() for m in cuts if link["start"] + m.end() <= value["start"])])
    right = min([len(text), *(m.start() for m in cuts if link["start"] + m.start() >= value["end"])])
    clause = {"start": link["start"] + left, "end": link["start"] + right, "quote": text[left:right]}
    if (_contained(owner, clause) and _contained(prop, clause) and clause["quote"].count(prop["quote"]) == 1
            and not _other_owner_in(clause["quote"], owner_names, choices)):
        return clause
    return None


def fact_record(fact, shown, block, source, document, fields, choices, table):
    if fact.field not in fields:
        raise ValueError("undeclared_field")
    if fact.value_source.window != block["block_id"] or fact.value_source.quote != fact.value:
        raise ValueError("value_not_locally_attested")
    value = locate(fact.value_source, shown)
    prop = locate(fact.property_source, shown)
    evidence = [value, prop]
    # Validate numeric boundaries against the source around the quote, including signs.
    if numeric_tokens(fact.value) == [(0, len(fact.value))]:
        left = max(block["start"], value["start"] - 64)
        text = source[left:min(block["end"], value["end"] + 64)]
        if (value["start"] - left, value["end"] - left) not in numeric_tokens(text):
            raise ValueError("numeric_substring")
    obj = choices.get(fact.owner)
    owner = locate(fact.owner_source, shown) if fact.owner_source else None
    link = locate(fact.link_source, shown) if fact.link_source else None
    fit_span = locate(fact.fit_source, shown) if fact.fit_source else None
    qualifications = [locate(c, shown) for c in fact.qualifier_sources]
    evidence.extend(e for e in [owner, link, fit_span, *qualifications] if e and e not in evidence)
    names = [obj["label"], *(a["name"] for a in obj["aliases"])] if obj else []
    owner_ok = bool(obj and owner and owner["quote"] in names and link)
    local = link
    if owner_ok:
        if block["kind"] == "table":
            owner_ok = table_aligned(table, source, owner, value, prop)
        else:
            local = _local_assertion(link, owner, prop, value, names, choices)
            owner_ok = local is not None
        competitors = [o for o in choices.values() if o["object_id"] != obj["object_id"] and
                       owner["quote"] in {o["label"], *(a["name"] for a in o["aliases"])}]
        if competitors:
            # Repeated names require a distinguishing attested qualifier in the assignment itself.
            owner_ok = bool(owner_ok and obj.get("qualifier") and obj["qualifier"] in link["quote"]
                            and all(o.get("qualifier") != obj["qualifier"] for o in competitors))
        if obj["section"] != block["scope"] and not (obj.get("qualifier") and obj["qualifier"] in link["quote"]):
            owner_ok = False
    qualifiers_ok = all(not term or any(term in e["quote"] for e in evidence)
                        for term in (fact.unit, fact.statistic, fact.conditions))
    if block["kind"] == "table":
        local_fit = (owner is not None and fit_span is not None and
                     all(_table_qualifier(table, source, s, owner, value, prop) for s in [fit_span, *qualifications]))
    else:
        # For unknown owners the clause can still establish the measured property.
        anchor = local or fit_span
        local_fit = bool(anchor and fit_span and _one_statement(anchor)
                         and all(_contained(s, anchor) for s in [prop, value, fit_span, *qualifications]))
    fit_ok = bool(fact.fit == "supported" and local_fit and qualifiers_ok)
    # Scope exclusions are explicit protocol outcomes (the same field may apply to a different owner).
    if fact.purpose in {"classification_threshold", "example"} or (
            fact.purpose == "aggregate" and not (obj and obj["kind"] in {"group", "type"})):
        fit_ok = False
    assignable = bool(fact.direct and owner_ok)
    reason = fact.reason
    if fact.owner and not assignable:
        reason = "table_owner_or_panel_unconfirmed" if block["kind"] == "table" else "ownership_not_established"
    elif fact.fit == "supported" and not fit_ok:
        reason = "field_or_qualifier_scope_unconfirmed"
    obs_id = "efobs-" + digest([document, fact.field, value["start"], value["end"],
                                prop["start"], prop["end"]])[:24]
    return {"observation_id": obs_id, "field": fact.field, "value": fact.value,
            "entity": owner["quote"] if owner else None, "unit": fact.unit, "statistic": fact.statistic,
            "conditions": fact.conditions, "purpose": fact.purpose, "block_id": block["block_id"],
            "source_kind": block["kind"], "evidence": evidence, "owner_id": obj["object_id"] if assignable else None,
            "value_source": value, "owner_source": owner, "property_source": prop,
            "qualified": bool(fit_ok and assignable), "field_fit": fact.fit, "property_name": prop["quote"],
            "reason": reason, "model_reason": fact.reason, "owner_supported": assignable, "field_supported": fit_ok}
