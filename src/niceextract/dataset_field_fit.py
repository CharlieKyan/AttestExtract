"""Check qualifications explicitly named by the task against local property evidence."""

import re
from collections import defaultdict
from html import unescape

from .dataset_statistics import _numbers, value_statistic
from .table_notation import _UNIT as _KNOWN_UNIT, header_labels
from .value_location import _math_spelling, locate_value


POLICY_COLUMNS = {"allowed_units": "unit", "allowed_purposes": "purpose",
                  "allowed_statistics": "statistic", "required_conditions": "conditions"}
VALUE_FORMS = {"description", "number", "quantity"}
_PRINTED_UNIT = re.compile(rf"(?:{_KNOWN_UNIT}|ppm|ppb|rpm|dB)(?:[/·](?:{_KNOWN_UNIT}|L))?$")


def _printed_unit(value, unit, *, quantity=False):
    if unit:
        return unit.strip()
    if _numbers(value, ""):
        return ""
    match = _PRINTED_UNIT.search(value)
    if match and match.start() and value[match.start() - 1] in "0123456789) \t⁰¹²³⁴⁵⁶⁷⁸⁹":
        return match[0]
    # An extraction that explicitly identifies a quantity can name an unfamiliar unit.
    if quantity and (match := re.search(r"(?<=\d)\s*([A-Za-zµμ°%‰Ω][\wµμ°%‰·/^⁻⁺+-]*)$", value)):
        return match[1]
    return ""


def value_form(value, unit):
    """Classify the entire value, keeping codes and descriptions containing digits intact."""
    spelling = str(value).strip()
    if spelling.startswith("$") and spelling.endswith("$"):
        spelling = spelling.strip("$").strip()
    spelling = spelling.replace(r"\%", "%").replace(r"\~", "~")
    spelling = re.sub(r"^(?:[<>]=?|[≤≥≈~])\s*", "", spelling)
    observed_unit = _printed_unit(spelling, unit)
    parts = spelling.replace(r"\pm", "±").split("±")
    if len(parts) <= 2 and all(_numbers(part, observed_unit) for part in parts):
        return "quantity" if observed_unit else "number"
    # Judge a shared scale on the complete printed expression, keeping the stored value and unit intact.
    magnitude = spelling.removesuffix(observed_unit).strip() if observed_unit else spelling
    normalized = _math_spelling(magnitude)
    if normalized and (scaled := re.fullmatch(r"(.+)×10\^[+-]?\d+", normalized)):
        if _numbers(scaled[1].strip("()"), observed_unit):
            return "quantity" if observed_unit else "number"
    return "description"


def field_policy_blockers(observation, policy):
    reasons = []
    for key, allowed in policy.items():
        actual = (_policy_form(observation, allowed) if key == "allowed_value_forms"
                  else observation.get(POLICY_COLUMNS[key]))
        if actual not in allowed and not (key == "allowed_value_forms" and actual is None):
            reasons.append(key)
    return reasons


def _policy_form(obs, allowed):
    role = obs.get("value_role")
    value = str(obs.get("value", "")).strip()
    form = value_form(value, obs.get("unit"))
    if role in {"label", "description"}:
        return "description"
    if role == "quantity":
        return "quantity" if obs.get("unit") or _printed_unit(value, None, quantity=True) else (
            "number" if form == "number" else None)
    if form == "number" and "description" in allowed and "number" not in allowed:
        # A digit-only spelling could be a categorical label or a dimensionless reading.
        return None
    if form == "description" and "description" not in allowed and re.match(r"^[<>≤≥≈~+−-]?\s*\d", value):
        return None
    return form


def _reading_kind(kind, role, raw, unit):
    form = value_form(raw, unit)
    if role in {"label", "description"}:
        return "text"
    if role == "quantity" or form == "quantity" or kind == "numeric" and form == "number":
        return "numeric"
    if form == "number" or (re.search(r"\d", raw) and (kind == "numeric" or re.match(r"^[<>≤≥≈~+−-]?\s*\d", raw))):
        return "unknown"
    return "text"


