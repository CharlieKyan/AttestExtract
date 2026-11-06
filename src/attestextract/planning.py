"""Task-aware output estimates and splits that retain original Unicode spans."""

from __future__ import annotations

import math
import re

from .chunking import chunk_document
from .models import ContextOptions, DocumentChunk, ExecutionOptions, GenerationOptions, SourceSpan, TaskSpec


def estimate_output_tokens(text: str, task: TaskSpec) -> int:
    """A planning heuristic, not a tokenizer, provider limit or recall guarantee.

    More numeric statements, field wrappers and measurement quotes need more
    output. No domain vocabulary or transformations of source text are used.
    """
    statements = [part for part in re.split(r"\n+|[。！？!?;；]", text) if part.strip()]
    opportunities = sum(max(1, math.ceil(len(re.findall(r"\d+(?:[.,]\d+)?", part)) / 4))
                        for part in statements if re.search(r"\d", part))
    # Numeric statements often describe the same observation (value, unit,
    # condition), or contain equations/references. Treat density as a grouped
    # signal rather than assuming every numeric statement is a new record.
    records = max(1, math.ceil(len(text) / 600), math.ceil(opportunities / 3))
    mean_bytes = sum(len(part.encode("utf-8")) for part in statements) / max(1, len(statements))
    quote_tokens = min(200, max(32, math.ceil(mean_bytes / 3)))
    per_record = max(64 + len(record.fields) * (24 + quote_tokens)
                     + len(record.measurement_groups) * (64 + quote_tokens)
                     for record in task.record_types.values())
    return 16 + records * per_record


def _map_spans(local: list[SourceSpan], parent: DocumentChunk) -> list[SourceSpan]:
    mapped: list[SourceSpan] = []
    for span in local:
        cursor = 0
        for source in parent.segments:
            length = source.end - source.start
            start, end = max(span.start, cursor), min(span.end, cursor + length)
            if start < end:
                item = SourceSpan(start=source.start + start - cursor, end=source.start + end - cursor)
                if mapped and mapped[-1].end == item.start:
                    mapped[-1] = SourceSpan(start=mapped[-1].start, end=item.end)
                else:
                    mapped.append(item)
            cursor += length
    return mapped


def split_chunk(text: str, parent: DocumentChunk, *, min_chars: int,
                target_chars: int | None = None, structure=None) -> list[DocumentChunk]:
    """Use the shared sentence/table splitter and require strictly smaller children.

    Truncation recovery defaults to half the parent size. Initial planning can
    supply an estimated target directly, avoiding repeated arbitrary halvings.
    """
    if parent.context_policy is not None:
        from .contextual_chunking import structured_chunks, enrich_chunk, merge_spans
        from .structure import build_structure
        structure = structure or build_structure(text)
        body = parent.body_segments or parent.segments
        body_size = sum(s.end - s.start for s in body)
        if body_size <= min_chars:
            return []
        target = max(min_chars, target_chars if target_chars is not None else math.ceil(body_size / 2))
        pieces = structured_chunks(text, max_chars=target, table_rows_per_chunk=1,
                                   context=ContextOptions(), structure=structure)
        children = []
        for piece in pieces:
            spans = merge_spans([SourceSpan(start=max(left.start, right.start), end=min(left.end, right.end))
                                for left in body for right in piece.body_segments
                                if max(left.start, right.start) < min(left.end, right.end)])
            if spans:
                base = DocumentChunk(chunk_id=f"{parent.chunk_id}.{len(children) + 1}",
                                     text="".join(text[s.start:s.end] for s in spans),
                                     segments=spans, body_segments=spans)
                children.append(enrich_chunk(text, base, structure, parent.context_policy))
        if len(children) < 2 or any(sum(s.end-s.start for s in c.body_segments) >= body_size for c in children):
            return []
        if merge_spans([s for c in children for s in c.body_segments]) != merge_spans(body):
            raise ValueError("Subdivision changed body source coverage")
        return children
    if len(parent.text) <= min_chars:
        return []
    target = max(min_chars, target_chars if target_chars is not None else math.ceil(len(parent.text) / 2))
    local = chunk_document(parent.text, max_chars=target,
                           table_rows_per_chunk=max(1, len(parent.text.splitlines()) // 2))
    if len(local) < 2 or any(len(child.text) >= len(parent.text) for child in local):
        return []
    children = []
    for index, child in enumerate(local, 1):
        spans = _map_spans(child.segments, parent)
        children.append(DocumentChunk(chunk_id=f"{parent.chunk_id}.{index}",
                                      text="".join(text[span.start:span.end] for span in spans), segments=spans))
    return children


def plan_chunks(text: str, task: TaskSpec, generation: GenerationOptions,
                options: ExecutionOptions, *, max_chars: int = 6000,
                table_rows_per_chunk: int = 12, context: ContextOptions | None = None,
                structure=None) -> list[DocumentChunk]:
    queue = chunk_document(text, max_chars=max_chars, table_rows_per_chunk=table_rows_per_chunk,
                           context=context, structure=structure)
    limit = generation.max_output_tokens or options.planning_output_tokens
    planned = []
    while queue:
        chunk = queue.pop(0)
        children = []
        if options.adaptive_chunking:
            estimate = estimate_output_tokens(chunk.text, task)
            if estimate > limit * 0.7:
                target = max(1, math.floor(len(chunk.text) * limit * 0.7 / estimate))
                children = split_chunk(text, chunk, min_chars=options.min_chunk_chars, target_chars=target,
                                       structure=structure)
        if children:
            queue[0:0] = children
        else:
            planned.append(chunk)
    return [chunk.model_copy(update={"chunk_id": f"chunk-{index:04d}"})
            for index, chunk in enumerate(planned, 1)]
