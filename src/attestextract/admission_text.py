"""Resolve prose admission decisions against the exact window and cards of a frozen call."""

import re

from .entity_first_protocol import (
    Citation, Fact, _assertion_text, _attested_unit, _contained, _name_pattern, _one_statement, _potential_denial,
    _unique_fragment, fact_record, locate,
)
from .measurement_syntax import numeric_tokens


def _span(source, start, end):
    return {"start": start, "end": end, "quote": source[start:end]}


def _citation(window, span, shown):
    """Make a checked, zero-based occurrence, counting even overlapping literals."""
    offset, text = shown[window]
    pos = span["start"] - offset
    literal = span["quote"]
    assert literal and text[pos:pos + len(literal)] == literal
    occurrence = sum(1 for _ in re.finditer("(?=" + re.escape(literal) + ")", text[:pos + len(literal) - 1]))
    return Citation(window=window, quote=literal, occurrence=occurrence)


def _locate(citation, shown, source, anchor=None):
    if citation is None or citation.window not in shown:
        return None
    try:
        return locate(citation, shown)
    except ValueError:
        pass
    if citation.occurrence is not None or not citation.quote:
        return None
    start, text = shown[citation.window]
    inside = anchor and start <= anchor["start"] < anchor["end"] <= start + len(text)
    if inside:
        try:
            return _unique_fragment(anchor, citation.quote)
        except ValueError:
            pass
    # Markdown escaping of a range separator between numbers (possibly percentages).
    # Only compare spelling. Require a unique match and return its verbatim source.
    parts = re.split(r"(?<=[\d%])\\?~(?=\d)", citation.quote)
    if len(parts) > 1:
        pattern = re.compile(r"\\?~".join(re.escape(p) for p in parts))
        ranges = [(0, len(text))]
        if inside:
            ranges.insert(0, (anchor["start"] - start, anchor["end"] - start))
        for lo, hi in ranges:
            matches = list(pattern.finditer(text, lo, hi))
            if len(matches) == 1:
                m = matches[0]
                return _span(source, start + m.start(), start + m.end())
    return None


def _math_key(expression):
    """Compare complete printed quantities for review. Keep powers, signs and operators."""
    expression = re.sub(r"\\mathrm\s*\{\s*([A-Za-z]+)\s*\}", r"\1", expression)
    expression = re.sub(r"\\times\b", "×", expression)
    expression = re.sub(r"\\mu\b", "μ", expression)
    expression = re.sub(r"[ \t]", "", expression)
    expression = re.sub(r"\^\{([+-]?\d+)\}", r"^\1", expression)
    expression = re.sub(r"\{([A-Za-zμ]+)\}", r"\1", expression)
    # In math mode ~ before a unit is a typesetting space.
    return re.sub(r"(?<=[0-9])~(?=[A-Za-zμ])", "", expression)


def _value_span(context, value, source):
    if not context:
        return None, False
    rendered = False
    try:
        span = _unique_fragment(context, value)
    except ValueError:
        # A normalized coordinate is useful provenance. It is never sufficient
        # for a new formal measurement. Do not equate fragments of TeX formulae.
        local = Citation(window="context", quote=value)
        span = _locate(local, {"context": (context["start"], context["quote"])}, source)
        if not span and numeric_tokens(value):
            matches = [m for m in re.finditer(r"\$([^$\r\n]+)\$", context["quote"])
                       if _math_key(m[1]) == _math_key(value)]
            if len(matches) == 1:
                m = matches[0]
                return _span(source, context["start"] + m.start(), context["start"] + m.end()), True
        if not span:
            return None, False
        rendered = True
    # Exact matches still require full numeric token boundaries in the original.
    offset = max(0, span["start"] - 64)
    nearby = source[offset:span["end"] + 64]
    tokens = set(numeric_tokens(nearby))
    if any((span["start"] - offset + a, span["start"] - offset + b) not in tokens
           for a, b in numeric_tokens(span["quote"])):
        return None, False
    return span, rendered


def _decision(status, reason, spans=(), label=None, object_id=None, scope=None):
    return {"status": status, "reason": reason, "basis": "source_checked",
            "label": label, "id": object_id, "scope": scope,
            "evidence": [s for s in spans if s is not None]}


