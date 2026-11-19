"""Check qualifications explicitly named by the task against local property evidence."""

import re


def _requested_property(definition):
    # Only use a short, literal property label. Arbitrary instructions require semantic review.
    head = re.split(r"[，,。.;；\n]", definition, maxsplit=1)[0].strip()
    if re.search(r"[\u3400-\u9fff]", head):
        head = head.rsplit("的", 1)[-1]
        head = re.sub(r"^(?:各类|各种|各项|所有|任意)", "", head)
        return head if re.fullmatch(r"[\u3400-\u9fff]{2,16}", head) and not re.search(r"或|以及|包括|和|及", head) else ""
    head = re.sub(r"^(?:the\s+)?(?:reported\s+)?(?:(?:all|any)\s+(?:types\s+of\s+)?)?", "", head, flags=re.I)
    head = re.split(r"\s+(?:of|for)\s+", head, maxsplit=1, flags=re.I)[0].strip()
    return head if re.fullmatch(r"[a-zA-Z]+(?:[ -][a-zA-Z]+){0,4}", head) else ""


def _excluded_properties(definition):
    # All property names come from the user's definition, including exclusions.
    patterns = (r"只有([^，。；;]+?)的表述时[，,]\s*不(?:改称|视为|作为)",
                r"(?:^|[.;]\s*)exclude(?:s)?\s+([^.;]+)")
    for pattern in patterns:
        for match in re.finditer(pattern, definition, re.I):
            for term in re.split(r"、|或者|或|,|\s+and\s+|\s+or\s+", match[1], flags=re.I):
                if term.strip():
                    yield term.strip()


_CLAUSE_BREAK = re.compile(r"[，,；;。！？!?\n]|(?<!\d)\.(?=\s)")


def checked_field_fit(definition, item, fit, fact_evidence):
    """Return a stricter field decision only when the local evidence explains the change."""
    if not isinstance(definition, str) or not fact_evidence or fit["status"] == "contradicted":
        return fit
    if item.get("target_match") == "unsupported":
        return {**fit, "status": "contradicted", "reason": "legacy_field_mismatch"}
    if fit["status"] == "unknown" and fit["reason"] not in {"field_definition_unresolved", "extraction_checks_unresolved"}:
        return fit
    if any(b.get("code") != "target_field_unknown" for b in item.get("blockers", [])):
        return fit

    requested = _requested_property(definition)
    raw_value = item.get("value")
    value = "" if raw_value is None else str(raw_value).strip()
    if not requested or not value:
        return fit
    quotes = [s["quote"] for s in fact_evidence]
    value_pattern = re.compile(r"(?<!\d)" + re.escape(value) + r"(?!\d)")
    local = [clause.strip().casefold() for q in quotes for clause in _CLAUSE_BREAK.split(q)
             if value_pattern.search(clause)]
    if not local:
        return fit
    # The value and the explicit absence of the requested property must belong to
    # the same statement, without a new subject between the delimiter and denial.
    denial = re.compile(r"^\s*(?:(?:but|whereas)\s+|但|而)?(?:the\s+)?"
                        + re.escape(requested) + r"\s*(?:(?:was|is)\s+)?(?:not (?:measured|reported)\b|未测|未报告)",
                        re.I)
    for quote in quotes:
        for sentence in re.split(r"[。!?！？\n]|(?<!\d)\.(?=\s)", quote):
            clauses = re.split(r"[,，;；]", sentence)
            if any(value_pattern.search(left) and denial.match(right) for left, right in zip(clauses, clauses[1:])):
                return {**fit, "status": "contradicted", "reason": "requested_property_unreported",
                        "requested_property": requested}
    # A grid's property and value can occupy distinct cells. Only use headings on the
    # verified path supplied by the table resolver, never an arbitrary nearby passage.
    headings = {q.strip().casefold().split("/")[0].strip() for q in quotes} if item.get("source_kind") == "table" else set()
    def reported(term):
        name = term.casefold()
        pattern = re.escape(name)
        if term.isascii():
            pattern = r"(?<!\w)" + pattern + r"(?!\w)"
        return name in headings or any(re.search(pattern, clause) for clause in local)

    def paired_with_value(term):
        # An assertion can report two properties with distinct values in one clause.
        prefix = r"(?<!\w)" if term.isascii() else ""
        connector = r"\s*(?:(?:was|is|of|为|约为|为约|约|最高达|最高可达|均为|[=:：])\s*)?"
        paired = re.compile(prefix + re.escape(term) + connector + value_pattern.pattern, re.I)
        return term.casefold() in headings or any(paired.search(c) for c in local)

    for term in _excluded_properties(definition):
        if paired_with_value(term):
            return {**fit, "status": "contradicted", "reason": "explicit_property_exclusion",
                    "reported_property": term, "requested_property": requested}

    prop = str(item.get("property_name") or "").strip()
    base = re.sub(r"^(?:(?:mean|average|minimum|maximum)\s+|平均|最小|最大)", "", prop, flags=re.I)
    if len(base) < 2 or not reported(base) or paired_with_value(requested):
        return fit
    suffix = requested.casefold().removesuffix(base.casefold())
    if (suffix == requested.casefold() or not suffix
            or (base.isascii() and not suffix.endswith((" ", "-")))):
        return fit
    return {**fit, "status": "unknown", "reason": "field_qualifier_unverified",
            "reported_property": prop, "requested_property": requested}