def _typed_measurement(source, obs, raw, unit):
    # The bound/mean detector still reads the original occurrence and its context.
    # Only the numeric spelling is simplified when a whole expression has a shared scale.
    numeric_spelling, scale = None, ""
    expression = raw.strip("$").strip().removesuffix(unit).strip()
    scaled = re.fullmatch(r"(.+)(×10\^[+−-]?\d+)", _math_spelling(expression) or "")
    if scaled and _numbers(scaled[1].strip("()"), ""):
        numeric_spelling, scale = scaled[1].strip("()"), scaled[2]
    statistic, parts = value_statistic(source, {**obs, "unit": unit}, obs.get("table_context"),
                                      numeric_spelling=numeric_spelling)
    shape = {"value": "scalar", "avg": "scalar", "min": "scalar", "max": "scalar",
             "range": "range", "bound": "bound"}.get(statistic)
    if not shape:
        return None, {}
    statistic_parts = {k: v + scale if k in {"value", "min", "max", "avg", "lower_bound", "upper_bound"} else v
                       for k, v in parts.items()}
    obs.update(statistic_kind=statistic, statistic_parts=statistic_parts)
    numeric_parts = {"value": parts["value" if statistic == "value" else statistic]} if shape == "scalar" else dict(parts)
    if scale:
        numeric_parts["scale"] = scale
    return shape, numeric_parts


def apply_value_type(source, obs, contract, legacy_forms):
    """Use the model's semantic role separately from the shape of the printed measurement."""
    if not contract:
        if legacy_forms and _policy_form(obs, legacy_forms) is None:
            obs.update(field_type_status="review", field_type_reason="值的写法不足以确认是否符合字段要求", eligible=False)
        return
    kind, role = contract["kind"], obs.get("value_role", "unknown")
    raw = unescape(str(obs["value"])).strip()
    unit = _printed_unit(raw, obs.get("unit"), quantity=role == "quantity")
    reading = _reading_kind(kind, role, raw, unit)
    role_conflict = (role in {"label", "description"} and bool(obs.get("unit"))
                     and value_form(raw, unit) == "quantity")

    shape, parts = None, {}
    if reading == "numeric":
        shape, parts = _typed_measurement(source, obs, raw, unit)
    else:
        # A material grade may have a numeric spelling. Its digits must not create numeric statistics.
        obs.update(statistic_kind=None, statistic_parts={})
    obs.update(numeric_shape=shape, numeric_value=parts, numeric_unit=(unit or None) if reading == "numeric" else None)

    status, reason = "compatible", ""
    if role_conflict:
        status, reason = "review", "模型将值解释为名称或描述，同时又为这个数报告了单位"
    elif kind == "unknown":
        status, reason = "unspecified", "任务尚未确定字段类型"
    elif reading == "unknown":
        status, reason = "review", "尚不能判明是名称、描述还是测量值"
    elif kind not in {reading, "mixed"}:
        decisive = role in {"quantity", "label", "description"} or reading == "numeric" and bool(unit)
        status = "excluded" if contract["source"] == "user" and decisive else "review"
        reason = "原读数的含义与任务字段类型有分歧"
    elif reading == "numeric" and not shape:
        status, reason = "review", "保留原值，数值形式尚未可靠辨认"
    elif shape and shape not in contract.get("shapes", ("scalar", "range", "bound")):
        status = "excluded" if contract["source"] == "user" else "review"
        reason = "原数值形式不在字段要求的范围内"
    obs.update(field_type_status=status, field_type_reason=reason)
    if status == "review":
        obs["eligible"] = False
    elif status == "excluded":
        obs.update(field_excluded=True, eligible=False)
        obs.setdefault("projection_reason", "task_field_type")


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


