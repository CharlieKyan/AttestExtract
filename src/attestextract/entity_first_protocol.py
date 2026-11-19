"""Model contracts and exact source checks for entity-first extraction."""

import re
from typing import Literal

from pydantic import Field

from .execution import digest
from .measurement_syntax import numeric_tokens
from .models import ContractModel


class Citation(ContractModel):
    window: str
    quote: str | None = Field(default=None, min_length=1,
        description="Verbatim excerpt. May be omitted for a displayed whole-cell reference or a review region.")
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
    review_regions: list[Citation] = Field(default_factory=list, max_length=24,
        description="Source regions kept for inspection without enumerating unsupported cell-level facts.")


def primary_window(reference):
    return reference.partition("@")[0]


def attach_table_cells(block, table, source):
    """Index only physical cells fully visible in this primary window or its cited context."""
    if table is None or block.get("gap"):
        return
    for segment in [block, *block["context"]]:
        cells = {}
        for cell in table.cells:
            start, end = cell.span.start, cell.span.end
            if not segment["start"] <= start < end <= segment["end"]:
                continue
            raw = source[start:end]
            value = raw.strip()
            if not value:
                continue
            start += len(raw) - len(raw.lstrip())
            cells[f"r{cell.row}c{cell.column}"] = (start, value, cell.rowspan, cell.colspan)
        if cells:
            segment["cells"] = cells


def windows(blocks):
    out = {}
    for block in blocks:
        key = block["block_id"]
        segments = [(key, block), *((f"{key}/context/{i}", c) for i, c in enumerate(block["context"]))]
        for window, segment in segments:
            out[window] = (segment["start"], segment["text"])
            for cell, (start, value, _, _) in segment.get("cells", {}).items():
                out[f"{window}@{cell}"] = (start, value)
    return out


def locate(citation, shown):
    if citation is None or citation.window not in shown:
        raise ValueError("citation_window_missing")
    offset, text = shown[citation.window]
    if citation.quote is None:
        if "@" not in citation.window or citation.occurrence is not None:
            raise ValueError("citation_quote_missing")
        return {"start": offset, "end": offset + len(text), "quote": text}
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


def _unique_fragment(context, literal):
    """Narrow an already located quote only when its literal fragment is unambiguous."""
    if not literal.strip():
        raise ValueError("empty_source_fragment")
    text = context["quote"]
    if literal == text:
        return context
    position = text.find(literal)
    if position < 0:
        raise ValueError("fragment_not_in_citation")
    if text.find(literal, position + 1) >= 0:
        raise ValueError("fragment_not_unique")
    end = position + len(literal)
    # An ASCII name/word must not be carved out of a longer identifier.
    if ((position and re.match(r"[A-Za-z_]", literal) and re.match(r"[A-Za-z0-9_]", text[position - 1]))
            or (end < len(text) and re.search(r"[A-Za-z_]$", literal)
                and re.match(r"[A-Za-z0-9_]", text[end]))):
        raise ValueError("partial_identifier")
    start = context["start"] + position
    return {"start": start, "end": start + len(literal), "quote": literal}


def _name_pattern(name):
    left = r"(?<![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", name) else ""
    right = r"(?![A-Za-z0-9_])" if re.search(r"[A-Za-z0-9_]$", name) else ""
    return re.compile(left + re.escape(name) + right)


def _name_span(context, name, window):
    span = _unique_fragment(context, name)
    offset, text = window
    if not _name_pattern(name).match(text, span["start"] - offset):
        raise ValueError("partial_identifier")
    return span


def mentions_name(span, name, window):
    offset, text = window
    index = span["start"] - offset
    end = span["end"] - offset
    match = _name_pattern(name).search(text, index, min(len(text), end + 1))
    return bool(match and match.end() <= end)


