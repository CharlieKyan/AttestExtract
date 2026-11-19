"""Assess claims within the original, visible cells of a single source table."""

import re

from .dataset_resolution_table import _covers
from .entity_first_protocol import (
    Citation, _attested_unit, _cell, _potential_denial, _table_qualifier, locate, table_aligned,
)
from .execution import digest


class _Grid:
    def __init__(self, source, table, packet):
        self.source = source
        self.table = table
        self.shown = {}
        self.primary = set()
        for window in packet["windows"]:
            begin, text = window["start"], window["text"]
            if source[begin:begin + len(text)] != text:
                continue
            wid = window["id"]
            if window.get("role") == "primary":
                self.primary.add(wid)
            self.shown[wid] = (begin, text)
            for cell in table.cells:
                start, end = cell.span.start, cell.span.end
                key = f"r{cell.row}c{cell.column}"
                if not begin <= start < end <= begin + len(text):
                    continue
                raw = source[start:end]
                if raw.strip() and raw.strip() == window.get("cells", {}).get(key):
                    self.shown[f"{wid}@{key}"] = (start + len(raw) - len(raw.lstrip()), raw.strip())

    def span(self, citation):
        if not citation:
            return None
        try:
            span = locate(Citation.model_validate(citation), self.shown)
        except ValueError:
            return None
        return span if _cell(self.table, span) is not None else None

    def text(self, cell):
        return self.source[cell.span.start:cell.span.end].strip()

    def whole(self, cell):
        raw = self.source[cell.span.start:cell.span.end]
        start = cell.span.start + len(raw) - len(raw.lstrip())
        return {"start": start, "end": start + len(raw.strip()), "quote": raw.strip()}


def _label_in_definition(label, definition):
    # Only the leading description can attest a role. Later sentences often name exclusions.
    description = re.split(r"[.;:。；：\n]", definition, maxsplit=1)[0]
    if not label or _potential_denial(description, definition):
        return False
    left = r"(?<![A-Za-z\d_])" if re.match(r"[A-Za-z\d_]", label) else ""
    right = r"(?![A-Za-z\d_])" if re.search(r"[A-Za-z\d_]$", label) else ""
    return re.search(left + re.escape(label) + right, description, flags=re.I) is not None


def _property_named(header, definition, unit=None):
    """Require a positive property cue rather than a suffix of a more specific property.

    This deliberately recognizes only simple task wordings. Ambiguous paraphrases
    remain accessible in the proposal view.
    """
    name = header.strip()
    if unit:
        for suffix in ("/" + unit, "(" + unit + ")"):
            if name.endswith(suffix):
                name = name.removesuffix(suffix).strip()
                break
    if not _label_in_definition(name, definition):
        return False
    wording = re.split(r"[,.;:，。；：\n]", definition, maxsplit=1)[0].strip()
    if re.search(r"[\u3400-\u9fff]", name):
        return bool(re.search(r"(?:^|的(?:各类|各项|各种)?|、)" + re.escape(name) + "$", wording))
    return bool(re.match(r"^(?:(?:the|measured|reported)\s+)*" + re.escape(name)
                         + r"(?:\s+(?:of|for|in|at)\b|$)", wording, re.I))


def _axis(role, owner):
    """The declared identity header must end before the object along exactly one axis."""
    if role.column == owner.column and role.colspan == owner.colspan and role.row + role.rowspan <= owner.row:
        return "row"
    if role.row == owner.row and role.rowspan == owner.rowspan and role.column + role.colspan <= owner.column:
        return "column"
    return None


def _in_header_band(role, header, value, axis):
    # The extent of the separate identity header witnesses the depth of the local header
    # band, even when a converter serializes every header as a data cell.
    cross, width = ("column", "colspan") if axis == "row" else ("row", "rowspan")
    depth = "rowspan" if axis == "row" else "colspan"
    return (_covers(role, header, axis, depth) and _covers(header, value, cross, width)
            and getattr(header, axis) + getattr(header, depth) <= getattr(value, axis))