def _unit_scope_unclear(source, claim, value, link, prop):
    if not (claim.unit and value and link and numeric_tokens(value["quote"]) and _contained(value, link)):
        return False
    others = [(a, b) for a, b in numeric_tokens(link["quote"])
              if link["start"] + a < value["start"] or link["start"] + b > value["end"]]
    if not others:
        return False
    # A unit elsewhere in a multi-measurement assertion may describe a different
    # number. Check the reported value and its immediate unit or a unit-bearing header.
    tail = source[value["end"]:link["end"]]
    space = len(tail) - len(tail.lstrip(" \t"))
    near = _span(source, value["start"], min(link["end"], value["end"] + space + len(claim.unit)))
    return not _attested_unit(claim.unit, [near, prop if prop and not numeric_tokens(prop["quote"]) else None], source)


def _described_property(definition, context):
    """Read an explicitly named classification axis and match it to a field heading."""
    leading = re.split(r"[。；;,.，]", definition.strip().casefold(), maxsplit=1)[0]
    english = re.search(r"\b(?:grouped|classified) by ([a-z ]{2,40})\s*[,;:]", context.casefold())
    if english:
        axis = re.escape(english[1].strip())
        return bool(re.fullmatch(r"(?:the )?" + axis + r"(?: and [a-z ]+)?", leading))
    named = re.search(r"(?:根据|依据|按照)([\u3400-\u9fff]{2,10}?)(?:特点|特征)(?:将|把|分为)", context)
    if named:
        axis = re.escape(named[1])
        return bool(re.match(r"(?:[\u3400-\u9fff]{1,8}的)?(?:主要|实际)?" + axis + r"(?:[和及、]|$)", leading))
    return False


_CAUTION = re.compile(r"\b(?:if|unless|assum\w*|example|combined|together|either|or|versus)\b"
                      r"|假定|假如|如果|例如|合计|对照", re.I)

# These explicit qualifications can change the interpretation of a proposed
# measurement. Keep the quoted observation available for review.
_NON_MEASUREMENT = re.compile(
    r"\b(?:if|unless|assum(?:e|ed|ing)|predicted|simulated|hypothetical|cutoff|threshold|"
    r"combined|together|model input|model setting)\b|如果|假设|假定|预测|模拟值|设定值|阈值|合计", re.I)


def _identifier_subject(source, block, claim, owner, prop, value, link):
    """Accept an explicit, single subject–property–value statement without a roster."""
    if not owner or not claim.owner_source or owner["quote"] != claim.owner_source.quote:
        return False
    label = owner["quote"]
    numbered = r"(?:[A-Za-z][A-Za-z-]*\s+|[\u3400-\u9fff]{1,8})?[A-Za-z]{0,8}[-#]?\d+[A-Za-z]?"
    if not re.fullmatch(numbered, label) or not re.search(r"[A-Za-z\u3400-\u9fff]", label):
        return False
    if not (_one_statement(link) and all(_contained(s, link) for s in (owner, prop, value))
            and link["start"] == owner["start"] < owner["end"] <= prop["start"] < prop["end"] <= value["start"]):
        return False
    statement = _assertion_text(source, block, link)
    if _potential_denial(statement, "") or _potential_denial(claim.reason, "") or _CAUTION.search(statement):
        return False
    # Check the complete statement, including text the model might have cropped.
    if statement.strip().rstrip(".。") != link["quote"].strip().rstrip(".。"):
        return False
    before = source[owner["end"]:prop["start"]].strip().casefold()
    between = source[prop["end"]:value["start"]].strip().casefold()
    after = source[value["end"]:link["end"]].strip().rstrip(".。")
    return (before in {"", "'s", "’s", "的"} and between in {"", "=", ":", "：", "was", "is", "reached", "为"}
            and after in {"", claim.unit})