def _identity_role_cell(table, source, proposal, mention, proof):
    """A separate role header may identify a whole object cell on either axis."""
    if not table or not all("@" in c.window for c in (proposal.mention, proposal.qualification)):
        return False
    role, owner = _cell(table, proof), _cell(table, mention)
    if not role or not owner or source[role.span.start:role.span.end].strip() != proof["quote"]:
        return False
    if source[owner.span.start:owner.span.end].strip() != mention["quote"]:
        return False
    for axis, cross, depth, width in (("row", "column", "rowspan", "colspan"),
                                      ("column", "row", "colspan", "rowspan")):
        start, end = getattr(role, axis), getattr(owner, axis)
        if (start + getattr(role, depth) > end or getattr(role, cross) != getattr(owner, cross)
                or getattr(role, width) != getattr(owner, width) or not (role.is_header or start == 0)):
            continue
        # A repeated header starts a new panel, even with all cells serialized as <td>.
        if not any(start < getattr(c, axis) < end and getattr(c, cross) == getattr(role, cross)
                   and (c.is_header or source[c.span.start:c.span.end].strip() == proof["quote"])
                   for c in table.cells):
            return True
    return False


def entity_record(proposal, shown, block_scope, document, stage, *, table=None, source=""):
    context = locate(proposal.mention, shown)
    mention = _name_span(context, proposal.label, shown[proposal.mention.window])
    proof = locate(proposal.qualification, shown)
    role_evidence = _identity_role_cell(table, source, proposal, mention, proof)
    if ((not role_evidence and not mentions_name(proof, proposal.label, shown[proposal.qualification.window]))
            or proposal.scope and proposal.scope not in proof["quote"]):
        raise ValueError("invalid_identity_evidence")
    if mention != context and not role_evidence and not _contained(mention, proof):
        raise ValueError("recovered_mention_outside_identity_proof")
    aliases = []
    if len(proposal.alias_names) != len(proposal.aliases):
        raise ValueError("missing_alias_evidence")
    for name, citation in zip(proposal.alias_names, proposal.aliases):
        span = locate(citation, shown)
        if (not name.strip() or name == proposal.label
                or not all(mentions_name(span, x, shown[citation.window]) for x in [name, proposal.label])):
            raise ValueError("alias_not_grounded")
        aliases.append({"name": name, "evidence": span})
    key = digest([document, mention["start"], mention["end"], block_scope, proposal.scope])[:24]
    return {"object_id": "entity-" + key, "label": proposal.label, "scope": proposal.scope or block_scope,
            "section": block_scope,
            "qualifier": proposal.scope, "kind": proposal.kind,
            "evidence": [mention, proof], "mention_context": context, "aliases": aliases,
            "stage": stage, "basis": "model_proposed"}


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
            if _name_pattern(name).search(text):
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


_REVIEW_LANGUAGE = re.compile(
    r"\b(?:not|never|neither|hypothetical(?:ly)?|would|might|unmeasured|"
    r"unsupported|uncertain|rather than|instead of)\b|n['’]t\b|"
    r"并非|而非|不是|并未|未达到|未观测|未测得|未测量|未报告|未经证实|不能作为|不代表|假设",
    re.IGNORECASE,
)


def _named_property(definition, prop, fit):
    """A literal property label, optionally followed by instructions in the task.

    Never match arbitrary substrings of a long definition: they may name exclusions.
    Distinct local fit evidence may supply a modifier omitted from the property cue.
    """
    parts = re.split(r"[:：;；,，。\n]|\.\s|\.$", definition.strip(), maxsplit=1)
    label = parts[0].strip().casefold()
    label = re.sub(r"^(?:the|a|an)\s+", "", label)
    if not label or not prop or not fit:
        return False
    if len(parts) == 2 and (tail := parts[1].strip(" .。").casefold()):
        # A heading can be followed by material constraints. Only ignore a simple
        # presentation instruction; any other tail needs literal support itself.
        formatting = re.fullmatch(
            r"(?:preserve|retain|keep|record|include) (?:the )?(?:(?:reported|original|source) )?"
            r"(?:units?|wording|precision|evidence|conditions?)", tail)
        if not formatting and tail not in fit["quote"].casefold():
            return False
    if label == prop["quote"].strip().casefold():
        return True
    # The property cue, named field and separately supplied fit wording must agree.
    # Word boundaries avoid matching "load" inside "unload".
    before = r"(?<![a-z0-9_])" if label[0].isascii() and label[0].isalnum() else ""
    after = r"(?![a-z0-9_])" if label[-1].isascii() and label[-1].isalnum() else ""
    return (prop["quote"].strip().casefold() in label
            and bool(re.search(before + re.escape(label) + after, fit["quote"].casefold())))


