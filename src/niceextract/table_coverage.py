"""Recover missing simple-task table observations from explicit local header/value pairs."""
from __future__ import annotations

import re

from .evidence import is_missing
from .table_notation import header_labels
from .table_windows import window_owned_refs


def _label(text):
    return " ".join(text.casefold().replace("_", " ").split())


def _header_field(quote, fields):
    for heading in header_labels(quote):
        label = _label(heading)
        if not 2 <= len(label) <= 80 or not any(ch.isalpha() for ch in label):
            continue
        exact = [field for field in fields if label == _label(field)]
        if exact:
            return exact[0] if len(exact) == 1 else None
        pattern = re.escape(label)
        if label.isascii():
            pattern = r"(?<!\w)" + pattern + r"(?!\w)"
        matches = []
        for field, definition in fields.items():
            clauses = re.split(r"[;；。]", _label(definition))
            positive = [re.split(r"\b(?:exclude[sd]?|excluding|except|not)\b|排除|不含|不把|不能",
                                 clause, maxsplit=1)[0] for clause in clauses]
            if any(re.search(pattern, part) for part in positive):
                matches.append(field)
        if matches:
            return matches[0] if len(matches) == 1 else None
    return None


def _numeric(cell):
    return re.match(r"^[\s$~≈<>≤≥+−–-]*(?:\d|\.\d)", cell["quote"]) is not None


def _reported_pairs(parsed):
    pairs = set()
    for record in parsed["records"]:
        binding = record["fields"].get("value")
        if not binding or is_missing(binding.get("value")):
            continue
        anchors = {a["ref"] for a in record["anchors"]}
        pairs.update((record["record_type"], s["ref"]) for s in binding["sources"] if s["ref"] in anchors)
    return pairs


def coverage_targets(task, table, parsed):
    """Owned numeric cells below/along a matching title; other layouts may remain unrecognized."""
    if not task.input_materials or parsed.get("rejections"):
        return {}
    cells = [e for e in table["entries"] if e["kind"] == "cell"]
    owned = window_owned_refs(table)
    reported = _reported_pairs(parsed)
    heading_fields = {e["ref"]: _header_field(e["quote"], task.input_materials.field_list) for e in cells}
    targets = {}
    for h in cells:
        field = heading_fields[h["ref"]]
        if not field:
            continue
        # Also accept a subheading covered by a leading merged title.
        for axis, width, cross, span in (("row", "rowspan", "column", "colspan"),
                                         ("column", "colspan", "row", "rowspan")):
            if h[axis] and not any(c[axis] == 0 and c[width] == h[axis] and c[span] > 1
                                  and c[cross] <= h[cross] and h[cross] + h[span] <= c[cross] + c[span]
                                  for c in cells):
                continue
            lanes = {e[cross] for e in cells if h[cross] <= e[cross] < h[cross] + h[span]}
            for lane in sorted(lanes):
                seen_value = False
                for cell in sorted((e for e in cells if e[axis] >= h[axis] + h[width]
                                    and e[cross] == lane), key=lambda e: e[axis]):
                    if heading_fields[cell["ref"]] and heading_fields[cell["ref"]] != field:
                        break
                    if not _numeric(cell):
                        if seen_value and cell["quote"].strip() not in {"", "—", "-", "–"}:
                            break  # Later subtable/header: its labels have their own scope.
                        continue
                    seen_value = True
                    ref = cell["ref"]
                    if (owned is None or ref in owned) and (field, ref) not in reported:
                        targets.setdefault(field, {})[ref] = h["ref"]
    return targets


def _receipt_key(record, lookup):
    """An identical claim needs the same source selections, scope, values and bindings."""
    from .execution import digest
    from .table_routes import _resolve

    binding = record["fields"].get("value", {})
    if is_missing(binding.get("value")):
        return None
    try:
        anchors = [_resolve(s, lookup) for s in record["anchors"]]
        values = [_resolve(s, lookup) for s in binding["sources"]]
    except ValueError:
        return None
    if not values or not all(v["kind"] == "cell" and any(
        a["ref"] == v["ref"] and a["selected_start"] <= v["selected_start"] < v["selected_end"] <= a["selected_end"]
        for a in anchors) for v in values):
        return None
    # A repeated field value must actually be present at its cited cell.
    if str(binding["value"]).strip() not in {v["selected_quote"].strip() for v in values}:
        return None
    return digest({k: v for k, v in record.items() if k != "record_id"})


def filter_reread(parsed, targets, first, table):
    """Accept only requested field/value cells; shared cells remain usable as contextual evidence."""
    records, indices = [], []
    rejections = list(parsed["rejections"])
    lookup = {entry["ref"]: entry for entry in table["entries"]}
    originals = {key: index for index, rec in zip(first["record_indices"], first["records"], strict=True)
                 if (key := _receipt_key(rec, lookup)) is not None}
    repeats = []
    for i, rec in zip(parsed["record_indices"], parsed["records"], strict=True):
        allowed = targets.get(rec["record_type"], {})
        anchors = {s["ref"] for s in rec["anchors"]}
        binding = rec["fields"].get("value", {})
        refs = {s["ref"] for s in binding.get("sources", [])}
        if anchors and anchors <= allowed.keys() and refs and refs <= anchors and not is_missing(binding.get("value")):
            records.append(rec)
            indices.append(i)
        elif (key := _receipt_key(rec, lookup)) in originals:
            repeats.append({"index": i, "first_index": originals[key]})
        else:
            rejections.append({"index": i, "raw": rec, "errors": [{
                "loc": ["anchors"], "type": "outside_coverage_reread",
                "msg": "Record does not report a requested field at its owned value cell."}]})
    return {"records": records, "record_indices": indices, "rejections": rejections}, repeats


def remaining_targets(targets, parsed):
    reported = _reported_pairs(parsed)
    return {field: [ref for ref in refs if (field, ref) not in reported] for field, refs in targets.items()
            if any((field, ref) not in reported for ref in refs)}