def _reaffirmed_alias(source, primary, obj, name, catalog):
    """A recorded alias can transfer when its definition is reaffirmed in this scope."""
    if obj.get("qualifier") and obj["qualifier"] not in primary["text"]:
        return None
    aliases = {a["name"]: a["evidence"] for a in obj["aliases"]}
    if name not in aliases or any(name in {other["label"], *(a["name"] for a in other["aliases"])}
                                  for other in catalog.values() if other["object_id"] != obj["object_id"]):
        return None
    marker = r"hereafter(?: called)?|also called|abbreviated as|identifier|编号(?:为)?|简称(?:为)?|以下简称|下称"
    declaration = re.compile(_name_pattern(obj["label"]).pattern + r"\s*[(（,，]\s*(?i:" + marker + r")\s*[:：]?\s*"
                             + _name_pattern(name).pattern + r"(?=\s*[)）,，.。;；\n]|$)")
    old = aliases[name]
    if not declaration.search(old["quote"]) or _potential_denial(old["quote"], ""):
        return None
    matches = list(declaration.finditer(primary["text"]))
    if len(matches) == 1:
        anchor = _span(source, primary["start"] + matches[0].start(), primary["start"] + matches[0].end())
        if obj.get("qualifier") and obj["qualifier"] not in anchor["quote"]:
            return None
        statement = _assertion_text(source, {"start": primary["start"], "end": primary["start"] + len(primary["text"])}, anchor)
        if not (_potential_denial(statement, "") or _CAUTION.search(statement)):
            return anchor
    return None