def _potential_denial(text, definition):
    # Affirmative/excluded modifier pairs have a narrow, checkable exception.
    # Else retain the caution: implicit/multilingual semantics require review.
    def contrast(match):
        yes, no = match.group(1).casefold(), match.group(2).casefold()
        words = set(re.findall(r"[a-z]+", definition.casefold()))
        return yes if yes in words and no not in words else match.group()

    text = re.sub(r"\b([a-z]{3,}),\s*not\s+([a-z]{3,})(?=[.!;]|$)", contrast, text, flags=re.I)
    return _REVIEW_LANGUAGE.search(text) is not None


def _assertion_text(source, block, link):
    """Include a source qualification that the model may have cropped off its quote."""
    before = source[block["start"]:link["start"]]
    after = source[link["end"]:block["end"]]
    boundary = r"[。！？;；!?\n]|\.\s+"
    prefix = re.split(boundary, before)[-1]
    suffix = re.split(boundary, after, maxsplit=1)[0] if not re.search(r"[.!?。！？;；]\s*$", link["quote"]) else ""
    return prefix + link["quote"] + suffix


def _attested_unit(unit, spans, source):
    """A unit cannot be a prefix, suffix or factor of the source unit."""
    if not unit:
        return True
    alphabet = r"[A-Za-zµμΩ°%‰]"
    for span in spans:
        if span is None:
            continue
        for match in re.finditer(re.escape(unit), span["quote"]):
            start = span["start"] + match.start()
            end = span["start"] + match.end()
            if start and re.fullmatch(alphabet, source[start-1]):
                continue
            if end < len(source) and (re.fullmatch(alphabet, source[end]) or source[end].isnumeric()):
                continue
            prefix = source[max(0, start - 80):start].rstrip(" \t")
            suffix = source[end:].lstrip(" \t")
            if (suffix.startswith(("/", "·", "⋅", "^", "⁻")) or prefix.endswith(("·", "⋅"))
                    or re.search(r"[A-Za-zµμΩ°%‰]+\s*/$", prefix)):
                continue
            return True
    return False


