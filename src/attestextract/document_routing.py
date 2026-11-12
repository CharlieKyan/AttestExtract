"""Route complete source tables and disjoint prose without rewriting the source."""

from __future__ import annotations

import re

from .contextual_chunking import coverage_report, enrich_chunk, merge_spans
from .errors import ConfigurationError
from .models import ContextOptions, DocumentChunk, SourceSpan
from .structure import build_structure, validate_structure


ROUTING_REVISION = "dev29-table-a-2"


def is_table_chunk(chunk: DocumentChunk) -> bool:
    return re.fullmatch(r"table-[0-9]{4,}", chunk.chunk_id) is not None


def routing_structure(text: str, table_mode: str, structure=None):
    """Return the validated structure only when automatic table routing applies."""
    if table_mode not in {"auto", "text"}:
        raise ConfigurationError("table_mode must be 'auto' or 'text'.")
    if table_mode == "text":
        return None
    detected = structure if structure is not None else build_structure(text)
    validate_structure(text, detected)
    if not detected.tables:
        return None
    for table in detected.tables:
        if not table.cells:
            raise ConfigurationError(
                f"Table {table.table_id} has no valid cell grid; automatic table routing cannot proceed. "
                "Inspect the structure diagnostics and original input before running extraction.")
    return detected


def table_input_for_chunk(text: str, chunk: DocumentChunk, structure=None) -> dict | None:
    """Build the A input from the original document, including supplied MinerU structure."""
    if not is_table_chunk(chunk):
        return None
    detected = routing_structure(text, "auto", structure)
    index = int(chunk.chunk_id.split("-")[1]) - 1
    if detected is None or not 0 <= index < len(detected.tables):
        raise ConfigurationError("Table chunk does not identify a table in the current source.")
    table = detected.tables[index]
    body = merge_spans(chunk.body_segments or chunk.segments)
    if not any(span.start <= table.span.start and table.span.end <= span.end for span in body):
        raise ConfigurationError("Table chunk does not own the complete original table.")
    for span in body:
        before = text[span.start:min(span.end, table.span.start)]
        after = text[max(span.start, table.span.end):span.end]
        if before.strip() or after.strip():
            raise ConfigurationError("Table chunk contains unrelated prose in its owned source range.")
    from .table_routes import build_table_input
    return build_table_input(text, table_id=table.table_id, structure=detected)


def plan_routed_chunks(text, task, generation, options, *, max_chars, table_rows_per_chunk,
                       context, structure) -> list[DocumentChunk]:
    """Keep each table atomic; use the existing planner for the remaining prose."""
    from .planning import plan_chunks

    context = context or ContextOptions()
    parts = []

    def prose(start, end):
        if start == end:
            return
        if not text[start:end].strip():
            parts.append((None, [SourceSpan(start=start, end=end)]))
            return
        chunks = plan_chunks(text[start:end], task, generation, options, max_chars=max_chars,
                             table_rows_per_chunk=table_rows_per_chunk, table_mode="text")
        for chunk in chunks:
            parts.append((None, [SourceSpan(start=start + span.start, end=start + span.end)
                                 for span in chunk.segments]))

    cursor = 0
    for index, table in enumerate(structure.tables, 1):
        prose(cursor, table.span.start)
        parts.append((index, [table.span]))
        cursor = table.span.end
    prose(cursor, len(text))

    # Whitespace belongs to a neighboring owned range, never to an extra model call.
    packed, leading = [], []
    for table_index, spans in parts:
        if not any(text[span.start:span.end].strip() for span in spans):
            if packed:
                previous_index, previous_spans = packed[-1]
                packed[-1] = (previous_index, merge_spans([*previous_spans, *spans]))
            else:
                leading.extend(spans)
            continue
        packed.append((table_index, merge_spans([*leading, *spans])))
        leading = []

    result = []
    prose_index = 0
    for table_index, spans in packed:
        if table_index is None:
            prose_index += 1
            chunk_id = f"prose-{prose_index:04d}"
            policy = context
        else:
            chunk_id = f"table-{table_index:04d}"
            # These are the same mandatory heading/caption/footnote sources as A's input.
            policy = ContextOptions(mode="structure")
        base = DocumentChunk(chunk_id=chunk_id, text="".join(text[s.start:s.end] for s in spans),
                             segments=spans, body_segments=spans)
        chunk = enrich_chunk(text, base, structure, policy)
        if table_index is None and any(
                segment.start < table.span.end and table.span.start < segment.end
                for segment in chunk.segments for table in structure.tables):
            raise ConfigurationError("Prose routing leaked a table source range.")
        result.append(chunk)
    coverage_report(text, result, max_chars=max_chars)
    return result
