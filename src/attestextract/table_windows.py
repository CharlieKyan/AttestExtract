"""Bound output responsibility without rewriting a table or guessing its headers.

Every physical cell belongs to exactly one window. The full, unchanged table is
still shared context, so merged labels, units and footnotes remain available.
These helpers never infer that an owned cell contains a requested measurement.
"""

from __future__ import annotations

from copy import deepcopy
import re

from .contextual_chunking import enrich_chunk, merge_spans
from .errors import ConfigurationError
from .models import ContextOptions, DocumentChunk, SourceSpan, TaskSpec


WINDOW_REVISION = "table-window-v1"


def _owned_cells(entries: list[dict], start: int, end: int) -> list[dict]:
    cells = []
    for entry in entries:
        if entry["kind"] != "cell":
            continue
        left, right = entry["start"], entry["end"]
        # Empty cells use their physical point; the right window owns a seam.
        owned = start <= left < end and right <= end
        if not owned and max(start, left) < min(end, right):
            raise ConfigurationError("Table window cuts through a physical cell.")
        if owned:
            cells.append(entry)
    return cells


def add_table_window(table_input: dict, chunk: DocumentChunk) -> dict:
    """Add checked ownership; canonical split-tree validation is execution's job."""
    spans = merge_spans(chunk.body_segments or chunk.segments)
    if len(spans) != 1:
        raise ConfigurationError("A table window needs one contiguous owned source range.")
    start, end = spans[0].start, spans[0].end
    if end <= table_input["start"] or start >= table_input["end"]:
        raise ConfigurationError("Table window does not overlap its source table.")
    cells = _owned_cells(table_input["entries"], start, end)
    if not cells:
        raise ConfigurationError("Table window owns no physical cells.")
    result = deepcopy(table_input)
    result["window"] = {"revision": WINDOW_REVISION, "chunk_id": chunk.chunk_id,
                        "start": start, "end": end,
                        "owned_cell_refs": [entry["ref"] for entry in cells]}
    window_owned_refs(result)
    return result


def window_owned_refs(table_input: dict) -> set[str] | None:
    """Validate ownership metadata before constructing requests or reading replies."""
    window = table_input.get("window")
    if window is None:
        return None
    keys = {"revision", "chunk_id", "start", "end", "owned_cell_refs"}
    if (not isinstance(window, dict) or set(window) != keys
            or window["revision"] != WINDOW_REVISION
            or not isinstance(window["chunk_id"], str)
            or re.fullmatch(r"table-[0-9]{4,}(?:\.[12])+", window["chunk_id"]) is None
            or window["chunk_id"].split(".")[0] != table_input["table_id"]
            or type(window["start"]) is not int or type(window["end"]) is not int
            or not 0 <= window["start"] < window["end"]
            or window["end"] <= table_input["start"] or window["start"] >= table_input["end"]):
        raise ConfigurationError("Invalid table window metadata.")
    expected = [entry["ref"] for entry in _owned_cells(
        table_input["entries"], window["start"], window["end"])]
    if not expected or window["owned_cell_refs"] != expected or len(expected) != len(set(expected)):
        raise ConfigurationError("Table window cell ownership does not match its source range.")
    return set(expected)


def split_table_chunk(text: str, parent: DocumentChunk, *, structure=None) -> list[DocumentChunk]:
    """Split physical rows, or cells of one wide row; never split cell text.

    A binary split balances the number of owned cells, an output-complexity
    proxy. Input character limits do not prevent splitting a short, dense table.
    Each split strictly reduces owned cells; a single physical cell is terminal.
    """
    from .document_routing import routing_structure, table_input_for_chunk

    detected = routing_structure(text, "auto", structure)
    table_input = table_input_for_chunk(text, parent, detected)
    if table_input is None:
        raise ConfigurationError("Table subdivision requires a routed table chunk.")
    body = merge_spans(parent.body_segments or parent.segments)
    if len(body) != 1:
        raise ConfigurationError("Table subdivision requires contiguous source ownership.")
    start, end = body[0].start, body[0].end
    cells = _owned_cells(table_input["entries"], start, end)
    if len(cells) < 2:
        return []
    table = next(t for t in detected.tables if t.table_id == table_input["table_id"])

    def usable(point):
        if not start < point < end:
            return False
        if any(entry["start"] < point < entry["end"] for entry in cells):
            return False
        count = sum(entry["start"] < point for entry in cells)
        return 0 < count < len(cells)

    row_points = [row.start for row in table.rows if usable(row.start)]
    points = row_points or [entry["start"] for entry in cells if usable(entry["start"])]
    if not points:
        return []
    point = min(points, key=lambda pos: (abs(2 * sum(c["start"] < pos for c in cells) - len(cells)), pos))
    children = []
    for number, (left, right) in enumerate(((start, point), (point, end)), 1):
        spans = [SourceSpan(start=left, end=right)]
        chunk = DocumentChunk(chunk_id=f"{parent.chunk_id}.{number}", text=text[left:right],
                              segments=spans, body_segments=spans)
        child = enrich_chunk(text, chunk, detected, parent.context_policy or ContextOptions(mode="structure"))
        # Construction rejects any split cell or empty ownership, independently
        # of the boundary selection heuristic above.
        add_table_window(table_input, child)
        children.append(child)
    return children


def _owned_value_fields(task: TaskSpec | dict | None) -> dict[str, set[str]]:
    """Use declared observation roles, never infer arbitrary string semantics."""
    if task is None:
        return {}
    spec = task if isinstance(task, TaskSpec) else TaskSpec.model_validate(task)
    fields = {}
    for name, record in spec.record_types.items():
        values = {group.value_field for group in record.measurement_groups.values()}
        shared = set(record.identity_fields + record.condition_fields)
        for group in record.measurement_groups.values():
            shared.update([group.unit_field, *group.condition_fields])
        values.update(field for field, definition in record.fields.items()
                      if definition.type in {"integer", "number"} and field not in shared)
        if spec.input_materials is not None:
            values.add("value")
        fields[name] = values
    return fields


def filter_window_records(parsed: dict, table_input: dict, task: TaskSpec | dict | None = None) -> dict:
    """Quarantine foreign anchors; retain raw rejected records and original indices."""
    owned = window_owned_refs(table_input)
    if owned is None:
        return parsed
    value_fields = _owned_value_fields(task)
    records = parsed["records"]
    indices = parsed.get("record_indices", list(range(len(records))))
    if len(indices) != len(records) or any(type(i) is not int or i < 0 for i in indices):
        raise ValueError("Invalid original record indices")
    retained, retained_indices = [], []
    rejections = deepcopy(parsed.get("rejections", []))
    for index, record in zip(indices, records):
        foreign = sorted({selection["ref"] for selection in record["anchors"]
                          if selection["ref"] not in owned})
        observed = value_fields.get(record["record_type"], set())
        foreign += sorted({selection["ref"] for name, binding in record["fields"].items()
                           if binding.get("relation") == "anchor" or name in observed
                           for selection in binding["sources"]
                           if selection["ref"] not in owned and selection["ref"] not in foreign})
        if foreign:
            rejections.append({"index": index, "raw": deepcopy(record), "errors": [{
                "loc": ["anchors"], "msg": "Record uses observation anchors outside this table window.",
                "type": "anchor_outside_window", "refs": foreign}]})
        else:
            retained.append(deepcopy(record))
            retained_indices.append(index)
    return {"records": retained, "record_indices": retained_indices, "rejections": rejections}