def assess_text(source, packet, answer, fact, report, prior, label_field):
    """Judge the local statement separately from roster identity and field suitability."""
    try:
        claim = Fact.model_validate(fact)
    except ValueError:
        return {}
    primary = next((w for w in packet["windows"] if w["role"] == "primary" and w["id"] == claim.value_source.window), None)
    if not primary or primary["kind"] != "text":
        return {}
    shown = {w["id"]: (w["start"], w["text"]) for w in packet["windows"]
             if source[w["start"]:w["start"] + len(w["text"])] == w["text"]}
    wid = primary["id"]
    if wid not in shown:
        return {}
    link = _locate(claim.link_source, shown, source)
    value_context = _locate(claim.value_source, shown, source, link)
    value, rendered = _value_span(value_context, claim.value, source)
    prop = _locate(claim.property_source, shown, source, link)
    unit_scope_unclear = _unit_scope_unclear(source, claim, value, link, prop)
    owner = _locate(claim.owner_source, shown, source, link)
    fit_span = _locate(claim.fit_source, shown, source, link)
    definition = packet["task"]["fields"].get(claim.field, "")
    spans = []
    for s in (value, value_context, prop, owner, link, fit_span):
        if s and s not in spans:
            spans.append(s)
    fit = _decision("contradicted" if claim.fit == "unsupported" else "unknown",
                    "model_reports_field_mismatch" if claim.fit == "unsupported" else "field_evidence_unconfirmed",
                    [prop, fit_span])
    out = {"direct": _decision("unknown", "local_assignment_unconfirmed", [owner, link]),
           "target": _decision("unknown", "document_identity_unconfirmed"), "fit": fit,
           "value_source": value, "property_source": prop, "source_property": prop["quote"] if prop else None,
           "evidence": spans, "record_group": [wid, owner, claim.purpose], "local_mention": owner}
    if prior and prior.get("reason") == "conflicting_assignments":
        return out
    if not definition or not value or rendered or not link or not prop:
        return out
    block = {"block_id": wid, "scope": primary["scope"], "kind": "text",
             "start": primary["start"], "end": primary["start"] + len(primary["text"])}

    # Build the original competitors before selecting a referent. A temporary
    # nomination can identify a local mention even without approved row identity.
    choices = {}
    nominations = {}
    catalog = {obj["object_id"]: obj for obj in report["objects"]}
    for obj in packet["objects"]:
        saved = catalog.get(obj["object_id"], {})
        choices[obj["object_id"]] = dict(obj, kind=saved.get("kind", "other"),
                                         qualifier=saved.get("qualifier"),
                                         aliases=[{"name": a} for a in obj.get("aliases", [])])
    for obj in answer.get("nominations", []):
        key = obj["key"]
        if key in choices or key in nominations:
            nominations[key] = None
            continue
        nominations[key] = obj
    if claim.owner in nominations and not nominations[claim.owner]:
        return out
    for key, obj in nominations.items():
        if obj:
            choices[key] = {"label": obj["label"], "object_id": key, "kind": obj.get("kind", "other"),
                            "aliases": []}
    selected = choices.get(claim.owner)
    if selected:
        choices[claim.owner] = {**selected, "section": primary["scope"]}
    standalone = (claim.owner is None and claim.purpose not in {"example", "aggregate", "classification_threshold"}
                  and _identifier_subject(source, block, claim, owner, prop, value, link))
    if standalone:
        # Homonyms concern catalog identity. This narrow statement establishes
        # only its one local mention, without selecting any of those objects.
        choices = {k: v for k, v in choices.items() if v["label"] != owner["quote"]}
        choices[None] = {"object_id": None, "label": owner["quote"], "kind": "other",
                         "aliases": [], "section": primary["scope"]}
    adjusted = {}
    if standalone:
        adjusted["direct"] = True
    for name, span in (("value_source", value_context), ("owner_source", owner), ("link_source", link),
                       ("property_source", prop), ("fit_source", fit_span)):
        citation = getattr(claim, name)
        if span and citation:
            adjusted[name] = _citation(citation.window, span, shown)

    # A descriptive clause can be quoted inside a longer sentence identifying
    # the property. Verify both verbatim before using the smaller common scope.
    if (not numeric_tokens(claim.value) and value_context != value and fit_span and owner and prop == link and
            _contained(link, fit_span) and fit_span != link and _one_statement(fit_span) and
            _contained(value, link) and _contained(prop, link) and not _potential_denial(fit_span["quote"], definition)):
        extra = source[fit_span["start"]:link["start"]]
        copula = source[owner["end"]:value["start"]].strip().casefold()
        if copula in {"was", "is", "were", "are", "是", "为", "以"} and _described_property(definition, extra):
            adjusted.update(value_source=_citation(wid, value, shown),
                            fit_source=_citation(claim.link_source.window, link, shown))

    try:
        record = fact_record(claim.model_copy(update=adjusted), shown, block, source, report["source_sha256"],
                             packet["task"]["fields"], choices, None)
    except ValueError:
        return out
    fit_record = record
    if any(issue["role"] == "owner" for issue in record["evidence_issues"]):
        # Re-evaluate the property on its own evidence. Unresolved object names
        # do not invalidate a locally attested property, value, unit and qualifiers.
        field_claim = claim.model_copy(update={**adjusted, "owner": None, "owner_source": None, "direct": False})
        fit_record = fact_record(field_claim, shown, block, source, report["source_sha256"],
                                 packet["task"]["fields"], {}, None)
    if (claim.purpose == "measurement" and claim.fit != "unsupported" and
            _NON_MEASUREMENT.search(_assertion_text(source, block, link))):
        out["fit"] = _decision("unknown", "measurement_scope_requires_review", [link, fit_span])
    elif unit_scope_unclear and claim.fit != "unsupported":
        out["fit"] = _decision("unknown", "unit_scope_requires_review", [value, link])
    elif fit_record["field_supported"]:
        out["fit"] = _decision("supported", "field_evidence_in_local_assertion", fit_record["evidence"])
    if not record["owner_supported"]:
        return out
    local = record["owner_source"]
    out["direct"] = _decision("supported", "owner_in_value_clause", [local, link], local["quote"], scope=primary["scope"])
    approved_id = claim.owner
    if claim.owner in nominations:
        approved_id = next((r.get("object_id") for r in report["nominations"] if r["key"] == claim.owner
                            and r["block_id"] == wid and r["status"] in ("new_object", "existing_object")), None)
    obj = catalog.get(approved_id)
    reaffirmed = _reaffirmed_alias(source, primary, obj, local["quote"], catalog) if obj else None
    if obj and (obj["section"] == primary["scope"] or (obj.get("qualifier") and obj["qualifier"] in link["quote"]) or reaffirmed):
        proof = [*obj["evidence"], *([reaffirmed] if reaffirmed else [])]
        out["target"] = _decision("supported", "attested_document_object", proof,
                                  obj["label"], approved_id, obj["scope"])
        if local["quote"] != obj["label"] and local["quote"] in {a["name"] for a in obj["aliases"]}:
            alias_evidence = next(a["evidence"] for a in obj["aliases"] if a["name"] == local["quote"])
            out["target"]["alias_names"] = [local["quote"]]
            if alias_evidence not in out["target"]["evidence"]:
                out["target"]["evidence"].append(alias_evidence)
        out["direct"]["id"] = approved_id
    return out