def fact_record(fact, shown, block, source, document, fields, choices, table):
    if fact.field not in fields:
        raise ValueError("undeclared_field")
    if primary_window(fact.value_source.window) != block["block_id"]:
        raise ValueError("value_not_locally_attested")
    value_context = locate(fact.value_source, shown)
    value = _unique_fragment(value_context, fact.value)
    evidence = [value]
    if value_context != value:
        evidence.append(value_context)
    evidence_issues = []

    def support(citation, role):
        if citation is None:
            return None
        try:
            span = locate(citation, shown)
        except ValueError as exc:
            evidence_issues.append({"role": role, "reason": str(exc)})
            return None
        if span not in evidence:
            evidence.append(span)
        return span

    prop = support(fact.property_source, "property")
    # Validate numeric boundaries against the source around the quote, including signs.
    tokens = numeric_tokens(fact.value)
    if tokens:
        left = max(block["start"], value["start"] - 64)
        text = source[left:min(block["end"], value["end"] + 64)]
        offset = value["start"] - left
        source_tokens = set(numeric_tokens(text))
        if any((offset + start, offset + end) not in source_tokens for start, end in tokens):
            raise ValueError("numeric_substring")
    obj = choices.get(fact.owner)
    owner = support(fact.owner_source, "owner")
    link = support(fact.link_source, "link")
    fit_span = support(fact.fit_source, "fit")
    qualifications = [span for c in fact.qualifier_sources if (span := support(c, "qualifier"))]
    names = [obj["label"], *(a["name"] for a in obj["aliases"])] if obj else []
    owner_valid = False
    if owner and names:
        candidates = ([owner["quote"]] if owner["quote"] in names
                      else [name for name in names if _name_pattern(name).search(owner["quote"])])
        if len(candidates) == 1:
            try:
                owner = _name_span(owner, candidates[0], shown[fact.owner_source.window])
                owner_valid = True
                if owner not in evidence:
                    evidence.append(owner)
            except ValueError as exc:
                evidence_issues.append({"role": "owner", "reason": str(exc)})
    owner_ok = bool(obj and prop and owner_valid and link)
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
                        for term in (fact.statistic, fact.conditions))
    if block["kind"] == "table":
        unit_spans = [prop, fit_span, *qualifications]
        if table and _cell(table, value_context) == _cell(table, value):
            unit_spans.append(value_context)
    else:
        unit_spans = [local, *qualifications]
    if not _attested_unit(fact.unit, unit_spans, source):
        evidence_issues.append({"role": "unit", "reason": "unit_not_locally_attested"})
    if prop is None:
        local_fit = False
    elif block["kind"] == "table":
        local_fit = (owner is not None and fit_span is not None and
                     all(_table_qualifier(table, source, s, owner, value, prop) for s in [fit_span, *qualifications]))
    else:
        # For unknown owners the clause can still establish the measured property.
        anchor = local or fit_span
        local_fit = bool(anchor and fit_span and _one_statement(anchor)
                         and all(_contained(s, anchor) for s in [prop, value, fit_span, *qualifications]))
    fit_ok = bool(fact.fit == "supported" and local_fit and qualifiers_ok and not evidence_issues)
    # A recovered coordinate supplies no new scientific evidence about the field.
    # Only a literal task property label attested in the source supplies an independent
    # deterministic check on this path. Paraphrases remain useful review details.
    expanded = value != value_context
    if expanded and fit_ok and not _named_property(fields[fact.field], prop, fit_span):
        fit_ok = False
        evidence_issues.append({"role": "fit", "reason": "recovered_value_requires_field_review"})
    if fact.fit == "supported":
        statements = [e["quote"] for e in [link, fit_span, value_context] if e]
        if block["kind"] != "table" and link is not None and block["start"] <= link["start"] < link["end"] <= block["end"]:
            statements.append(_assertion_text(source, block, link))
        if any(_potential_denial(text, fields[fact.field]) for text in [fact.reason, *statements]):
            fit_ok = False
            evidence_issues.append({"role": "fit", "reason": "assertion_or_rationale_requires_review"})
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
                                prop["start"] if prop else None, prop["end"] if prop else None])[:24]
    component = None
    if len(tokens) == 1:
        start, end = tokens[0]
        component = {"start": value["start"] + start, "end": value["start"] + end, "quote": fact.value[start:end]}
    return {"observation_id": obs_id, "field": fact.field, "value": fact.value, "numeric_component": component,
            "model_claim": fact.model_dump(mode="json"), "raw_owner_ref": fact.owner,
            "grounding": {"method": "unique_subspan" if expanded else "exact", "context": value_context},
            "evidence_issues": evidence_issues,
            "entity": owner["quote"] if owner else None, "unit": fact.unit, "statistic": fact.statistic,
            "conditions": fact.conditions, "purpose": fact.purpose, "block_id": block["block_id"],
            "source_kind": block["kind"], "evidence": evidence, "owner_id": obj["object_id"] if assignable else None,
            "value_source": value, "owner_source": owner, "property_source": prop,
            "qualified": bool(fit_ok and assignable), "field_fit": fact.fit, "property_name": prop["quote"] if prop else None,
            "reason": reason, "model_reason": fact.reason, "owner_supported": assignable, "field_supported": fit_ok}
