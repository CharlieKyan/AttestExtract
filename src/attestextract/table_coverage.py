"""Recover missing simple-task table observations from explicit local header/value pairs."""
from __future__ import annotations

import re

from .evidence import is_missing
from .table_windows import window_owned_refs


def _label(text):
    return " ".join(text.casefold().replace("_", " ").split())


def _header_field(quote, fields):
    # Only the label before a unit can designate a task field. Keep the original
    # cell and all its offsets for the request and for subsequent validation.
    label = _label(re.split(r"[/／（(\[]", quote, maxsplit=1)[0].strip())
    if not 2 <= len(label) <= 80 or not any(ch.isalpha() for ch in label):
        return None
    exact = [field for field in fields if label == _label(field)]
    if len(exact) == 1:
        return exact[0]
    pattern = re.escape(label)
    if label.isascii():
        pattern = r"(?<!\w)" + pattern + r"(?!\w)"
    matches = []
    for field, definition in fields.items():
        positive = re.split(r"[;；。]|\b(?:exclude|excluding|except|not)\b|排除|不含|不把|不能",
                            _label(definition), maxsplit=1)
        if re.search(pattern, positive[0]):
            matches.append(field)
    return matches[0] if len(matches) == 1 else None


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
    targets = {}
    for h in cells:
        # Only read a clear leading heading band in either orientation.
        for axis, width, cross, span in (("row", "rowspan", "column", "colspan"),
                                         ("column", "colspan", "row", "rowspan")):
            if h[axis] != 0:
                continue
            field = _header_field(h["quote"], task.input_materials.field_list)
            if not field:
                continue
            lanes = {e[cross] for e in cells if h[cross] <= e[cross] < h[cross] + h[span]}
            for lane in sorted(lanes):
                seen_value = False
                for cell in sorted((e for e in cells if e[axis] >= h[axis] + h[width]
                                    and e[cross] == lane), key=lambda e: e[axis]):
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