def _identity_claim(grid, answer, key, label_field, owner, role):
    """Look for an independently declared identity field at this precise name/role pair."""
    return any(f.get("field") == label_field and f.get("owner") == key
               and f.get("fit") == "supported" and f.get("direct") is True
               and f.get("value") == owner["quote"]
               and grid.span(f.get("value_source")) == owner
               and grid.span(f.get("property_source")) == role for f in answer["facts"])


def _decision(status, reason, spans=(), **values):
    return {"status": status, "reason": reason, "evidence": list(spans), **values}


def _group_parameter(grid, role, owner, prop, value):
    """A group descriptor and its adjacent parameter occur in the same repeated key/value band."""
    if not (role.row == owner.row == prop.row == value.row and
            role.column + 1 == owner.column and owner.column + 1 == prop.column
            and prop.column + 1 == value.column):
        return False
    above = {c.column: c for c in grid.table.cells if c.row + c.rowspan == owner.row and c.colspan == 1}
    if not all(c.column in above for c in (role, owner, prop, value)):
        return False
    titles = [grid.text(above[c.column]) for c in (role, owner, prop, value)]
    competing_roles = [c for c in grid.table.cells if grid.text(c) == grid.text(role)]
    return (len(competing_roles) == 1 and all(titles)
            and titles[:2] == titles[2:] and titles[0] != titles[1])


def _captions(table, source):
    spans = [{"start": s.start, "end": s.end, "quote": source[s.start:s.end]} for s in table.captions]
    # Converters often put the caption before the HTML instead of inside <caption>.
    begin = max(0, table.span.start - 600)
    for match in reversed(list(re.finditer(r"[^\r\n]+", source[begin:table.span.start]))):
        line = match.group()
        if not line.strip():
            continue
        if not re.match(r"^\s*(?:table|表)\s*\d", line, re.I):
            break
        spans.append({"start": begin + match.start(), "end": begin + match.end(), "quote": line})
    return spans


def _group_property(header, unit, definition, titles):
    if _property_named(header, definition, unit):
        return True
    # A documented compound heading can separately name the caption subject and
    # the requested property (e.g. an unsegmented Chinese identifier). Require
    # both complete pieces, not a character overlap or a similarity threshold.
    name = header.removesuffix("/" + unit).strip() if unit else header
    wording = re.split(r"[，。；：]", definition, maxsplit=1)[0]
    if not re.fullmatch(r"[\u3400-\u9fff]{4,}", name) or _potential_denial(wording, definition):
        return False
    return any(name[:i] in titles and wording.endswith(name[i:]) for i in range(2, len(name) - 1))


def _scope_guard(table, source, fact):
    if fact.get("purpose") in {"example", "aggregate", "classification_threshold"}:
        return "claim_has_limited_scope"
    titles = " ".join(c["quote"] for c in _captions(table, source))
    if re.search(r"\b(?:classification|thresholds?|cutoffs?|acceptance limits?)\b|阈值|分类标准|分级标准", titles, re.I):
        return "classification_scope_requires_review"
    if fact.get("purpose") == "measurement" and re.search(
            r"\b(?:predicted|simulated|hypothetical|model inputs?)\b|模拟值|预测值|模型(?:输入|设定)", titles, re.I):
        return "measurement_scope_requires_review"
    if table.footnotes:
        return "table_footnote_scope_requires_review"
    return None


def _compound_parameter(grid, packet, fact, cards, prop, value, definition):
    """An adjacent key/value pair can spell out both a named object and its property."""
    h, v = _cell(grid.table, prop), _cell(grid.table, value)
    if (len(cards) != 1 or not definition or not fact.get("direct") or fact.get("purpose") != "model_input"
            or h.row != v.row or h.column + 1 != v.column or h.rowspan != 1 or v.rowspan != 1):
        return None
    name = cards[0]["label"]
    if not prop["quote"].startswith(name):
        return None
    remainder = prop["quote"][len(name):]
    if not remainder or (name[-1:].isascii() and remainder[0].isalnum()):
        return None
    tail = remainder.lstrip(" :：")
    if not _property_named(tail, definition, fact.get("unit")) or any(c["label"] != name and c["label"] in tail
                                                   for c in packet["objects"]):
        return None
    location = prop["start"] + len(prop["quote"]) - len(tail)
    return ({"start": prop["start"], "end": prop["start"] + len(name), "quote": name},
            {"start": location, "end": prop["end"], "quote": tail})


