"""Plan source-owned work before inspecting any entity names or observations."""
from __future__ import annotations

import re

from .contextual_chunking import structured_chunks
from .models import ContextOptions
from .structure import build_structure
from .table_windows import table_split_point


# Only the source notation is available to a text backend. Keep captions and
# surrounding prose in their own readable blocks.
_IMAGE = re.compile(r"!\[[^\]\n]*\]\([^\n)]*\)|<img\b[^>]*>", re.IGNORECASE)


def _image_parts(source, start, end):
    cursor = start
    for image in _IMAGE.finditer(source, start, end):
        left, right = image.span()
        if source[cursor:left].strip():
            yield cursor, left, "text", None
        else:
            left = cursor
        if not source[right:end].strip():
            right = end
        yield left, right, "image", "image_content_unavailable"
        cursor = right
    if cursor < end:
        yield cursor, end, "text", None


def recovery_blocks(source, block, table):
    """One level of recovery. A child owns its primary span; both can see the original scope."""
    start, end = block["start"], block["end"]
    if table:
        point = table_split_point(table, start, end, prefer_data=True)
    else:
        from .chunking import _protected_positions, _sentence_ends
        body = block["text"]
        boundaries = _sentence_ends(body, 0, len(body), *_protected_positions(body))
        points = [start + pos for pos in boundaries if body[pos:].strip()]
        point = min(points, key=lambda pos: abs(2 * pos - start - end), default=None)
    ranges = [(start, end)] if point is None else [(start, point), (point, end)]
    children = []
    for i, (left, right) in enumerate(ranges, 1):
        context = list(block["context"])
        for other_start, other_end in ranges:
            if other_start != left:
                context.append({"role": "recovery_context", "start": other_start, "end": other_end,
                                "text": source[other_start:other_end]})
        children.append({"block_id": f"{block['block_id']}.r{i}", "kind": block["kind"], "scope": block["scope"],
                         "start": left, "end": right, "text": source[left:right], "gap": None,
                         "context": context, "parent": block["block_id"]})
    return children


def source_blocks(source: str, *, max_chars: int = 3600) -> list[dict]:
    """Original Unicode offsets; context may qualify an observation but never owns it."""
    if not isinstance(source, str) or not source.strip() or type(max_chars) is not int or max_chars < 1:
        raise ValueError("Entity-first requires nonempty source and a positive block size.")
    structure = build_structure(source)
    chunks = structured_chunks(source, structure=structure, max_chars=max_chars, table_rows_per_chunk=10,
                               context=ContextOptions(mode="neighbors", neighbor_chars=min(240, max_chars // 8)))
    blocks = []
    for chunk in chunks:
        for body in chunk.body_segments:
            table = next((t for t in structure.tables if t.span.start < body.end and body.start < t.span.end), None)
            context = [(c.role, c.span) for c in chunk.context_segments]
            if table:
                if not table.header_rows:
                    # Untyped HTML cells: present the leading band as original context,
                    # without declaring a semantic header role from physical position alone.
                    leading = max((c.rowspan for c in table.cells if c.row == 0), default=1)
                    context.extend(("table_leading_rows", row) for row in table.rows[:leading])
                gap = None
                if not table.cells:
                    gap = "invalid_table_grid"
                elif _IMAGE.search(source, body.start, body.end):
                    gap = "table_image_content_unavailable"
                parts = [(body.start, body.end, "table", gap)]
            else:
                parts = _image_parts(source, body.start, body.end)
            for start, end, kind, gap in parts:
                section = next((h.span.start for h in reversed(structure.headings) if h.span.start <= start), None)
                references = [{"role": role, "start": s.start, "end": s.end, "text": source[s.start:s.end]}
                              for role, s in context if not (start <= s.start and s.end <= end)]
                blocks.append({"block_id": f"block-{len(blocks)+1:05d}", "kind": kind, "start": start,
                               "end": end, "text": source[start:end], "context": references,
                               "scope": "document" if section is None else f"section@{section}",
                               "gap": gap})
    return blocks
