"""Read reported statistics at a value occurrence and project their source-linked columns."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from html import unescape
import re

from .measurement_syntax import _NUMBER, _range_match
from .value_location import locate_value


_LABELS = {
    "min": r"\bmin(?:imum)?\b|最小值?|最低值?",
    "max": r"\bmax(?:imum)?\b|最大值?|最高值?",
    "avg": r"\b(?:avg|average|mean)\b|平均(?:值)?|均值",
}
_INTERVAL = re.compile(r"confidence|credible|\bCI\b|percentile|quartile|置信|可信区间|分位", re.I)
_BOUND = re.compile(
    r"(?P<ge>>=|≥|以上|至少|不低于|不少于|不小于|\b(?:at least|no less than|not less than)\b)|"
    r"(?P<le><=|≤|以下|至多|不高于|不超过|不大于|\b(?:at most|no more than|not more than)\b)|"
    r"(?P<gt>>|大于|高于|超过|\b(?:above|greater than|more than)\b)|"
    r"(?P<lt><|小于|低于|\b(?:below|less than)\b)", re.I)
_OPERATOR = {"ge": ">=", "le": "<=", "gt": ">", "lt": "<"}
_TYPICAL = re.compile(r"通常|一般|\b(?:usually|generally|typically)\b", re.I)
_QUALIFIED_BOUND = re.compile(
    r"\b(?:not|never|no|possibly|perhaps|might|hypothetically)\b|\b(?:cannot|could not)\b"
    r"|并非|不是|未|不|可能|假如|假设", re.I)
_STAT_ONLY = re.compile("|".join([*_LABELS.values(), r"\brange\b|范围"]), re.I)
_COMPONENT_ORDER = ("min", "max", "avg", "unit")
_BOUND_COLUMNS = ("lower_bound", "upper_bound", "bound_operator", "qualifier")
_CLAUSE_BREAK = re.compile(r"[，,;；。!?！？\n]|\.(?!\d)|\b(?:and|but|with|while|whereas)\b", re.I)
_BRACKET_PAIRS = {"(": ")", "（": "）", "[": "]", "【": "】"}


def _outside_asides(fragment, labels, *, left):
    """Read away from the value at the same nesting level, stopping at its enclosing bracket."""
    opening = {close: start for start, close in _BRACKET_PAIRS.items()} if left else _BRACKET_PAIRS
    stack, kept = [], []
    indices = range(len(fragment) - 1, -1, -1) if left else range(len(fragment))
    for i in indices:
        char = fragment[i]
        if char in opening:
            stack.append((opening[char], i))
        elif char in opening.values():
            if not stack or char != stack[-1][0]:
                break
            _, j = stack.pop()
            if not stack:
                content = fragment[min(i, j) + 1:max(i, j)].strip()
                kept.append(f" {content} " if any(p.fullmatch(content) for p in labels) else " ")
        elif not stack:
            kept.append(char)
    return "".join(reversed(kept) if left else kept)


def _value_modifiers(source, span, reading, unit, labels):
    before = _outside_asides(source[span["start"]:reading["start"]], labels, left=True)
    after = _outside_asides(source[reading["end"]:span["end"]], labels, left=False)
    prefix = _CLAUSE_BREAK.split(before)[-1]
    # A previous value can finish its own property without punctuation.
    # A sample identifier's digit does not itself introduce a measurement.
    prefix = re.split(rf"(?<![a-zA-Z_0-9.]){_NUMBER}(?![\d.])", prefix)[-1]
    suffix = _CLAUSE_BREAK.split(after)[0].strip()
    if unit:
        suffix = suffix.removeprefix(unit).strip()
    # A qualifier after the number belongs here only when it closes the value.
    # Searching the entire suffix would read the next property's modifier.
    postfix = re.sub(r"^on (?:an? )?", "", suffix, flags=re.I)
    if not any(pattern.fullmatch(postfix) for pattern in labels):
        postfix = ""
    return prefix + " " + postfix


def _finite_number(spelling):
    try:
        number = Decimal(spelling.replace("−", "-"))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _numbers(value, unit):
    """Use the original unit including any shared multiplier; never convert an endpoint alone."""
    value = str(value).strip().replace("～", "~")
    unit = str(unit or "").strip()
    match = _range_match(value, unit)
    if unit and value.endswith(unit):
        scalar = value[:-len(unit)].strip()
    else:
        scalar = value
    if not match and scalar.startswith("(") and scalar.endswith(")"):
        match = _range_match(scalar[1:-1], "")
    if match:
        left, right = match.group("left", "right")
        lo, hi = _finite_number(left), _finite_number(right)
        if lo is not None and hi is not None and lo <= hi:
            return {"min": left, "max": right}
    return {"value": scalar} if re.fullmatch(_NUMBER, scalar) and _finite_number(scalar) is not None else {}


def value_contexts(source, obs, path, extra_labels=()):
    labels = (_STAT_ONLY, _BOUND, _INTERVAL, *extra_labels)
    # Only an elected metric header or the clause containing this occurrence
    # can identify its statistic. A neighbouring mean often shares its citation.
    contexts = []
    if path and not path.get("gap") and not path.get("qualifier_gap"):
        contexts = [h["quote"] for h in path.get("headers", [])]
    elif not path:
        for entry in obs.get("source_evidence", []):
            span = entry.get("span")
            if not span or "value" not in entry.get("fields", []):
                continue
            reading = locate_value(source, obs["value"], span["start"], span["end"])
            if reading:
                contexts.append(_value_modifiers(source, span, reading, obs.get("unit"), labels))
    # The value may itself be a short citation. Its declared condition can be
    # checked against the original conditions citation recorded for this fact.
    for entry in obs.get("source_evidence", []):
        if "conditions" in entry.get("fields", []) and entry.get("span"):
            span = entry["span"]
            if (obs.get("conditions") and any(p.fullmatch(obs["conditions"].strip()) for p in labels)
                    and obs["conditions"] == source[span["start"]:span["end"]]):
                contexts.append(obs["conditions"])
    return contexts


def _cited_bound_contexts(source, obs):
    # A short "10" value quote often lives inside an owner/condition citation
    # that supplies its comparator. Anchor to the same source occurrence so a
    # bound in a second sentence about another "10" cannot qualify this value.
    spans = [e["span"] for e in obs.get("source_evidence", []) if e.get("span")
             and len(e.get("matches", [])) <= 1 and len(e.get("source_matches", [])) <= 1]
    contexts = []
    for ref in obs.get("source_evidence", []):
        span = ref.get("span")
        if not span or "value" not in ref.get("fields", []) or span not in spans:
            continue
        reading = locate_value(source, obs.get("value"), span["start"], span["end"])
        if reading is None:
            continue
        for outer in spans:
            if outer != span and outer["start"] <= reading["start"] < reading["end"] <= outer["end"]:
                contexts.append(_value_modifiers(source, outer, reading, obs.get("unit"), (_BOUND,)))
    return contexts


def value_statistic(source, obs, path, contexts=None, *, numeric_spelling=None):
    value = numeric_spelling if numeric_spelling is not None else unescape(str(obs.get("value", ""))).strip()
    parts = _numbers(value, obs.get("unit"))
    if contexts is None:
        contexts = value_contexts(source, obs, path)
    # A qualifying sign can be part of the reported value, e.g. an HTML
    # cell containing "&gt;12". Require an entire quantity here: HTML tags,
    # formulas and sample identifiers containing a digit aren't bounds.
    raw_bound = None
    if not parts and (matches := list(_BOUND.finditer(value))) and len(matches) == 1:
        match = matches[0]
        if match.start() == 0 or match.end() == len(value):
            remainder = (value[:match.start()] + value[match.end():]).strip()
            scalar = _numbers(remainder, obs.get("unit")).get("value")
            if scalar is not None:
                raw_bound = match
                parts = {"value": scalar}
    if not parts:
        if obs.get("unit") and re.match(r"\s*\(?\s*[+−-]?\d", str(obs.get("value"))):
            return "qualified", {}
        return None, {}
    bound_matches = []
    bound_contexts = contexts if path else [*contexts, *_cited_bound_contexts(source, obs)]
    for context in bound_contexts:
        plain = re.sub(r"</?[A-Za-z][^<>]*>", "", context).strip()
        bound_matches.extend(m for m in _BOUND.finditer(plain) if m.end() == len(plain))
    if raw_bound:
        bound_matches.append(raw_bound)
    if any(_INTERVAL.search(s) for s in contexts):
        return "interval", {}
    if bound_matches:
        ops = {_OPERATOR[m.lastgroup] for m in bound_matches}
        if len(ops) != 1 or "value" not in parts or any(
            _QUALIFIED_BOUND.search(m.string[:m.start()])
            for m in bound_matches
        ):
            return "qualified", {}
        op = ops.pop()
        bounds = {"lower_bound" if op.startswith(">") else "upper_bound": parts["value"], "bound_operator": op}
        qualifiers = {m[0] for s in bound_contexts for m in _TYPICAL.finditer(s)}
        if len(qualifiers) == 1:
            bounds["qualifier"] = qualifiers.pop()
        return "bound", bounds
    if "min" in parts:
        return "range", parts
    kinds = {kind for kind, pattern in _LABELS.items() if any(re.search(pattern, s, re.I) for s in contexts)}
    if len(kinds) == 1:
        kind = kinds.pop()
        return kind, {kind: parts["value"]}
    return "value", parts


def _source_group(obs):
    table = obs.get("table_context")
    if table:
        headers = tuple(sorted(h["ref"] for h in table.get("headers", [])
                               if not _STAT_ONLY.fullmatch(h["quote"].strip())))
        if obs.get("record_group") and headers and not table.get("qualifier_gap"):
            return ("table", obs["record_group"], headers)
        return None
    spans = {(e["span"]["start"], e["span"]["end"]) for e in obs["source_evidence"]
             if "value" in e.get("fields", []) and e.get("span")}
    if len(spans) == 1:
        return ("text", *spans.pop())
    return None


def _series_key(obs, source):
    anchor = _source_group(obs)
    if not anchor or obs.get("statistic_kind") not in {"min", "max", "avg", "range"}:
        return None
    direct = obs["direct"]["id"]
    if anchor[0] == "text":
        start, end = anchor[1:]
        reading = locate_value(source, obs["value"], start, end)
        if not reading:
            return None
        # A change of cohort or date in the gap between the two values is
        # checked again when the complete series is known.
        if direct and direct.startswith("mention-"):
            label = obs["direct"].get("label")
            if not label or source[start:end].count(label) != 1:
                return None
            direct = ("mention", label)
    condition = (obs.get("conditions") or "").strip()
    qualifiers = tuple(s.strip().casefold() for s in re.split("[，,；;]", condition)
                       if s.strip() and not _STAT_ONLY.fullmatch(s.strip()))
    target = direct if obs["target"]["id"] == obs["direct"]["id"] else obs["target"]["id"]
    return (anchor, direct, target, obs["field"], obs.get("unit"),
            obs.get("measurement_use"), qualifiers)


def _text_series_is_adjacent(members, source, anchor):
    if anchor[0] != "text":
        return True
    spans = sorted((locate_value(source, obs["value"], *anchor[1:]) for obs in members),
                   key=lambda s: s["start"])
    for before, after in zip(spans, spans[1:]):
        between = source[before["end"]:after["start"]].strip()
        if members[0].get("unit"):
            between = between.removeprefix(members[0]["unit"])
        if not re.fullmatch(r"[\s,，；;:=：()（）]*", _STAT_ONLY.sub("", between)):
            return False
    return True


def _consistent_components(members):
    values = {}
    for obs in members:
        for key, number in obs["statistic_parts"].items():
            value = _finite_number(number)
            if key in values or value is None:
                return False
            values[key] = value
    sequence = [values[k] for k in ("min", "avg", "max") if k in values]
    return sequence == sorted(sequence)


def group_statistic_rows(rows, proofs, observations, identities, source):
    """Merge only entire mutually compatible rows and attach provenance for each statistic."""
    by_id = {o["observation_id"]: o for o in observations}
    candidates = defaultdict(list)
    for i, (row, proof) in enumerate(zip(rows, proofs, strict=True)):
        keys = []
        for field, ids in proof["cells"].items():
            if field in identities:
                continue
            members = [by_id[oid] for oid in ids]
            series = _series_key(members[0], source) if len(members) == 1 else None
            keys.append((field, series))
        if proof["delivery"] == "confirmed" and keys and all(key for _, key in keys):
            candidates[(tuple((f, row[f]) for f in identities), tuple(sorted(keys)))].append(i)
    skip = set()
    for (_, keys), indices in candidates.items():
        if len(indices) < 2:
            continue
        merged = {}
        for field, key in keys:
            members = [by_id[proofs[i]["cells"][field][0]] for i in indices]
            if not _consistent_components(members) or not _text_series_is_adjacent(members, source, key[0]):
                break
            merged[field] = members
        else:
            first, *others = indices
            proof = proofs[first]
            for field, members in merged.items():
                rows[first][field] = "；".join(str(o["value"]) + "（" + {
                    "range": "范围", "min": "最小", "max": "最大", "avg": "平均"
                }[o["statistic_kind"]] + "）" for o in members)
            for j in others:
                other = proofs[j]
                for field, ids in other["cells"].items():
                    proof["cells"][field] = list(dict.fromkeys(proof["cells"].get(field, []) + ids))
                for oid in other["observations"]:
                    obs = by_id[oid]
                    obs["row_ids"] = list(dict.fromkeys(proof["row_id"] if rid == other["row_id"] else rid
                                                       for rid in obs["row_ids"]))
                proof["observations"] = list(dict.fromkeys(proof["observations"] + other["observations"]))
                proof["context"] = list(dict.fromkeys(proof["context"] + other["context"]))
            skip.update(others)
    rows[:] = [r for i, r in enumerate(rows) if i not in skip]
    proofs[:] = [p for i, p in enumerate(proofs) if i not in skip]


def set_statistic_columns(data):
    """Allocate wide columns from delivered fields, including newly reviewed observations."""
    by_id = {o["observation_id"]: o for o in data["observations"]}
    fields_seen = {field for row in data["lineage"]["rows"] for field, ids in row["cells"].items()
                   if field not in data["identity_fields"] and any(by_id[oid].get("statistic_kind") for oid in ids)}
    reserved = [*data["schema"], *data.get("context_columns", {}).values(), data["status_column"]]
    used = set(reserved)
    extra = defaultdict(set)
    for o in by_id.values():
        if o.get("row_ids") and o.get("statistic_kind"):
            extra[o["field"]].update(o.get("statistic_parts", {}))
            if o.get("measurement_use") not in {None, "unspecified"}:
                extra[o["field"]].add("use")
    columns = {}
    for field in reserved:
        if field not in fields_seen:
            continue
        columns[field] = {}
        for part in (*_COMPONENT_ORDER, *(p for p in (*_BOUND_COLUMNS, "use") if p in extra[field])):
            column = field + "_" + part
            while column in used:
                column += "（抽取）"
            columns[field][part] = column
            used.add(column)
    data["statistic_columns"] = columns
    for proof in data["lineage"]["rows"]:
        proof["statistics"] = statistic_cells(proof, columns, by_id)


def statistic_cells(proof, columns, observations):
    """Derived CSV cells link to their contributing observations, including units."""
    cells = {}
    for field, names in columns.items():
        for oid in proof["cells"].get(field, []):
            obs = observations[oid]
            if not obs.get("statistic_kind"):
                continue
            use = obs.get("measurement_use")
            for part, value in {**obs.get("statistic_parts", {}), "unit": obs.get("numeric_unit") or obs.get("unit"),
                                "use": use if use != "unspecified" else None}.items():
                if value is None or part not in names:
                    continue
                cell = cells.setdefault(names[part], {"value": value, "observations": []})
                if cell["value"] != value:
                    cell["value"] = None
                cell["observations"].append(oid)
    return cells
