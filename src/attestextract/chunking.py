"""Sentence-aware paragraph and table chunks with original source segments."""

from __future__ import annotations

import re
import unicodedata

from .models import ContextOptions, DocumentChunk, SourceSpan


_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
# These are conservative lexical protections, not a mathematical/linguistic parser.
# A complete formula or a long word may exceed the requested character target.
_ATOMS = re.compile(
    r"(?<!\\)\$\$[\s\S]*?(?<!\\)\$\$"
    r"|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)"
    r"|\\begin\{(?P<environment>[A-Za-z*]+)\}[\s\S]*?\\end\{(?P=environment)\}"
    r"|(?<![\\$])\$(?!\$)(?:\\[^\n]|[^$\n\\])*?(?<!\\)\$"
    r"|\b(?i:Mr|Mrs|Ms|Dr|Prof|Fig|Figs|Eq|Eqs|No|vs|etc)\."
    r"|\b(?:[A-Za-z]\.){2,}"
    r"|[+-]?(?:\d+(?:[.,]\d+)*|\.\d+)(?:[eE][+-]?\d+)?"
    r"|[A-Za-z][A-Za-z0-9_]*(?:[-'][A-Za-z0-9_]+)*"
)
_CLOSERS = '\"\'”’）)]}》」』'
_TRAILING = _CLOSERS + ".。！？!?，,；;：:、"


def _protected_positions(text: str) -> tuple[bytearray, bytearray]:
    """Mark atomic characters and forbidden cuts, using original Unicode indices."""
    covered = bytearray(len(text))
    blocked = bytearray(len(text) + 1)
    for match in _ATOMS.finditer(text):
        start, end = match.span()
        covered[start:end] = b"\x01" * (end - start)
        blocked[start + 1:end] = b"\x01" * (end - start - 1)
    for index in range(1, len(text)):
        current, previous = text[index], text[index - 1]
        if (previous == "\r" and current == "\n" or unicodedata.combining(current)
                or current in "\ufe0e\ufe0f\u200d" or previous == "\u200d"
                or "\U0001f3fb" <= current <= "\U0001f3ff"):
            blocked[index] = 1
    return covered, blocked


def _after_separator(text: str, end: int, limit: int, blocked: bytearray) -> int:
    """Keep closing punctuation and following whitespace with the previous unit."""
    while end < limit and text[end] in _TRAILING and not blocked[end + 1]:
        end += 1
    while end < limit and text[end].isspace():
        end += 1
    while end < limit and blocked[end]:
        end += 1
    return end


def _sentence_ends(text: str, start: int, end: int,
                   covered: bytearray, blocked: bytearray) -> list[int]:
    boundaries = []
    for index in range(start, end):
        if covered[index] or boundaries and index < boundaries[-1]:
            continue
        char = text[index]
        if char not in ".。！？!?":
            continue
        # A Latin full stop must be followed by whitespace, a closer, or EOF.
        if char == "." and index + 1 < end and not (
                text[index + 1].isspace() or text[index + 1] in _CLOSERS):
            continue
        boundary = _after_separator(text, index + 1, end, blocked)
        if not boundaries or boundary > boundaries[-1]:
            boundaries.append(boundary)
    if not boundaries or boundaries[-1] != end:
        boundaries.append(end)
    return boundaries


def _split_long_sentence(text: str, start: int, end: int, max_chars: int,
                         blocked: bytearray) -> list[SourceSpan]:
    spans = []
    content_end = end
    while content_end > start and text[content_end - 1].isspace():
        content_end -= 1
    while end - start > max_chars:
        target = start + max_chars
        # Trailing whitespace alone must not create an extra inference chunk.
        if target >= content_end:
            break
        choices: list[list[int]] = [[], [], [], []]
        for position in range(start + 1, target + 1):
            if blocked[position]:
                continue
            previous = text[position - 1]
            if previous in "\r\n":
                choices[0].append(position)
            elif previous in ",;:，；：、":
                choices[1].append(position)
            elif previous.isspace():
                choices[2].append(position)
            else:
                choices[3].append(position)
        boundary = next((tier[-1] for tier in choices if tier), None)
        if boundary is None:
            # The target lies within one oversized atom. Preserve it whole.
            boundary = target + 1
            while boundary < end and blocked[boundary]:
                boundary += 1
        boundary = _after_separator(text, boundary, end, blocked)
        spans.append(SourceSpan(start=start, end=boundary))
        start = boundary
    if start < end:
        spans.append(SourceSpan(start=start, end=end))
    return spans


def _text_units(text: str, start: int, end: int, max_chars: int,
                covered: bytearray, blocked: bytearray) -> list[list[SourceSpan]]:
    if end - start <= max_chars:
        return [[SourceSpan(start=start, end=end)]]
    units = []
    for boundary in _sentence_ends(text, start, end, covered, blocked):
        units.extend([span] for span in _split_long_sentence(text, start, boundary, max_chars, blocked))
        start = boundary
    return units


def _is_table_start(lines: list[tuple[int, int, str]], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and "|" in lines[index][2]
        and bool(_SEPARATOR.fullmatch(lines[index + 1][2].strip()))
    )


def chunk_document(
    text: str, *, max_chars: int = 6000, table_rows_per_chunk: int = 12,
    context: ContextOptions | None = None, structure=None,
) -> list[DocumentChunk]:
    """Split without rewriting source text; repeated table headers retain origins.

    Prefer paragraphs and sentences, then newline/clause/word boundaries for
    long sentences. Unspaced text can fall back to unprotected character cuts.
    Table rows, headers, words and recognized formulas are atomic and can exceed
    ``max_chars``. This is not a tokenizer or provider context-window guarantee.
    """
    if max_chars < 1 or table_rows_per_chunk < 1:
        raise ValueError("Chunk size and table row count must be positive")
    if not text.strip():
        return []
    if structure is not None or context is not None and context.mode != "none":
        from .contextual_chunking import structured_chunks
        return structured_chunks(text, max_chars=max_chars, table_rows_per_chunk=table_rows_per_chunk,
                                 context=context or ContextOptions(), structure=structure)
    covered, blocked = _protected_positions(text)
    lines: list[tuple[int, int, str]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        lines.append((pos, pos + len(line), line))
        pos += len(line)

    units: list[list[SourceSpan]] = []
    i = 0
    while i < len(lines):
        if not blocked[lines[i][0]] and _is_table_start(lines, i):
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
            while j < len(lines):
                if not blocked[lines[j][0]] and _is_table_start(lines, j):
                    break
                # End after a paragraph boundary so paragraphs can be packed.
                if not blocked[lines[j][0]] and not lines[j - 1][2].strip():
                    break
                j += 1
            end = lines[j - 1][1]
            units.extend(_text_units(text, start, end, max_chars, covered, blocked))
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
