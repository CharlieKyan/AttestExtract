"""Conservative document structure indexed into the unchanged Unicode source.

No OCR, entity decoding, or HTML-to-Markdown conversion is performed here.
Uncertain tables retain their outer span but expose no usable cell grid.
"""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import math
import re

from pydantic import Field

from .models import ContractModel, SourceSpan


class Heading(ContractModel):
    level: int = Field(ge=1, le=6)
    span: SourceSpan


class TableCell(ContractModel):
    cell_id: str
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    rowspan: int = Field(default=1, ge=1)
    colspan: int = Field(default=1, ge=1)
    span: SourceSpan
    is_header: bool = False


class Table(ContractModel):
    table_id: str
    span: SourceSpan
    rows: list[SourceSpan] = Field(default_factory=list)
    cells: list[TableCell] = Field(default_factory=list)
    header_rows: list[int] = Field(default_factory=list)
    row_header_columns: list[int] = Field(default_factory=list)
    captions: list[SourceSpan] = Field(default_factory=list)
    footnotes: list[SourceSpan] = Field(default_factory=list)
    page_idx: int | None = Field(default=None, ge=0)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)


class DocumentStructure(ContractModel):
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    headings: list[Heading] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    # Hash of canonical JSON supplied as mineru_content, not of a PDF file.
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def _lines(text: str) -> list[SourceSpan]:
    spans = []
    start = 0
    for line in text.splitlines(keepends=True):
        spans.append(SourceSpan(start=start, end=start + len(line)))
        start += len(line)
    return spans


def _trim(text: str, start: int, end: int) -> SourceSpan:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return SourceSpan(start=start, end=end)


def _pipe_cells(text: str, line: SourceSpan) -> list[SourceSpan] | None:
    """Split unescaped pipes; escaped pipes remain literal cell content."""
    content = _trim(text, line.start, line.end)
    pipes = []
    for index in range(content.start, content.end):
        if text[index] != "|":
            continue
        back = index - 1
        while back >= content.start and text[back] == "\\":
            back -= 1
        if (index - back - 1) % 2 == 0:
            pipes.append(index)
    if not pipes:
        return None
    start = content.start + (pipes[0] == content.start)
    end = content.end - (pipes[-1] == content.end - 1)
    internal = [index for index in pipes if start <= index < end]
    edges = [start - 1, *internal, end]
    return [_trim(text, left + 1, right) for left, right in zip(edges, edges[1:])]


def _separator(text: str, cells: list[SourceSpan] | None) -> bool:
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", text[cell.start:cell.end]) for cell in cells
    )


def _markdown_tables(text: str, excluded: list[SourceSpan]) -> list[tuple[Table, list[str]]]:
    lines = _lines(text)
    result = []
    index = 0
    while index + 1 < len(lines):
        line = lines[index]
        if any(span.start < line.end and line.start < span.end for span in excluded):
            index += 1
            continue
        header = _pipe_cells(text, line)
        separator = _pipe_cells(text, lines[index + 1])
        if not header or not _separator(text, separator):
            index += 1
            continue
        rows = [line]
        contents = [header]
        end = index + 2
        while end < len(lines):
            if any(span.start < lines[end].end and lines[end].start < span.end for span in excluded):
                break
            cells = _pipe_cells(text, lines[end])
            if cells is None or _separator(text, cells):
                break
            rows.append(lines[end])
            contents.append(cells)
            end += 1
        table = Table(table_id="pending", span=SourceSpan(start=line.start, end=lines[end - 1].end))
        diagnostics = []
        if len(separator) != len(header) or any(len(cells) != len(header) for cells in contents):
            diagnostics.append("invalid_markdown_column_count")
        else:
            table.rows = rows
            table.header_rows = [0]
            table.cells = [TableCell(cell_id="pending", row=row, column=column,
                                    span=span, is_header=row == 0)
                           for row, cells in enumerate(contents) for column, span in enumerate(cells)]
        result.append((table, diagnostics))
        index = end
    return result