def _field_assessment(grid, fact, fields, owner, prop, value, identity, axis, role, aligned):
    guard = _scope_guard(grid.table, grid.source, fact)
    if guard or fact.get("fit") == "unsupported":
        return _decision("contradicted" if fact.get("fit") == "unsupported" else "unknown",
                         guard or "model_rejects_field", [prop])
    unknown = _decision("unknown", "table_field_evidence_incomplete")
    definition = fields.get(fact.get("field"))
    qualifiers = [grid.span(ref) for ref in fact.get("qualifier_sources", [])]
    checked = [grid.span(fact.get("fit_source")), *qualifiers]
    if not definition or fact.get("fit") != "supported" or not all(checked):
        return unknown
    table, source = grid.table, grid.source
    v, h = _cell(table, value), _cell(table, prop)
    nested = bool(axis and aligned and _in_header_band(role, h, v, axis))
    for span in checked:
        if span in (prop, value) or _table_qualifier(table, source, span, owner, value, prop):
            continue
        if not (nested and _in_header_band(role, _cell(table, span), v, axis)
                and table_aligned(table, source, owner, value, span)):
            return unknown
    cues = [value, prop, *checked]
    terms = [t for t in (fact.get("statistic"), fact.get("conditions"), fact.get("unit")) if t]
    if nested:
        # A leaf heading can change the measured statistic. If it is absent from
        # the claim, the numeric cell is grounded but the projected fact is underspecified.
        for cell in table.cells:
            if (getattr(cell, axis) > getattr(h, axis) and _in_header_band(role, cell, v, axis)
                    and grid.text(cell) and grid.text(cell) not in terms):
                return unknown
    for term in (fact.get("conditions"), fact.get("statistic")):
        if term and not any(term in s["quote"] for s in cues):
            return unknown
    if not fact.get("unit") and re.search(r"/\s*\S|\([^()]+\)\s*$", grid.text(h)):
        return unknown
    unit = fact.get("unit")
    # In a labelled header such as "Length/mm", '/' separates label and unit.
    # Compare the *whole* suffix, so "m" cannot pass for "mm" or "kg/m".
    heading, slash, suffix = grid.text(h).partition("/")
    unit_ok = suffix.strip() == unit if slash and heading.strip() else _attested_unit(unit, cues, source)
    if (not unit_ok
            or not (identity or _property_named(grid.text(h), definition, unit))
            or any(_potential_denial(s, definition) for s in [fact.get("reason", ""), grid.text(h)])):
        return unknown
    return _decision("supported", "verified_local_header_chain", cues)


