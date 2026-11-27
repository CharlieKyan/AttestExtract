"""Check a model reading against the original document, separately from its interpretation."""

import re
import unicodedata

from .chunking import _protected_positions, _sentence_ends
from .dataset_resolution_table import TablePaths
from .dataset_supplement_packs import _valid
from .structure import build_structure
from .value_location import _math_spelling, _NUMBER, _SUPERSCRIPTS, value_occurrences


_RANGE_TAIL = re.compile(r"\s*(?:\\?[~～]|–|—|-)\s*[+-]?\d+(?:\.\d+)?\s*")
_EXTRA_UNIT = (r"(?![A-Za-zµμ0-9_/·^°%‰]|\s*[/·^]|\s+(?i:per)\s+\w"
               r"|\s+[A-Za-zµμ]+(?:\^[-+]?|[-+])\d)")
_UNIT_TOKEN = r"[%‰°µμA-Za-z]+(?:\^[-+]?\d+|\d+)?(?:[/·][µμA-Za-z]+(?:\^[-+]?\d+|\d+)?)*"
_READING = re.compile(rf"\(?{_NUMBER}\s*(?P<left>{_UNIT_TOKEN})?"
                      rf"(?:(?:\\?~|–|—|-|±){_NUMBER}\s*(?P<right>{_UNIT_TOKEN})?)?"
                      rf"\)?(?P<scale>×10(?:\^[-+]?\d+|[-+]\d+))?\s*(?P<end>{_UNIT_TOKEN})?")
_CLAUSE = re.compile(r"[,，](?!\d{3}\b)|[;；]|\b(?:and|but|while|whereas|versus|unlike)\b|(?:而|但是|但|相比)", re.I)


def _span(source, start, end):
    return {"start": start, "end": end, "quote": source[start:end]}


def _unit_text(text):
    text = re.sub(r"[⁻⁺]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+", lambda m: "^" + m[0].translate(_SUPERSCRIPTS), text)
    text = re.sub(r"\\(?:mathrm|text)\s*\{([^{}]+)\}", r"\1", text)
    return (unicodedata.normalize("NFKC", text).replace(r"\times", "×").replace(r"\mu", "μ")
            .replace(r"\%", "%").replace("µ", "μ").replace("−", "-"))


def _unit_follows(source, reading, unit, end):
    if not unit:
        return True
    raw = reading["quote"].strip("$").replace("～", "~")
    canonical = _math_spelling("1" + unit)
    normalized = canonical[1:] if canonical else _unit_text(unit).strip()
    if not normalized:
        return False
    quantity = _READING.fullmatch(_math_spelling(raw) or _unit_text(raw).strip())
    if quantity:
        units = {u for u in (quantity["left"], quantity["right"], quantity["end"]) if u}
        if units:
            actual = units.pop()
            remaining = _unit_text(source[reading["end"]:end])
            return (not units and normalized in (actual, (quantity["scale"] or "") + actual)
                    and bool(re.match(_EXTRA_UNIT, remaining)))
    tail = _unit_text(source[reading["end"]:end]).lstrip()
    tail = _RANGE_TAIL.sub("", tail, count=1) if _RANGE_TAIL.match(tail) else tail
    return bool(re.match(re.escape(normalized) + _EXTRA_UNIT, tail))


def _cited_spans(source, obs):
    for entry in obs["source_evidence"]:
        coordinates = entry.get("span")
        if not coordinates or "value" not in entry["fields"]:
            continue
        if len(entry.get("matches", [])) > 1 or len(entry.get("source_matches", [])) > 1:
            continue
        start, end = coordinates["start"], coordinates["end"]
        if 0 <= start < end <= len(source):
            yield _span(source, start, end)