class _HTMLTables(HTMLParser):
    """An offset-only HTML reader; malformed or nested grids are never inferred."""

    def __init__(self, text: str, excluded: list[SourceSpan]) -> None:
        super().__init__(convert_charrefs=False)
        self.text = text
        masked = list(text)
        for span in excluded:
            for index in range(span.start, span.end):
                if masked[index] not in "\r\n":
                    masked[index] = " "
        self.parse_text = "".join(masked)
        self.line_starts = [0] + [match.end() for match in re.finditer("\n", text)]
        self.tables: list[tuple[Table, list[str]]] = []
        self.current: Table | None = None
        self.depth = 0
        self.errors: list[str] = []
        self.row_start: int | None = None
        self.row_cells: list[TableCell] = []
        self.cell: tuple[int, str, int, int, str | None] | None = None
        self.caption_start: int | None = None
        self.in_head = False

    def _position(self) -> int:
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def _error(self, reason: str) -> None:
        if reason not in self.errors:
            self.errors.append(reason)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        position = self._position()
        if tag == "table":
            self.depth += 1
            if self.current is not None:
                self._error("nested_html_table")
                return
            self.current = Table(table_id="pending", span=SourceSpan(start=position, end=position))
            self.errors = []
            return
        if self.current is None or self.depth != 1:
            return
        if tag == "thead":
            self.in_head = True
        elif tag == "tr":
            if self.row_start is not None:
                self._error("unclosed_html_row")
            self.row_start = position
            self.row_cells = []
        elif tag in {"td", "th"}:
            if self.row_start is None or self.cell is not None:
                self._error("invalid_html_cell_nesting")
            attributes = dict(attrs)
            if len(attributes) != len(attrs):
                self._error("duplicate_html_attribute")
            dimensions = []
            for name in ("rowspan", "colspan"):
                raw = attributes.get(name, "1")
                if raw is None or not re.fullmatch(r"[1-9][0-9]*", raw):
                    self._error("invalid_html_span")
                    dimensions.append(1)
                else:
                    dimension = int(raw)
                    if dimension > len(self.text):
                        self._error("html_span_exceeds_source_size")
                        dimension = 1
                    dimensions.append(dimension)
            self.cell = (position + len(self.get_starttag_text()), tag, *dimensions, attributes.get("scope"))
        elif tag == "caption":
            if self.caption_start is not None:
                self._error("nested_html_caption")
            self.caption_start = position + len(self.get_starttag_text())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"table", "tr", "td", "th"}:
            self.handle_starttag(tag, attrs)
            self._error("self_closing_html_table_element")
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        position = self._position()
        closing = self.text.find(">", position)
        end = len(self.text) if closing < 0 else closing + 1
        if tag == "table":
            self.depth -= 1
            if self.depth:
                return
            if self.row_start is not None or self.cell is not None or self.caption_start is not None:
                self._error("unclosed_html_table_content")
            self.current.span.end = end
            self._finish()
            return
        if self.depth != 1:
            return
        if tag in {"td", "th"}:
            if self.cell is None:
                self._error("unexpected_html_cell_end")
                return
            start, opening, rowspan, colspan, scope = self.cell
            if opening != tag:
                self._error("mismatched_html_cell_end")
            row = len(self.current.rows)
            column = 0
            previous = self.current.cells + self.row_cells
            for existing in sorted(previous, key=lambda cell: cell.column):
                if (existing.row <= row < existing.row + existing.rowspan
                        and existing.column <= column < existing.column + existing.colspan):
                    column = existing.column + existing.colspan
            if any(existing.row < row + rowspan and row < existing.row + existing.rowspan
                   and existing.column < column + colspan and column < existing.column + existing.colspan
                   for existing in previous):
                self._error("overlapping_html_cells")
            self.row_cells.append(TableCell(
                cell_id="pending", row=row, column=column, rowspan=rowspan, colspan=colspan,
                span=SourceSpan(start=start, end=position), is_header=tag == "th",
            ))
            if tag == "th" and scope in {"row", "rowgroup"}:
                self.current.row_header_columns.extend(range(column, column + colspan))
            self.cell = None
        elif tag == "tr":
            if self.row_start is None or self.cell is not None:
                self._error("invalid_html_row_end")
                self.cell = None
            else:
                row = len(self.current.rows)
                self.current.rows.append(SourceSpan(start=self.row_start, end=end))
                if self.in_head or self.row_cells and all(cell.is_header for cell in self.row_cells):
                    self.current.header_rows.append(row)
                self.current.cells.extend(self.row_cells)
            self.row_cells = []
            self.row_start = None
        elif tag == "thead":
            self.in_head = False
        elif tag == "caption":
            if self.caption_start is None:
                self._error("unexpected_html_caption_end")
            else:
                self.current.captions.append(SourceSpan(start=self.caption_start, end=position))
            self.caption_start = None

    def _finish(self) -> None:
        table = self.current
        if not table.rows or not table.cells:
            self._error("empty_html_grid")
        elif any(cell.row + cell.rowspan > len(table.rows) for cell in table.cells):
            self._error("html_rowspan_out_of_bounds")
        else:
            width = max(cell.column + cell.colspan for cell in table.cells)
            for row in range(len(table.rows)):
                cursor = 0
                active = sorted((cell for cell in table.cells if cell.row <= row < cell.row + cell.rowspan),
                                key=lambda cell: cell.column)
                for cell in active:
                    if cell.column != cursor:
                        self._error("incomplete_html_grid")
                    cursor = cell.column + cell.colspan
                if cursor != width:
                    self._error("incomplete_html_grid")
        if self.errors:
            table.rows = []
            table.cells = []
            table.header_rows = []
            table.row_header_columns = []
        else:
            table.row_header_columns = sorted(set(table.row_header_columns))
        self.tables.append((table, self.errors))
        self.current = None
        self.row_start = None
        self.cell = None
        self.row_cells = []
        self.caption_start = None
        self.in_head = False

    def finish(self) -> list[tuple[Table, list[str]]]:
        self.feed(self.parse_text)
        self.close()
        if self.current is not None:
            self._error("unclosed_html_table")
            self.current.span.end = len(self.text)
            self._finish()
        return self.tables


