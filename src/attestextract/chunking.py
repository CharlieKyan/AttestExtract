"""Paragraph and simple Markdown table chunks with original source segments."""

from __future__ import annotations

import re

from .models import DocumentChunk, SourceSpan


_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


def _is_table_start(lines: list[tuple[int, int, str]], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and "|" in lines[index][2]
        and bool(_SEPARATOR.fullmatch(lines[index + 1][2].strip()))
    )


def chunk_document(
    text: str, *, max_chars: int = 6000, table_rows_per_chunk: int = 12
) -> list[DocumentChunk]:
    """Split without rewriting source text; repeated table headers retain origins.

    A table row or header is atomic and can exceed ``max_chars``. This is a
    character target, not a tokenizer or provider context-window guarantee.
    """
    if max_chars < 1 or table_rows_per_chunk < 1:
        raise ValueError("Chunk size and table row count must be positive")
    if not text.strip():
        return []
    lines: list[tuple[int, int, str]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        lines.append((pos, pos + len(line), line))
        pos += len(line)

    units: list[list[SourceSpan]] = []
    i = 0
    while i < len(lines):
        if _is_table_start(lines, i):
            header = SourceSpan(start=lines[i][0], end=lines[i + 1][1])
            j = i + 2
            while j < len(lines) and lines[j][2].strip() and "|" in lines[j][2]:
                j += 1
            if j == i + 2:
                units.append([header])
            else:
                row = i + 2
                while row < j:
                    end_row = row
                    chars = header.end - header.start
                    while end_row < j and end_row - row < table_rows_per_chunk:
                        row_size = lines[end_row][1] - lines[end_row][0]
                        if end_row > row and chars + row_size > max_chars:
                            break
                        chars += row_size
                        end_row += 1
                    units.append([
                        header,
                        SourceSpan(start=lines[row][0], end=lines[end_row - 1][1]),
                    ])
                    row = end_row
            i = j
        else:
            start = lines[i][0]
            j = i + 1
            while j < len(lines) and not _is_table_start(lines, j):
                # End after a paragraph boundary so paragraphs can be packed.
                if not lines[j - 1][2].strip():
                    break
                j += 1
            end = lines[j - 1][1]
            # Hard-split long paragraphs, preserving every character and offset.
            while start < end:
                part_end = min(start + max_chars, end)
                units.append([SourceSpan(start=start, end=part_end)])
                start = part_end
            i = j

    packed: list[list[SourceSpan]] = []
    current: list[SourceSpan] = []
    size = 0
    for unit in units:
        unit_size = sum(s.end - s.start for s in unit)
        # Table batches remain separate when their header is repeated, keeping
        # a batch's evidence search limited to that batch's rows.
        repeated_header = len(unit) > 1 and unit[0].end < unit[1].start
        if current and (size + unit_size > max_chars or repeated_header):
            packed.append(current)
            current = []
            size = 0
        current.extend(unit)
        size += unit_size
        if repeated_header:
            packed.append(current)
            current = []
            size = 0
    if current:
        packed.append(current)

    chunks = []
    for number, spans in enumerate(packed, 1):
        # Coalesce adjacent spans only. No artificial offset is ever exposed.
        merged: list[SourceSpan] = []
        for span in spans:
            if merged and merged[-1].end == span.start:
                merged[-1] = SourceSpan(start=merged[-1].start, end=span.end)
            else:
                merged.append(span)
        chunks.append(DocumentChunk(
            chunk_id=f"chunk-{number:04d}",
            text="".join(text[s.start:s.end] for s in merged),
            segments=merged,
        ))
    return chunks