def _table_unit(source, table, unit, obs):
    if not unit or not table.get("value"):
        return None
    unit_positions = {(s["span"]["start"], s["span"]["end"]) for s in obs["source_evidence"]
                      if "unit" in s["fields"] and s.get("span")}
    normalized = _unit_text(unit).strip()
    for entry in table.get("headers", []):
        if not _valid(source, entry) or not any(entry["start"] <= a < b <= entry["end"] for a, b in unit_positions):
            continue
        text = _unit_text(entry["quote"]).strip()
        # Accept one unit written by itself, in parentheses, or after the usual
        # property/unit separator. A denominator inside a compound unit is not a unit label.
        pattern = re.escape(normalized)
        prefix, _, suffix = text.partition("/")
        named_axis = (re.search(r"[^A-Za-zµμ0-9/^·-]", prefix)
                      or prefix.casefold() in {str(obs.get(key) or "").casefold() for key in ("field", "property_name")})
        if (text == normalized or re.fullmatch(r"[^/()[\]]+[(\[]\s*" + pattern + r"\s*[)\]]", text)
                or named_axis and suffix.strip() == normalized
                or re.fullmatch(r"[^/()[\]]+\s+" + pattern, text)):
            return entry
    return None


def _report(status, reason, evidence=(), candidates=()):
    return {"citation_status": status, "citation_reason": reason, "citation_evidence": list(evidence),
            "citation_candidates": list(candidates)}


def _sentence_spans(source):
    covered, blocked = _protected_positions(source)
    boundaries = set(_sentence_ends(source, 0, len(source), covered, blocked))
    boundaries.update(m.end() for m in re.finditer(r"\r\n?|\n", source))
    left = 0
    for end in sorted(boundaries):
        if end > left and source[left:end].strip():
            yield _span(source, left, end)
        left = end


def _citation_order(source, cited):
    """Look near the citation first, but never resolve a tie by proximity."""
    anchors = []
    for span in cited:
        start, end = span["start"], span["end"]
        line_start = max(source.rfind("\n", 0, start), source.rfind("\r", 0, start)) + 1
        breaks = [pos for c in "\r\n" if (pos := source.find(c, end - 1)) >= 0]
        anchors.append((start, end, line_start, min(breaks, default=len(source))))

    def rank(sentence):
        point = sentence.get("value_span", sentence)
        priorities = []
        for start, end, line_start, line_end in anchors:
            same_sentence = sentence["start"] < end and start < sentence["end"]
            same_line = sentence["start"] < line_end and line_start < sentence["end"]
            distance = max(0, point["start"] - end, start - point["end"])
            priorities.append((0 if same_sentence else 1 if same_line else 2, distance))
        return min(priorities, default=(2, 0))
    return rank


def _owner_position(label, text):
    left = r"(?<![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", label) else ""
    right = r"(?![A-Za-z0-9_])" if re.search(r"[A-Za-z0-9_]$", label) else ""
    return re.search(left + re.escape(label) + right, text)


def _candidate_owner(entity, owners, sentence, reading):
    """Positive identification must share a clause; other readings remain visible for review."""
    text = sentence["quote"]
    offset = reading["start"] - sentence["start"]
    start, end = 0, len(text)
    for separator in _CLAUSE.finditer(text):
        if separator.end() <= offset:
            start = separator.end()
        elif separator.start() >= offset:
            end = separator.start()
            break
    clause = text[start:end]
    found = _owner_position(entity, clause)
    for other in owners:
        if other == entity or not other:
            continue
        match = _owner_position(other, clause)
        if match and not (found and found.start() <= match.start() < match.end() <= found.end()):
            return "ambiguous" if found else "other"
    return "matched" if found else "ambiguous"