def _fenced_blocks(text: str) -> list[SourceSpan]:
    spans = []
    active: tuple[str, int, int] | None = None
    for line in _lines(text):
        content = text[line.start:line.end].rstrip("\r\n")
        match = re.match(r" {0,3}(`{3,}|~{3,})(.*)$", content)
        if not match:
            continue
        marker, tail = match.groups()
        if active is None:
            active = (marker[0], len(marker), line.start)
        elif marker[0] == active[0] and len(marker) >= active[1] and not tail.strip():
            spans.append(SourceSpan(start=active[2], end=line.end))
            active = None
    if active is not None:
        spans.append(SourceSpan(start=active[2], end=len(text)))
    return spans


def _parse_tables(text: str) -> tuple[list[Table], list[str]]:
    fenced = _fenced_blocks(text)
    html = _HTMLTables(text, fenced).finish()
    parsed = html + _markdown_tables(text, fenced + [table.span for table, _ in html])
    tables = []
    diagnostics = []
    for number, (table, errors) in enumerate(sorted(parsed, key=lambda item: item[0].span.start), 1):
        table.table_id = f"table-{number:04d}"
        for cell in table.cells:
            cell.cell_id = f"{table.table_id}-r{cell.row:04d}-c{cell.column:04d}"
            cell.is_header = cell.is_header or cell.row in table.header_rows or cell.column in table.row_header_columns
        tables.append(table)
        diagnostics.extend(f"{table.table_id}:{error}" for error in errors)
    return tables, diagnostics


