"""Source-indexed structural and neighboring context, separate from body ownership."""

from __future__ import annotations

import re

from .models import ContextOptions, ContextSegment, DocumentChunk, SourceSpan


def merge_spans(spans: list[SourceSpan]) -> list[SourceSpan]:
    result: list[SourceSpan] = []
    for span in sorted(spans, key=lambda item: (item.start, item.end)):
        if span.start == span.end:
            continue
        if result and span.start <= result[-1].end:
            result[-1] = SourceSpan(start=result[-1].start, end=max(result[-1].end, span.end))
        else:
            result.append(span)
    return result


def _inside(span: SourceSpan, scopes: list[SourceSpan]) -> bool:
    return any(scope.start <= span.start and span.end <= scope.end for scope in scopes)


def _section(structure, position: int, length: int):
    stack = []
    end = length
    for heading in structure.headings:
        if heading.span.start > position:
            end = heading.span.start
            break
        while stack and stack[-1].level >= heading.level:
            stack.pop()
        stack.append(heading)
    start = stack[-1].span.end if stack else 0
    return stack, start, end


def _paragraphs(text: str, start: int, end: int, tables) -> list[SourceSpan]:
    spans = []
    for match in re.finditer(r"\S[\s\S]*?(?=\r?\n[ \t]*\r?\n|\Z)", text[start:end]):
        span = SourceSpan(start=start + match.start(), end=start + match.end())
        if not any(span.start < table.span.end and table.span.start < span.end for table in tables):
            spans.append(span)
    return spans


def _bounded_neighbor(text: str, span: SourceSpan, limit: int, before: bool) -> SourceSpan | None:
    if span.end - span.start <= limit:
        return span
    # Whole sentences only. Never cut a long formula or token to fill the allowance.
    from .chunking import _protected_positions, _sentence_ends
    covered, blocked = _protected_positions(text)
    boundaries = [span.start, *_sentence_ends(text, span.start, span.end, covered, blocked)]
    if before:
        starts = [start for start in boundaries[:-1] if span.end - start <= limit]
        return SourceSpan(start=min(starts), end=span.end) if starts else None
    ends = [end for end in boundaries[1:] if end - span.start <= limit]
    return SourceSpan(start=span.start, end=max(ends)) if ends else None


def enrich_chunk(text: str, chunk: DocumentChunk, structure, context: ContextOptions) -> DocumentChunk:
    """Add only original source ranges; no generated separator becomes evidence."""
    body = merge_spans(chunk.body_segments or chunk.segments)
    extras = list(chunk.context_segments)
    diagnostics = list(chunk.context_diagnostics)
    if not body:
        return chunk

    def add(role, span):
        if not _inside(span, body) and not any(item.role == role and item.span == span for item in extras):
            extras.append(ContextSegment(role=role, span=span))

    if context.mode != "none":
        headings, section_start, section_end = _section(structure, body[0].start, len(text))
        for heading in headings:
            add("heading", heading.span)
        for table in structure.tables:
            if any(span.start < table.span.end and table.span.start < span.end for span in body):
                for index in table.header_rows:
                    add("table_header", table.rows[index])
                touched_rows = [index for index, row in enumerate(table.rows)
                                if any(span.start < row.end and row.start < span.end for span in body)]
                for cell in table.cells:
                    if (any(cell.column <= column < cell.column + cell.colspan for column in table.row_header_columns)
                            and any(cell.row <= row < cell.row + cell.rowspan for row in touched_rows)):
                        add("table_row_header", cell.span)
                for span in table.captions:
                    add("table_caption", span)
                for span in table.footnotes:
                    add("table_footnote", span)
        if context.mode == "neighbors" and context.neighbor_chars:
            # A table is a structural neighbor, not prose to silently skip over.
            previous_tables = [table.span.end for table in structure.tables if table.span.end <= body[0].start]
            next_tables = [table.span.start for table in structure.tables if table.span.start >= body[-1].end]
            section_start = max([section_start, *previous_tables])
            section_end = min([section_end, *next_tables])
            paragraphs = _paragraphs(text, section_start, section_end, structure.tables)
            before = [span for span in paragraphs if span.end <= body[0].start]
            after = [span for span in paragraphs if span.start >= body[-1].end]
            for role, choices, backwards in (("neighbor_before", before[-1:], True),
                                              ("neighbor_after", after[:1], False)):
                if choices:
                    selected = _bounded_neighbor(text, choices[0], context.neighbor_chars, backwards)
                    if selected is None:
                        diagnostics.append(role + "_exceeds_sentence_budget")
                    else:
                        add(role, selected)
    # Keep scope boundaries: merging a context/body seam could validate a quote
    # that the model was never shown as a single passage.
    points = sorted({point for span in [*body, *(item.span for item in extras)]
                     for point in (span.start, span.end)})
    scopes = [*body, *(item.span for item in extras)]
    segments = [SourceSpan(start=left, end=right) for left, right in zip(points, points[1:])
                if any(span.start <= left and right <= span.end for span in scopes)]
    return DocumentChunk(chunk_id=chunk.chunk_id, text="".join(text[s.start:s.end] for s in segments),
                         segments=segments, body_segments=body, context_segments=extras,
                         context_policy=context, context_diagnostics=sorted(set(diagnostics)))