def _plan_in_measurement_field(requested, clauses, value_pattern):
    """Distinguish an explicitly attributed design value from a requested measurement."""
    if not re.search(r"\b(?:measured|observed)\b|实测", requested, re.I):
        return None
    if re.search(r"\bor\b|\b(?:planned|target|design|unmeasured)\b|或|计划|目标|设计", requested, re.I):
        return None
    pairing = re.compile(r"(?P<label>\b(?:design target|specified target|planned value)\b|设计目标|设定目标|目标值)"
                         r"\s*(?:(?:of|was|is|为|[=:：])\s*)?" + value_pattern.pattern, re.I)
    result = None
    for clause in clauses:
        pair = pairing.search(clause)
        if pair is None:
            # A separate explicit measurement of this same value is compatible.
            if re.search(r"\b(?:measured|observed)\b|实测", clause, re.I):
                return None
            continue
        if re.search(r"\b(?:met|matched|attained|achieved|measured|observed)\b|达到|测得|实测", clause[:pair.start()], re.I):
            return None
        result = pair["label"]
    return result


def reference_only_setting(item, spans):
    """Flag an assignment citing its claimed owner solely as the basis of the setting.

    This check never infers the actual target or selects a replacement field.
    """
    owner, value = item.get("entity"), item.get("value")
    if not isinstance(owner, str) or not owner.strip() or value is None:
        return False
    for span in spans:
        text = span["quote"]
        for match in re.finditer(
            r"(?:根据|依据)(?P<reference>[^。；;\n]+?)[，,]?\s*(?:将|把)(?P<assignment>[^。；;\n]+)"
            r"|(?:\bbased on\b|\busing\b)(?P<basis>[^,;\n]+),\s*(?P<setting>[^;\n]+)",
            text, re.I,
        ):
            basis = match["reference"] or match["basis"]
            assignment = match["assignment"] or match["setting"]
            if (owner in basis and owner not in assignment
                    and not re.search(r"\b(?:its|their|this|the same)\b|其|该|本身", assignment, re.I)
                    and re.search(r"设[置定]为|\b(?:was |is )?(?:set|configured) to\b", assignment, re.I)
                    and locate_value(assignment, value, 0, len(assignment))):
                return True
    return False


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
    if item.get("source_kind") != "table":
        planned = _plan_in_measurement_field(requested, local, value_pattern)
        if planned:
            return {**fit, "status": "contradicted", "reason": "planned_value_in_measurement_field",
                    "requested_property": requested, "reported_property": planned, "evidence": fact_evidence}
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
                        "requested_property": requested, "evidence": fact_evidence}
    # A grid's property and value can occupy distinct cells. Only use headings on the
    # verified path supplied by the table resolver, never an arbitrary nearby passage.
    headings = {label.casefold() for q in quotes for label in header_labels(q)} if item.get("source_kind") == "table" else set()
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

    exclusions = list(_excluded_properties(definition))
    for term in exclusions:
        if paired_with_value(term):
            return {**fit, "status": "contradicted", "reason": "explicit_property_exclusion",
                    "reported_property": term, "requested_property": requested, "evidence": fact_evidence}

    if exclusions and item.get("name_handling") == "as_reported" and item.get("source_kind") != "table":
        # If the task distinguishes e.g. "usable length" from "total length", a sentence
        # reporting just "length" supplies the measurement but leaves that qualifier open.
        words = re.findall(r"[A-Za-z]+|[\u3400-\u9fff]", requested.casefold())
        short_names = set()
        for i in range(1, len(words)):
            suffix = (" " if requested.isascii() else "").join(words[i:])
            boundary = r"(?<![A-Za-z])" if requested.isascii() else ""
            if len(suffix) >= 2 and any(re.search(boundary + re.escape(suffix) + "$", excluded, re.I)
                                        for excluded in exclusions):
                short_names.add(suffix)
        for quote in quotes:
            # A comma can separate a property label from its verb and value.
            for clause in re.split(r"[。！？!?；;\n]|(?<!\d)\.(?=\s)", quote):
                if not value_pattern.search(clause):
                    continue
                for base_name in sorted(short_names, key=len, reverse=True):
                    connector = r"[\s,，]*(?:(?:was|is|of|为|约为|约|[=：:])\s*)?"
                    match = re.search(boundary + re.escape(base_name) + connector + value_pattern.pattern, clause, re.I)
                    if match and not clause[:match.start() + len(base_name)].casefold().endswith(requested.casefold()):
                        return {**fit, "status": "unknown", "reason": "field_qualifier_unverified",
                                "reported_property": base_name, "requested_property": requested}

    prop = str(item.get("property_name") or "").strip()
    if not prop and item.get("name_handling") == "as_reported":
        # The simple extraction may quote the reported property in its condition
        # (e.g. "mean length"). It still has to occur in the measured clause below.
        prop = str(item.get("conditions") or "").strip()
    base = re.sub(r"^(?:(?:mean|average|minimum|maximum)\s+|平均|最小|最大)", "", prop, flags=re.I)
    if len(base) < 2 or not reported(base) or paired_with_value(requested):
        return fit
    suffix = requested.casefold().removesuffix(base.casefold())
    if (suffix == requested.casefold() or not suffix
            or (base.isascii() and not suffix.endswith((" ", "-")))):
        return fit
    return {**fit, "status": "unknown", "reason": "field_qualifier_unverified",
            "reported_property": prop, "requested_property": requested}


