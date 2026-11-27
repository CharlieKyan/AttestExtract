"""Physical checks on a cited filing target, independent of the measured value."""

import re
import unicodedata

from .dataset_identity import literal_name
from .dataset_citation import _sentence_spans


_CODE = re.compile(r"(?<![a-z0-9])[a-z]+[-_]?\d+[a-z0-9-]*(?![a-z0-9])")
_CELL = re.compile(r"(?P<table>table-\d+)-r(?P<row>\d+)-c(?P<column>\d+)")
_CONTRAST = re.compile(r"[;；]|\b(?:while|whereas|but)\b|但(?:是)?|然而|而(?=[\u3400-\u9fffA-Za-z])", re.I)


def _text(value):
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def _names(text, labels):
    return any(re.search(literal_name(word), _text(text)) for label in labels if label
               for word in (_text(label), *_CODE.findall(_text(label))))


def _different_code(text, label):
    """A sibling code attached to the same noun denotes a different local object."""
    name = _text(label)
    code = _CODE.search(name)
    if not code or _names(text, [name]):
        return False
    noun = name[:code.start()].strip()
    if noun:
        return bool(re.search(literal_name(noun) + r"\s+(" + _CODE.pattern + r")", _text(text)))
    prefix = re.match(r"[a-z]+", code[0])[0]
    return any(re.match(r"[a-z]+", other)[0] == prefix for other in _CODE.findall(_text(text)))


def _reference_bridge(left, right, source):
    """Two cited sentences can share an explicit reference, including across paragraphs."""
    before, after = sorted((left, right), key=lambda s: s["start"])
    earlier, later = _text(before["quote"]), _text(after["quote"])
    references = re.findall(r"\b(?:this|that|these|those|the same)\s+([a-z][a-z-]+)", later)
    if any(re.search(literal_name(word), earlier) for word in references):
        return True
    for match in re.finditer(r"(?:上述|前述|该|同一|此)([\u3400-\u9fff]{1,8})", later):
        noun = match[1]
        if any(noun[:length] in earlier for length in range(2, len(noun) + 1)):
            return True
    # A bare pronoun cannot bridge an uncited intervening passage.
    return (not source[before["end"]:after["start"]].strip()
            and bool(re.search(r"\b(?:it|its|there|this|these|they|their|that)\b|其中|它|其|该|上述|前述", later)))


def _table_path(direct, target, observation):
    a, b = _CELL.fullmatch(direct.get("ref") or ""), _CELL.fullmatch(target.get("ref") or "")
    if not a or not b or a["table"] != b["table"]:
        return False
    path = observation.get("table_context") or {}
    if path.get("owner") and path["owner"]["ref"] != direct["ref"]:
        return False
    anchors = [path["value"]["ref"]] if path.get("value") else [
        e["ref"] for e in observation.get("source_evidence", []) if "value" in e["fields"] and e.get("ref")]
    for ref in anchors:
        c = _CELL.fullmatch(ref)
        if not c or c["table"] != a["table"]:
            continue
        if a["row"] == b["row"] == c["row"] or a["column"] == b["column"] == c["column"]:
            return True
        if b["row"] == c["row"] or b["column"] == c["column"]:
            headers = [*path.get("headers", []), *path.get("row_relation_cells", [])]
            if target["ref"] in {h["ref"] for h in headers}:
                return True
    return False


def _prose_parts(span):
    for sentence in _sentence_spans(span["quote"]):
        start = 0
        segments = [*(_CONTRAST.finditer(sentence["quote"])), None]
        for separator in segments:
            end = separator.start() if separator else len(sentence["quote"])
            if sentence["quote"][start:end].strip():
                offset = span["start"] + sentence["start"]
                yield {"start": offset + start, "end": offset + end, "quote": sentence["quote"][start:end]}
            if separator:
                start = separator.end()


def filing_citation_problem(source, receipt, observation, *, registered_mentions=()):
    """Return a reason for a pending target. The model interprets the cited passages."""
    if receipt.get("status") != "located":
        return "归属引文尚未定位"
    spans = [s for s in receipt.get("evidence", []) if isinstance(s.get("start"), int)
             and isinstance(s.get("end"), int) and 0 <= s["start"] < s["end"] <= len(source)
             and source[s["start"]:s["end"]] == s["quote"]]
    # A citation consisting only of the reading, unit, and punctuation can prove
    # the number, but supplies no wording about what the measurement describes.
    material = []
    for span in spans:
        remainder = _text(span["quote"])
        for spelling in (observation.get("value"), observation.get("unit")):
            if spelling is not None and _text(spelling):
                remainder = remainder.replace(_text(spelling), "")
        if any(ch.isalpha() for ch in remainder):
            if span.get("ref"):
                material.append(span)
            else:
                material.extend(_prose_parts(span))
    if not material:
        return "归属引文只给出了数值或单位，未指明归档对象"
    names = [name for name in (receipt.get("target"), receipt.get("target_source_name")) if name]
    direct_name = observation.get("entity")
    owners = [part for part in material if _names(part["quote"], [direct_name])]
    targets = [part for part in material if _names(part["quote"], names)]
    if not targets:
        # Only the actual registered mention inside a cited passage can anchor
        # a verified alias. An independent registration elsewhere is not a link.
        targets = [part for part in material if any(
            isinstance(m.get("start"), int) and isinstance(m.get("end"), int)
            and part["start"] <= m["start"] < m["end"] <= part["end"] and m["quote"] == source[m["start"]:m["end"]]
            and any(ch.isalpha() for ch in m["quote"]) and not _names(m["quote"], [direct_name])
            for m in registered_mentions)]
    if not owners:
        return "归属引文没有辨认出本次直接对象，需核对是否属于其他样品或部件"
    if not targets:
        return "已定位关系引文，归档对象在原文中的称呼或指代仍待核实"
    for owner in owners:
        for target in targets:
            if owner.get("ref") or target.get("ref"):
                if _table_path(owner, target, observation):
                    return None
            elif owner == target:
                return None
            elif not _different_code(target["quote"], direct_name):
                if _reference_bridge(owner, target, source):
                    return None
                # An explicitly cited third sentence can refer back to both
                # endpoints, e.g. "It was collected from that station."
                if any(p["start"] >= max(owner["end"], target["end"])
                       and not _different_code(p["quote"], direct_name)
                       and _reference_bridge(owner, p, source) and _reference_bridge(target, p, source)
                       for p in material if not p.get("ref")):
                    return None
    return "两端对象分别有出处，其间的指代或表格对应关系仍待核实"