def assess_table(source, table, packet, answer, fact, report, prior, label_field):
    """Return independent local/target/fit decisions; empty output defers to the frozen record."""
    if table is None or not table.cells:
        return {}
    grid = _Grid(source, table, packet)
    value_ref = fact.get("value_source") or {}
    if value_ref.get("window", "").partition("@")[0] not in grid.primary:
        return {}
    value = grid.span(value_ref)
    prop = grid.span(fact.get("property_source"))
    owner = grid.span(fact.get("owner_source"))
    if not (value and prop and owner):
        return {}
    v, h, a = (_cell(table, x) for x in (value, prop, owner))
    prop = grid.whole(h)
    # A short model quote inside a cell cannot turn 117 into 17 or U70 into U7.
    if grid.text(v) != fact.get("value") or value["quote"] != fact["value"] or owner["quote"] != grid.text(a):
        return {}
    role = None
    key = fact.get("owner")
    nominations = [n for n in answer["nominations"] if n.get("key") == key]
    cards = [c for c in packet.get("objects", []) if c.get("object_id") == key]
    nomination = nominations[0] if len(nominations) == 1 and not cards else None
    if nomination and grid.span(nomination.get("mention")) == owner:
        role = grid.span(nomination.get("qualification"))
    fields = packet["task"]["fields"]
    role_valid = (role is not None and role["quote"] == grid.text(_cell(table, role)) and label_field in fields
                  and _label_in_definition(role["quote"], fields[label_field])
                  and _identity_claim(grid, answer, key, label_field, owner, role))
    role_cell = _cell(table, role) if role_valid else None
    axis = _axis(role_cell, a) if role_cell else None
    identity = fact.get("field") == label_field and owner == value and role_valid and prop == role
    aligned = table_aligned(table, source, owner, value, prop)
    if axis and aligned and not _in_header_band(role_cell, h, v, axis):
        # The proposed property cannot supply the local header role, e.g. a label
        # in another vertical panel with otherwise coinciding row/column coordinates.
        aligned = False
    grouped = bool(nomination and nomination.get("kind") == "group" and role_valid)
    captions = _captions(table, source) if grouped else []
    group_pair = (grouped and fact.get("fit") != "unsupported"
                  and fact.get("purpose") in {"measurement", "model_input", "aggregate"}
                  and _group_parameter(grid, role_cell, a, h, v)
                  and _group_property(prop["quote"], fact.get("unit"), fields.get(fact.get("field"), ""),
                                      " ".join(c["quote"] for c in captions)))
    link = grid.span(fact.get("link_source"))
    compound = (_compound_parameter(grid, packet, fact, cards, prop, value, fields.get(fact.get("field")))
                if owner == link == prop and not nominations else None)
    # A declared assignment and the actual local path must agree. A group key/value
    # pair explicitly describes the collection even when the model declined a member assignment.
    direct_ok = ((aligned or identity) and fact.get("direct") is True and link in (owner, role, prop)
                 and link is not None) or group_pair or compound is not None
    if not direct_ok:
        return {}
    if compound:
        owner, prop = compound
    label = owner["quote"]
    oid = "local-table-" + digest([report["source_sha256"], table.table_id, a.cell_id])[:24]
    scope = table.table_id
    evidence = list({(s["start"], s["end"]): s for s in [value, prop, owner, role, link] if s}.values())
    if group_pair:
        evidence.extend(captions)
    direct = _decision("supported", "group_parameter_record" if group_pair else "local_table_path",
                       evidence, label=label, id=oid, scope=scope)
    target = _decision("unknown", "table_subject_identity_pending", [owner], label=None, id=None, scope=scope)
    if grouped or re.fullmatch(r"(?:grand\s+)?total|subtotal|合计|小计|总计", label.strip(), re.I):
        target["reason"] = "collection_observation_not_distributed"
    elif prior and prior.get("target", {}).get("status") == "supported" and prior["target"]["label"] == label:
        target = prior["target"]
        direct["id"] = target["id"]
    elif (axis and nomination.get("kind") in {"instance", "model"} and nomination.get("label") == label
          and not nomination.get("scope")
          and not any(n["key"] == key and n["block_id"] == value_ref["window"].partition("@")[0]
                      and n["status"] in {"uncertain", "not_target"} for n in report.get("nominations", []))):
        target = _decision("supported", "declared_identity_in_role_cell", [owner, role], label=label, id=oid, scope=scope)
    fit = _field_assessment(grid, fact, fields, owner, prop, value, identity, axis, role_cell, aligned)
    evidence.extend(s for s in fit["evidence"] if s not in evidence)
    result = {"direct": direct, "target": target, "fit": fit, "value_source": value, "property_source": prop,
              "source_property": prop["quote"], "evidence": evidence,
              "record_group": [table.table_id, a.cell_id, fact.get("purpose")]}
    if grouped:
        result["members"] = [p.strip() for p in re.split(r"\s*[,、;；]\s*", label) if p.strip()]
    return result