def _table_label(source, header, prop):
    """A literal heading or its explicit property/symbol definition before this local table."""
    if not prop:
        return None
    if any(label.casefold() == prop.casefold() for label in header_labels(header["quote"])):
        return header
    symbol = re.fullmatch(r"\s*([A-Za-zα-ωΑ-Ω]{1,3})\s*(?:[/／].+)?", header["quote"])
    if not symbol:
        return None
    # Stop at a previous table or section. A symbol can have other meanings elsewhere.
    start = header["start"]
    preceding = source[max(0, start - 3500):start]
    boundary = max((m.end() for m in re.finditer(r"</table>|^#{1,6}[ \t].*$", preceding, re.I | re.M)), default=0)
    before = preceding[boundary:].split("<table", 1)[0]
    prefix = r"(?<![A-Za-z])" if prop.isascii() else ""
    definition = prefix + re.escape(prop) + r"\s*[（(]?\s*" + re.escape(symbol[1]) + r"(?![A-Za-z0-9])"
    matches = list(re.finditer(definition, before))
    if len(matches) != 1:
        return None
    match = matches[0]
    # A negated symbol definition cannot select a winning field.
    clause = re.split(r"[，,。;；.!?\n]", before[:match.start()])[-1]
    if re.search(r"\b(?:not|except|unlike)\b|并非|不是|不代表", clause, re.I):
        return None
    left = max(0, start - 3500) + boundary + match.start()
    return {"start": left, "end": left + len(match[0]), "quote": match[0]}


def check_competing_cell_fields(source, observations, fields, identities):
    """One located value occurrence with competing field meanings needs a source discriminator."""
    groups = defaultdict(list)
    for obs in observations:
        cell = (obs.get("table_context") or {}).get("value")
        if not cell or obs["field"] in identities or obs["fit"]["status"] != "supported":
            continue
        value = locate_value(source, obs["value"], cell["start"], cell["end"])
        if value:
            groups[(cell["ref"], value["start"], value["end"])].append(obs)
    for members in groups.values():
        names = {o["field"] for o in members}
        if len({fields[n] for n in names}) < 2:
            continue
        proof = {}
        for obs in members:
            field = obs["field"]
            prop = _requested_property(fields[field])
            for header in obs["table_context"].get("headers", []):
                matched = _table_label(source, header, prop)
                if matched:
                    proof[field] = [header] if matched == header else [header, matched]
        for obs in members:
            obs["competing_fields"] = sorted(names)
            if len(proof) == 1 and obs["field"] in proof:
                obs["field_discriminator"] = proof[obs["field"]]
                continue
            prior = obs["fit"]
            obs["fit"] = {**prior, "status": "unknown", "reason": "competing_fields_for_source_cell",
                          "prior_decision": prior}
            obs["fit_blockers"].append("competing_fields_for_source_cell")
            obs["eligible"] = False