class TableCitations:
    """Use the archived table and the model's independent owner/heading selections for repair."""

    def __init__(self, source, tables=()):
        self.source = source
        self.records = {f"/table_results/{i}/candidates/{j}": (table["table_id"], record)
                        for i, table in enumerate(tables) for j, record in enumerate(table["candidates"])}
        self.paths = None

    def _proof(self, obs, table, record, cell, reading, entry):
        fields = record.get("resolved_fields", {})
        owners = self.paths.selected(fields.get("entity", []), table)
        axis = (record.get("fields", {}).get("entity") or {}).get("relation")
        if len(owners) != 1 or axis not in {"row", "column"} or not obs.get("entity"):
            return None
        owner = owners[0]
        cross, width = ("column", "colspan") if axis == "row" else ("row", "rowspan")
        owner_width = owner.rowspan if axis == "row" else owner.colspan
        if (getattr(owner, axis) != getattr(cell, axis) or owner_width != 1
                or cell.rowspan != 1 or cell.colspan != 1):
            return None
        # A lone number under a broad heading cannot select among several metrics.
        headings = []
        for key in ("unit", "conditions"):
            headings.extend(self.paths.selected(fields.get(key, []), table))
        specific = [h for h in headings if getattr(h, cross) == getattr(cell, cross) and getattr(h, width) == 1
                    and getattr(h, axis) < getattr(cell, axis)]
        selected = {**entry, "selected_quote": reading["quote"], "selected_start": reading["start"],
                    "selected_end": reading["end"]}
        path = self.paths.path(obs, {**record, "resolved_fields": {**fields, "value": [selected]}}, table.table_id)
        if (path.get("axis") != axis or not path.get("owner") or path.get("gap") or path.get("qualifier_gap")
                or path["owner"]["ref"] != owner.cell_id):
            return None
        # A dimensionless result may instead identify the printed metric by its author-supplied wording.
        prop = str(obs.get("source_property") or "").strip()
        for heading in path["headers"]:
            physical = self.paths.cells[heading["ref"]]
            if (prop and prop == heading["quote"].strip() and getattr(physical, cross) == getattr(cell, cross)
                    and getattr(physical, width) == 1 and getattr(physical, axis) < getattr(cell, axis)):
                specific.append(physical)
        if not {h.cell_id for h in specific}.intersection(h["ref"] for h in path["headers"]):
            return None
        unit_header = _table_unit(self.source, path, obs.get("unit"), obs)
        if not _unit_follows(self.source, reading, obs.get("unit"), cell.span.end):
            if not unit_header or self.source[reading["end"]:cell.span.end].strip() or re.search(r"[A-Za-zµμ%°‰]", reading["quote"]):
                return None
        return [entry, path["owner"], *path["headers"]]

    def adjacent_parameter(self, obs, value_entry, unit_entry):
        """Check a cited label/value pair in a parameter table independently of its filing subject."""
        table_id, record = self.records.get(obs.get("source_record"), (None, None))
        if not record or not unit_entry or not value_entry:
            return False
        if self.paths is None:
            self.paths = TablePaths(self.source, build_structure(self.source))
        table = self.paths.tables.get(table_id)
        if table is None:
            return False
        by_ref = {cell.cell_id: cell for cell in table.cells}
        value, unit = (by_ref.get(entry.get("ref")) for entry in (value_entry, unit_entry))
        if (value is None or unit is None or value.row != unit.row or
                value.column != unit.column + 1 or any(
                    cell.rowspan != 1 or cell.colspan != 1 for cell in (value, unit))):
            return False
        return all(cell.span.start <= entry["start"] < entry["end"] <= cell.span.end
                   for cell, entry in ((value, value_entry), (unit, unit_entry)))

    def repair(self, obs):
        if self.paths is None:
            self.paths = TablePaths(self.source, build_structure(self.source))
        table_id, record = self.records.get(obs.get("source_record"), (None, None))
        table = self.paths.tables.get(table_id)
        if table is None and record is None:
            old_cell = (obs.get("table_context") or {}).get("value")
            if _valid(self.source, old_cell):
                table = next((t for t in self.paths.tables.values() if
                              t.span.start <= old_cell["start"] < old_cell["end"] <= t.span.end), None)
        candidates, supported = [], []
        for cell in table.cells if table else ():
            if cell.is_header or cell.row in table.header_rows:
                continue
            entry = {**self.paths.quote(cell), "row": cell.row, "column": cell.column,
                     "rowspan": cell.rowspan, "colspan": cell.colspan}
            matches = value_occurrences(self.source, obs["value"], cell.span.start, cell.span.end)
            for reading in matches:
                candidates.append({**entry, "value_span": reading})
                if record and len(matches) == 1:
                    proof = self._proof(obs, table, record, cell, reading, entry)
                    if proof:
                        supported.append(proof)
        if len(supported) == 1:
            return _report("located", "引文已找回", supported[0])
        return _report("unverified", "原表未能唯一核实对象、指标、数值和单位", candidates=candidates)