def _headings(text: str, tables: list[Table]) -> list[Heading]:
    lines = _lines(text)
    excluded = [table.span for table in tables] + _fenced_blocks(text)
    headings = []
    index = 0
    while index < len(lines):
        line = lines[index]
        content = text[line.start:line.end].rstrip("\r\n")
        if any(span.start < line.end and line.start < span.end for span in excluded):
            index += 1
            continue
        atx = re.fullmatch(r" {0,3}(#{1,6})(?:[ \t]+.*|[ \t]*)", content)
        if atx:
            headings.append(Heading(level=len(atx.group(1)), span=line))
        elif content.strip() and index + 1 < len(lines):
            underline = text[lines[index + 1].start:lines[index + 1].end].strip()
            if re.fullmatch(r"=+|-+", underline):
                headings.append(Heading(level=1 if underline[0] == "=" else 2,
                                        span=SourceSpan(start=line.start, end=lines[index + 1].end)))
                index += 1
        index += 1
    return headings


def _unique_span(text: str, quote: str) -> SourceSpan | None:
    if not quote:
        return None
    start = text.find(quote)
    if start < 0 or text.find(quote, start + 1) >= 0:
        return None
    return SourceSpan(start=start, end=start + len(quote))


def _import_mineru(text: str, structure: DocumentStructure, content: object) -> None:
    if not isinstance(content, list) or any(not isinstance(item, dict) for item in content):
        raise ValueError("MinerU content must be a content_list array of objects")
    structure.source_sha256 = hashlib.sha256(json.dumps(
        content, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()
    attached: set[str] = set()
    for index, item in enumerate(content):
        label = f"mineru[{index}]"
        if item.get("type") == "table":
            body = item.get("table_body")
            span = _unique_span(text, body) if isinstance(body, str) else None
            matches = [table for table in structure.tables if span is not None
                       and table.span.start == span.start
                       and span.end <= table.span.end
                       and text[span.end:table.span.end].strip() == ""]
            if len(matches) != 1 or matches[0].table_id in attached:
                structure.diagnostics.append(f"{label}:table_body_not_uniquely_aligned")
                continue
            table = matches[0]
            attached.add(table.table_id)
            for source_key, target_key in (("table_caption", "captions"), ("table_footnote", "footnotes")):
                values = item.get(source_key, [])
                if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                    structure.diagnostics.append(f"{label}:invalid_{source_key}")
                    continue
                for value in values:
                    located = _unique_span(text, value)
                    if located is None:
                        structure.diagnostics.append(f"{label}:{source_key}_not_uniquely_aligned")
                    elif located not in getattr(table, target_key):
                        getattr(table, target_key).append(located)
            page = item.get("page_idx")
            if page is not None:
                if type(page) is int and page >= 0:
                    table.page_idx = page
                else:
                    structure.diagnostics.append(f"{label}:invalid_page_idx")
            bbox = item.get("bbox")
            if bbox is not None:
                if (isinstance(bbox, list) and len(bbox) == 4
                        and all(type(value) in {int, float} for value in bbox)
                        and all(math.isfinite(value) for value in bbox)
                        and bbox[0] <= bbox[2] and bbox[1] <= bbox[3]):
                    table.bbox = [float(value) for value in bbox]
                else:
                    structure.diagnostics.append(f"{label}:invalid_bbox")
        elif item.get("type") == "text" and item.get("text_level") is not None:
            level, quote = item["text_level"], item.get("text")
            if type(level) is not int or not 1 <= level <= 6 or not isinstance(quote, str):
                structure.diagnostics.append(f"{label}:invalid_heading")
                continue
            span = _unique_span(text, quote)
            if span is None:
                structure.diagnostics.append(f"{label}:heading_not_uniquely_aligned")
                continue
            existing = [heading for heading in structure.headings
                        if heading.span.start <= span.start and span.end <= heading.span.end]
            if existing:
                if existing[0].level != level:
                    structure.diagnostics.append(f"{label}:heading_level_conflict")
            elif not any(table.span.start <= span.start < table.span.end for table in structure.tables):
                structure.headings.append(Heading(level=level, span=span))


def _apply_hints(structure: DocumentStructure, hints: object) -> None:
    if not isinstance(hints, dict) or set(hints) - {"tables"} or not isinstance(hints.get("tables", {}), dict):
        raise ValueError("Structure hints must contain a tables mapping")
    by_id = {table.table_id: table for table in structure.tables}
    for key, hint in hints.get("tables", {}).items():
        if key not in by_id:
            raise ValueError(f"Structure hints reference an unknown table: {key}")
        if not isinstance(hint, dict) or set(hint) - {"header_rows", "row_header_columns"}:
            raise ValueError("Table hints support header_rows and row_header_columns only")
        table = by_id[key]
        if not table.cells:
            raise ValueError("Hints cannot repair an invalid table grid")
        width = max(cell.column + cell.colspan for cell in table.cells)
        for name, limit in (("header_rows", len(table.rows)), ("row_header_columns", width)):
            if name not in hint:
                continue
            values = hint[name]
            if (not isinstance(values, list) or any(type(value) is not int or not 0 <= value < limit for value in values)
                    or len(set(values)) != len(values)):
                raise ValueError(f"Invalid {name} hint for {key}")
            setattr(table, name, sorted(values))
        for cell in table.cells:
            cell.is_header = cell.is_header or cell.row in table.header_rows or cell.column in table.row_header_columns


def build_structure(text: str, *, mineru_content: object = None, hints: object = None) -> DocumentStructure:
    """Index supported source syntax and only uniquely aligned MinerU metadata."""
    if not isinstance(text, str):
        raise TypeError("Document text must be a string")
    tables, diagnostics = _parse_tables(text)
    structure = DocumentStructure(text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                                  tables=tables, headings=_headings(text, tables), diagnostics=diagnostics)
    if mineru_content is not None:
        _import_mineru(text, structure, mineru_content)
    if hints is not None:
        _apply_hints(structure, hints)
    for table in structure.tables:
        if table.cells and not table.header_rows:
            structure.diagnostics.append(f"{table.table_id}:header_rows_unspecified")
    structure.headings.sort(key=lambda heading: heading.span.start)
    validate_structure(text, structure)
    return structure


def validate_structure(text: str, structure: DocumentStructure) -> None:
    """Reject stale/tampered offsets and cell geometry, without guessing semantics."""
    if structure.text_sha256 != hashlib.sha256(text.encode("utf-8")).hexdigest():
        raise ValueError("Document structure does not identify this exact text")
    expected, _ = _parse_tables(text)
    if len(expected) != len(structure.tables):
        raise ValueError("Document structure must retain every parsed table")
    def check_span(span: SourceSpan, *, empty: bool = False) -> None:
        if span.end > len(text) or span.start < 0 or span.end < span.start or not empty and span.start == span.end:
            raise ValueError("Structure spans must lie within the original source")
    for heading in structure.headings:
        check_span(heading.span)
        if not text[heading.span.start:heading.span.end].strip():
            raise ValueError("Heading spans must contain source text")
    if len({(h.span.start, h.span.end) for h in structure.headings}) != len(structure.headings):
        raise ValueError("Heading spans must be unique")
    for table, original in zip(structure.tables, expected):
        check_span(table.span)
        if table.table_id != original.table_id or table.span != original.span or table.rows != original.rows:
            raise ValueError("Table identity and rows must match the original syntax")
        geometry = lambda cell: (cell.cell_id, cell.row, cell.column, cell.rowspan, cell.colspan, cell.span)
        if [geometry(cell) for cell in table.cells] != [geometry(cell) for cell in original.cells]:
            raise ValueError("Table cell geometry must match the original syntax")
        width = max((cell.column + cell.colspan for cell in table.cells), default=0)
        for values, limit in ((table.header_rows, len(table.rows)), (table.row_header_columns, width)):
            if values != sorted(set(values)) or any(value < 0 or value >= limit for value in values):
                raise ValueError("Table header indices must be unique and within the grid")
        for cell, raw in zip(table.cells, original.cells):
            check_span(cell.span, empty=True)
            if cell.is_header != (raw.is_header or cell.row in table.header_rows or cell.column in table.row_header_columns):
                raise ValueError("Cell header flags must agree with declared header indices")
        for span in [*table.captions, *table.footnotes]:
            check_span(span)
        if table.bbox is not None and (table.bbox[0] > table.bbox[2] or table.bbox[1] > table.bbox[3]):
            raise ValueError("Table bounding-box coordinates must be ordered")
