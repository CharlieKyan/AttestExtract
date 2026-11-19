"""Bounded source-side leads and local evidence packets for optional semantic review."""

from collections import defaultdict
import re

from .evidence import is_missing
from .execution import digest
from .structure import build_structure
from .verification_sources import SourceIndex


# A number is a retrieval cue, including a count. Years, figure labels and limits may
# also match. No cue becomes an observation until the reviewer identifies its use.
_NUMBER = re.compile(r"(?<![\d.])[+−-]?\d+(?:[.,]\d+)*(?:[eE][+-]?\d+)?(?![\d]|\.\d)")


def _slice(source, start, end):
    return {"start": start, "end": end, "quote": source[start:end]}


def _valid(source, span):
    if not isinstance(span, dict):
        return False
    left, right = span.get("start"), span.get("end")
    return (type(left) is int and type(right) is int and 0 <= left < right <= len(source)
            and source[left:right] == span.get("quote"))


def _value_anchors(source, observation):
    """Resolve only uniquely located *value* evidence, never a title or unrelated mention."""
    anchors = []
    for ref in observation.get("source_evidence", []):
        span = ref.get("span")
        if (not isinstance(span, dict) or "value" not in ref["fields"]
                or len(ref.get("matches", [])) > 1 or len(ref.get("source_matches", [])) > 1):
            continue
        original = {"quote": ref.get("quote"), **span}
        if _valid(source, original):
            anchors.append(original)
        for match in ref.get("source_matches", []):
            exact = {"quote": match.get("source_quote"), **match["span"]}
            if match["span"] == span and _valid(source, exact):
                anchors.append(exact)
    return anchors


def discover_gaps(source: str, result: dict, analysis: dict) -> list[dict]:
    """Find uncovered numeric occurrences in source order. These are leads, not recall estimates."""
    covered = set()
    for observation in analysis["observations"]:
        numbers = set(_NUMBER.findall(str(observation.get("value") or "")))
        if observation.get("value") == 0:
            numbers.add("0")
        for anchor in _value_anchors(source, observation):
            for match in _NUMBER.finditer(anchor["quote"]):
                if match.group() in numbers:
                    covered.add((anchor["start"] + match.start(), anchor["start"] + match.end()))
    gaps = []
    if not source.strip():
        return gaps
    structure = build_structure(source)
    routes = defaultdict(list)
    for route in result.get("table_results", []):
        routes[route["table_id"]].append(route)
    for table in structure.tables:
        diagnostics = [s.split(":", 1)[1] for s in structure.diagnostics if s.startswith(table.table_id + ":")]
        if not table.cells:
            diagnostics.append("unusable_cell_grid")
        if table.table_id not in routes:
            diagnostics.append("no_frozen_table_route")
        for route in routes[table.table_id]:
            if route["status"] in {"format_error", "partial_format_error"} or not route["candidates"]:
                diagnostics.append(f'{route["chunk_id"]}:{route["status"]}:{len(route["candidates"])}_candidates')
        if diagnostics:
            gaps.append({"kind": "table_coverage", "table_id": table.table_id, "diagnostics": diagnostics,
                         "span": {"start": table.span.start, "end": table.span.end}})
    for passage in SourceIndex(source).passages:
        # Ignore numeric HTML attributes; visible cell text retains its exact offsets.
        visible = re.sub(r"<[A-Za-z/][^<>]*>", lambda tag: " " * len(tag.group()), passage["text"])
        uncovered = []
        for match in _NUMBER.finditer(visible):
            left, right = passage["start"] + match.start(), passage["start"] + match.end()
            if (left, right) not in covered:
                uncovered.append(_slice(source, left, right))
        if uncovered:
            anchor = _slice(source, passage["start"], passage["end"])
            gaps.append({"kind": "possible_missing_observation", "status": "unreviewed",
                         "gap_id": "gap-" + digest(anchor)[:24], "scope": passage["passage_id"],
                         "reason": "uncovered_numeric_occurrence", "evidence": [anchor],
                         "uncovered": uncovered})
    return gaps


def _windows(source, spans):
    intervals = []
    for span in spans:
        if _valid(source, span):
            # Long paragraphs are available to a gap reviewer. Observation contexts are
            # clipped separately before this step. Never rewrite text submitted for citation.
            intervals.append((max(0, span["start"] - 120), min(len(source), span["end"] + 120)))
    merged = []
    for left, right in sorted(set(intervals)):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return [_slice(source, left, right) for left, right in merged]


def _relation_item(source, observation):
    if observation["fit"].get("status") == "contradicted" or observation.get("target_match") == "unsupported":
        return None
    axes = [name for name in ("direct", "target", "fit") if observation[name].get("status") == "unknown"]
    if not axes or is_missing(observation["value"]):
        return None
    anchors = _value_anchors(source, observation)
    if not anchors:
        return None
    endpoints = {}
    spans = list(anchors)
    for end in [observation["direct"], observation["target"], *observation.get("candidates", []),
                *observation.get("related", [])]:
        if end.get("id") and end.get("label"):
            citations = [s for s in end.get("evidence", []) if _valid(source, s)]
            endpoints[end["id"]] = {"id": end["id"], "label": end["label"], "scope": end.get("scope"),
                                    "reason": end.get("reason"), "mentions": citations[:3]}
            spans.extend(citations[:3])
    entity = observation.get("entity")
    if isinstance(entity, str) and entity.strip() and any(entity in s["quote"] for s in anchors):
        if not any(e["label"] == entity for e in endpoints.values()):
            key = "mention-" + digest([entity, anchors])[:24]
            endpoints[key] = {"id": key, "label": entity, "scope": None, "mentions": anchors}
    item = {key: observation.get(key) for key in ("field", "value", "unit", "conditions", "purpose", "entity")}
    item.update(item_id=observation["observation_id"], axes=axes, value_evidence=anchors,
                blockers=observation.get("blockers", []) + observation.get("fit_blockers", []),
                prior={a: observation[a] for a in ("direct", "target", "fit")},
                endpoints=endpoints)
    return item, spans


def review_packs(dataset, source):
    groups = defaultdict(list)
    for observation in dataset["observations"]:
        prepared = _relation_item(source, observation)
        if prepared:
            item, spans = prepared
            # Share calls across a source block and its competing endpoints; each
            # observation retains its own admissible evidence and decisions.
            key = (item["value_evidence"][0]["start"] // 800, tuple(sorted(item["endpoints"])))
            groups[key].append((item, spans))
    task = {"row_subject": dataset.get("row_subject"), "fields": dataset.get("field_definitions", {}),
            "field_policies": dataset.get("field_policies", {})}
    for group in groups.values():
        for index in range(0, len(group), 4):
            block = group[index:index + 4]
            yield {"kind": "existing_observation", "task": task, "items": [item for item, _ in block],
                   "windows": _windows(source, [s for _, spans in block for s in spans])}
    for gap in dataset["coverage_gaps"]:
        if gap.get("kind") != "possible_missing_observation":
            continue
        if not all(_valid(source, s) for s in gap["evidence"] + gap["uncovered"]):
            raise ValueError("Coverage evidence differs from the supplied source.")
        yield {"kind": "missing_candidate", "task": task, "item_id": gap["gap_id"],
               "uncovered": gap["uncovered"], "windows": gap["evidence"]}