def check_reading(source, obs, *, owners=(), allow_search=True, table_citations=None):
    """Prefer the cited value/cell. Repair only a unique, scoped, same-document occurrence."""
    value, unit = obs["value"], obs.get("unit")
    table = obs.get("table_context") or {}
    is_table = obs["source_kind"] == "table"
    cited = list(_cited_spans(source, obs))
    if is_table:
        cell = table.get("value")
        cited = [cell] if cell and source[cell["start"]:cell["end"]] == cell["quote"] else []
    numeric = bool(re.search(r"\d", str(value)))
    hits = {}
    header = _table_unit(source, table, unit, obs)
    for span in cited:
        if not numeric and not is_table:
            # Paraphrased qualitative text is judged by the model; record its actual citation.
            hits[span["start"], span["end"]] = [span]
            continue
        for reading in value_occurrences(source, value, span["start"], span["end"], unit=unit):
            # A short value citation may stop before the adjacent unit; include the unit in its proof.
            following = source[reading["end"]:reading["end"] + 70]
            boundary = re.split(r"[\r\n。；;，,|<]", following, maxsplit=1)[0]
            end = span["end"] if is_table else max(span["end"], reading["end"] + len(boundary))
            # An inline unit belongs to its own cell, even if a different unit occurs in the heading.
            bare_cell = is_table and value_occurrences(source, value, span["start"], span["end"]) == [reading] and (
                not source[reading["end"]:span["end"]].strip() and
                not re.search(r"[A-Za-zµμ%°‰]", reading["quote"]))
            use_header = header if bare_cell else None
            if _unit_follows(source, reading, unit, end) or use_header:
                proof = [span]
                if unit and not use_header and not _unit_follows(source, reading, unit, span["end"]):
                    proof.append(_span(source, reading["start"], end))
                if use_header:
                    proof.append(use_header)
                hits[reading["start"], reading["end"]] = proof
    if len(hits) == 1:
        proof = next(iter(hits.values()))
        if not (is_table and (table.get("gap") or table.get("qualifier_gap"))):
            return _report("located", "所引原文位置已核对", proof)
        if (obs.get("filing_protocol") == "explanation-v1" and table_citations is not None
                and table.get("gap") == "local_owner_path_unconfirmed" and not table.get("qualifier_gap")
                and header in proof and table_citations.adjacent_parameter(obs, table.get("value"), header)):
            return _report("located", "同一行相邻参数格的原值和单位已核对；主体归属另列", proof)
    if is_table:
        if allow_search:
            return (table_citations or TableCitations(source)).repair(obs)
        return _report("unverified", "未能在所引表格单元及其表头同时核实值和单位")
    if not allow_search:
        return _report("unverified", "所选原文未能唯一核实值和单位")
    if not numeric or not obs.get("entity"):
        return _report("unverified", "没有可唯一核对的原文位置")
    candidates, plausible = [], []
    rank = _citation_order(source, cited)
    sentences = _sentence_spans(source)
    for sentence in sorted(sentences, key=rank) if cited else sentences:
        text = sentence["quote"]
        # Tables carry separate cell and heading coordinates. A prose reread cannot borrow a cell.
        if "|" in text or "<td" in text or "<tr" in text:
            continue
        for reading in value_occurrences(source, value, sentence["start"], sentence["end"], unit=unit):
            if _unit_follows(source, reading, unit, sentence["end"]):
                scoped = _candidate_owner(obs["entity"], owners, sentence, reading)
                candidates.append({**sentence, "value_span": reading})
                if scoped != "other":
                    plausible.append((sentence, scoped))
    if len(plausible) == 1 and plausible[0][1] == "matched":
        return _report("located", "引文已找回", [plausible[0][0]])
    if cited:
        candidates.sort(key=rank)
    return _report("unverified", "有多个可能位置或对象仍待辨认" if candidates else "所引原文未能核实值和单位",
                   candidates=candidates)