def _table_bodies(table, max_chars: int, row_limit: int) -> list[list[SourceSpan]]:
    if not table.rows:
        return [[table.span]]
    # Rowspans form indivisible row groups. Their size is a reported soft overrun.
    groups = []
    row = 0
    while row < len(table.rows):
        stop = row + 1
        while True:
            extended = max([stop, *(cell.row + cell.rowspan for cell in table.cells if row <= cell.row < stop)])
            if extended == stop:
                break
            stop = extended
        groups.append((row, stop))
        row = stop
    batches = []
    first, stop = groups[0]
    for left, right in groups[1:]:
        size = table.rows[right - 1].end - table.rows[first].start
        data_rows = sum(index not in table.header_rows for index in range(first, left))
        if data_rows and (data_rows >= row_limit or size > max_chars):
            batches.append((first, stop))
            first = left
        stop = right
    batches.append((first, stop))
    # Assign every original table byte once, including wrappers and inter-row gaps.
    return [[SourceSpan(start=table.span.start if first == 0 else table.rows[first].start,
                        end=table.span.end if stop == len(table.rows) else table.rows[stop].start)]
            for first, stop in batches]


def structured_chunks(text: str, *, max_chars: int, table_rows_per_chunk: int,
                      context: ContextOptions, structure=None) -> list[DocumentChunk]:
    from .chunking import chunk_document
    from .structure import build_structure, validate_structure
    structure = structure or build_structure(text)
    validate_structure(text, structure)
    # Reserve neighbor allowance before splitting the body; mandatory headings
    # and indivisible table rows remain soft limits, reported by coverage.
    reserve = 2 * context.neighbor_chars if context.mode == "neighbors" else 0
    target = max(1, max_chars - min(reserve, max_chars // 2))
    bodies: list[list[SourceSpan]] = []

    def prose(start, end):
        cuts = sorted({start, end, *(h.span.start for h in structure.headings if start < h.span.start < end)})
        for left, right in zip(cuts, cuts[1:]):
            part = text[left:right]
            if not part.strip():
                if part:
                    bodies.append([SourceSpan(start=left, end=right)])
                continue
            heading = next((h for h in structure.headings if h.span.start == left and h.span.end <= right), None)
            content_start = heading.span.end if heading is not None else left
            if heading is not None:
                while content_start < right and text[content_start].isspace():
                    content_start += 1
            pieces = chunk_document(text[content_start:right], max_chars=target, table_rows_per_chunk=table_rows_per_chunk)
            if not pieces:
                bodies.append([SourceSpan(start=left, end=right)])
            for index, chunk in enumerate(pieces):
                spans = [SourceSpan(start=content_start + s.start, end=content_start + s.end) for s in chunk.segments]
                if index == 0 and heading is not None:
                    spans = merge_spans([SourceSpan(start=left, end=content_start), *spans])
                bodies.append(spans)

    cursor = 0
    for table in structure.tables:
        prose(cursor, table.span.start)
        bodies.extend(_table_bodies(table, target, table_rows_per_chunk))
        cursor = table.span.end
    prose(cursor, len(text))
    # Whitespace belongs to a neighboring body, not a separate inference call.
    packed: list[list[SourceSpan]] = []
    leading = []
    for spans in bodies:
        if not "".join(text[s.start:s.end] for s in spans).strip():
            if packed:
                packed[-1] = merge_spans([*packed[-1], *spans])
            else:
                leading.extend(spans)
        else:
            packed.append(merge_spans([*leading, *spans]))
            leading = []
    chunks = []
    for index, spans in enumerate(packed, 1):
        base = DocumentChunk(chunk_id=f"chunk-{index:04d}", text="".join(text[s.start:s.end] for s in spans),
                             segments=spans, body_segments=spans)
        chunks.append(enrich_chunk(text, base, structure, context))
    coverage_report(text, chunks, max_chars=max_chars)
    return chunks


def coverage_report(text: str, chunks: list[DocumentChunk], *, max_chars: int = 6000) -> dict:
    """Validate reconstruction and report source coverage independently of context."""
    all_body = []
    context_chars = 0
    for chunk in chunks:
        if any(s.start < 0 or s.end > len(text) or s.start >= s.end for s in chunk.segments):
            raise ValueError("Chunk source span is outside the document")
        if chunk.text != "".join(text[s.start:s.end] for s in chunk.segments):
            raise ValueError("Chunk text cannot be reconstructed from its original source spans")
        scopes = merge_spans(chunk.segments)
        body = chunk.body_segments or chunk.segments
        if any(not _inside(span, scopes) for span in [*body, *(item.span for item in chunk.context_segments)]):
            raise ValueError("Body or context is outside the chunk source scope")
        all_body.extend(body)
        context_chars += sum(item.span.end - item.span.start for item in chunk.context_segments)
    covered = merge_spans(all_body)
    gaps = []
    cursor = 0
    for span in covered:
        if cursor < span.start:
            gaps.append(SourceSpan(start=cursor, end=span.start))
        cursor = span.end
    if cursor < len(text):
        gaps.append(SourceSpan(start=cursor, end=len(text)))
    if text.strip() and gaps:
        raise ValueError("Chunk plan leaves original document content uncovered")
    return {"source_characters": len(text), "covered_characters": sum(s.end-s.start for s in covered),
            "uncovered": [s.model_dump() for s in gaps], "whitespace_only": not text.strip(),
            "context_characters": context_chars,
            "repeated_body_characters": sum(s.end-s.start for s in all_body) - sum(s.end-s.start for s in covered),
            "oversized_chunks": [c.chunk_id for c in chunks if len(c.text) > max_chars]}
