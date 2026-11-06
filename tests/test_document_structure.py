"""Synthetic source fixtures with hand-specified grid and provenance expectations."""

import hashlib

import pytest

from attestextract.documents import Document
from attestextract.models import SourceSpan
from attestextract.structure import build_structure, validate_structure


def content(text, span):
    return text[span.start:span.end]


@pytest.mark.parametrize("outer", [True, False])
def test_pipe_table_keeps_escaped_pipe_in_cell_and_accepts_optional_outer_pipes(outer):
    lines = [r"Label \| type | Pressure (MPa) | Force (N)", "--- | --- | ---", "A | 12 | 24"]
    if outer:
        lines = ["| " + line + " |" for line in lines]
    text = "前言🙂\r\n\r\n" + "\r\n".join(lines) + "\r\n"
    structure = build_structure(text)
    table = structure.tables[0]
    assert table.table_id == "table-0001"
    assert len(table.rows) == 2
    assert table.header_rows == [0]
    assert table.row_header_columns == []
    assert [(cell.row, cell.column, content(text, cell.span), cell.is_header) for cell in table.cells] == [
        (0, 0, r"Label \| type", True), (0, 1, "Pressure (MPa)", True), (0, 2, "Force (N)", True),
        (1, 0, "A", False), (1, 1, "12", False), (1, 2, "24", False),
    ]
    assert table.cells[-1].cell_id == "table-0001-r0001-c0002"
    assert content(text, table.rows[-1]) == lines[-1] + "\r\n"
    assert content(text, table.span) == "\r\n".join(lines) + "\r\n"
    assert structure.diagnostics == []


def test_markdown_table_preserves_empty_cells_and_backslash_parity():
    text = "| Name | Value |\n| --- | --- |\n| A | |\n| B\\\\| 2 |\n"
    table = build_structure(text).tables[0]
    assert [(cell.row, cell.column, content(text, cell.span)) for cell in table.cells[-4:]] == [
        (1, 0, "A"), (1, 1, ""), (2, 0, "B\\\\"), (2, 1, "2"),
    ]


@pytest.mark.parametrize("text", [
    "| A | B |\n| --- | --- |\n| 1 |\n",
    "| A | B |\n| --- | --- | --- |\n| 1 | 2 |\n",
])
def test_wrong_markdown_width_retains_outer_source_without_usable_grid(text):
    structure = build_structure(text)
    assert len(structure.tables) == 1
    assert content(text, structure.tables[0].span) == text
    assert structure.tables[0].cells == structure.tables[0].rows == []
    assert structure.diagnostics == ["table-0001:invalid_markdown_column_count"]


def test_html_grid_keeps_raw_markup_and_merged_cell_coordinates():
    text = ('前🙂\r\n<table><caption>Table A</caption><thead>'
            '<tr><th rowspan="2">Sample</th><th colspan="2">Reading</th></tr>'
            '<tr><th>P (MPa)</th><th>T (K)</th></tr></thead><tbody>'
            '<tr><th scope="row">A</th><td><b>12</b>&nbsp;</td><td>300</td></tr>'
            '</tbody></table>后')
    table = build_structure(text).tables[0]
    assert table.header_rows == [0, 1]
    assert table.row_header_columns == [0]
    assert len(table.rows) == 3
    assert [(cell.row, cell.column, cell.rowspan, cell.colspan, content(text, cell.span)) for cell in table.cells] == [
        (0, 0, 2, 1, "Sample"), (0, 1, 1, 2, "Reading"),
        (1, 1, 1, 1, "P (MPa)"), (1, 2, 1, 1, "T (K)"),
        (2, 0, 1, 1, "A"), (2, 1, 1, 1, "<b>12</b>&nbsp;"), (2, 2, 1, 1, "300"),
    ]
    assert content(text, table.rows[1]) == "<tr><th>P (MPa)</th><th>T (K)</th></tr>"
    assert [content(text, span) for span in table.captions] == ["Table A"]
    assert content(text, table.span).startswith("<table>")
    assert content(text, table.span).endswith("</table>")


def test_all_td_requires_explicit_header_hints_and_thead_is_explicit():
    text = "<table><tr><td>Sample</td><td>P (MPa)</td></tr><tr><td>A</td><td>12</td></tr></table>"
    unspecified = build_structure(text)
    assert unspecified.tables[0].header_rows == []
    assert all(not cell.is_header for cell in unspecified.tables[0].cells)
    assert unspecified.diagnostics == ["table-0001:header_rows_unspecified"]
    hinted = build_structure(text, hints={"tables": {"table-0001": {
        "header_rows": [0], "row_header_columns": [0],
    }}})
    assert hinted.tables[0].header_rows == [0]
    assert [cell.is_header for cell in hinted.tables[0].cells] == [True, True, True, False]
    assert hinted.diagnostics == []
    explicit = "<table><thead><tr><td>P (MPa)</td></tr></thead><tr><td>12</td></tr></table>"
    assert build_structure(explicit).tables[0].header_rows == [0]


@pytest.mark.parametrize("body,diagnostic", [
    ('<tr><td rowspan="9">A</td></tr>', "html_rowspan_out_of_bounds"),
    ('<tr><td>A</td><td>B</td></tr><tr><td>C</td></tr>', "incomplete_html_grid"),
    ('<tr><td colspan="0">A</td></tr>', "invalid_html_span"),
    ('<tr><td>A</th></tr>', "mismatched_html_cell_end"),
    ('<tr><td>A<table><tr><td>B</td></tr></table></td></tr>', "nested_html_table"),
    ('<tr><td colspan="999999999999">A</td></tr>', "html_span_exceeds_source_size"),
])
def test_malformed_html_never_exposes_a_plausible_cell_grid(body, diagnostic):
    text = "<table>" + body + "</table>"
    structure = build_structure(text)
    table = structure.tables[0]
    assert table.cells == table.rows == []
    assert content(text, table.span) == text
    assert f"table-0001:{diagnostic}" in structure.diagnostics


def test_unclosed_table_is_preserved_as_one_unresolved_source_span():
    text = "Before\n<table><tr><td>A</td></tr>"
    structure = build_structure(text)
    assert content(text, structure.tables[0].span) == "<table><tr><td>A</td></tr>"
    assert structure.tables[0].cells == []
    assert "table-0001:unclosed_html_table" in structure.diagnostics


def test_heading_syntax_and_code_blocks_do_not_create_false_tables_or_headings():
    text = ("# Results\r\n\r\nDetail\r\n------\r\n\r\n"
            "```text\n# Example\n| A | B |\n| --- | --- |\n| 1 | 2 |\n```\n"
            "## Method ##\n")
    structure = build_structure(text)
    assert [(heading.level, content(text, heading.span)) for heading in structure.headings] == [
        (1, "# Results\r\n"), (2, "Detail\r\n------\r\n"), (2, "## Method ##\n"),
    ]
    assert structure.tables == []


def test_unclosed_html_example_in_fence_cannot_hide_a_real_table():
    text = "```html\n<table>\n```\n<table><tr><td>A</td></tr></table>"
    tables = build_structure(text).tables
    assert len(tables) == 1
    assert content(text, tables[0].span) == "<table><tr><td>A</td></tr></table>"
    assert content(text, tables[0].cells[0].span) == "A"


def test_mineru_metadata_only_attaches_through_unique_exact_source_matches():
    body = "<table><tr><td>P (MPa)</td></tr><tr><td>12</td></tr></table>"
    text = "Results\n\nTable A. Dry sample\n" + body + "\nNote: at 300 K.\n"
    mineru = [{"type": "text", "text": "Results", "text_level": 1},
              {"type": "table", "table_body": body, "table_caption": ["Table A. Dry sample"],
               "table_footnote": ["Note: at 300 K."], "page_idx": 2, "bbox": [10, 20, 90, 70]}]
    structure = build_structure(text, mineru_content=mineru)
    table = structure.tables[0]
    assert [(heading.level, content(text, heading.span)) for heading in structure.headings] == [(1, "Results")]
    assert table.page_idx == 2
    assert table.bbox == [10.0, 20.0, 90.0, 70.0]
    assert [content(text, span) for span in table.captions] == ["Table A. Dry sample"]
    assert [content(text, span) for span in table.footnotes] == ["Note: at 300 K."]
    assert structure.source_sha256 is not None
    assert structure.text_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    duplicate = build_structure(text + body, mineru_content=mineru)
    assert all(table.page_idx is None for table in duplicate.tables)
    assert "mineru[1]:table_body_not_uniquely_aligned" in duplicate.diagnostics


def test_mineru_does_not_match_rewritten_html_or_repeated_captions():
    body = "<table><tr><td>A</td></tr></table>"
    text = "Caption\n" + body + "\nCaption\n"
    data = [{"type": "table", "table_body": body, "table_caption": ["Caption"]}]
    indexed = build_structure(text, mineru_content=data)
    assert indexed.tables[0].captions == []
    assert "mineru[0]:table_caption_not_uniquely_aligned" in indexed.diagnostics
    data[0]["table_body"] = body.replace("<td>", "<td >")
    assert "mineru[0]:table_body_not_uniquely_aligned" in build_structure(text, mineru_content=data).diagnostics
    data[0]["table_body"] = body + "\nCaption\n"
    assert "mineru[0]:table_body_not_uniquely_aligned" in build_structure(text, mineru_content=data).diagnostics


@pytest.mark.parametrize("hints", [
    {"tables": {"table-0002": {"header_rows": [0]}}},
    {"tables": {"table-0001": {"header_rows": [2]}}},
    {"tables": {"table-0001": {"header_rows": [True]}}},
    {"tables": {"table-0001": {"row_header_columns": [0, 0]}}},
])
def test_invalid_hints_fail_explicitly(hints):
    with pytest.raises(ValueError):
        build_structure("<table><tr><td>A</td></tr></table>", hints=hints)


def test_document_structure_roundtrip_preserves_original_document_identity():
    text = "# Trial\r\n\r\n| A | B |\r\n| --- | --- |\r\n| 1 | 2 |\r\n"
    original = Document.from_text(text)
    indexed = Document.from_text(text, structure=build_structure(text))
    assert original.document_id == indexed.document_id
    assert original.text == indexed.text
    assert Document.model_validate_json(indexed.model_dump_json()) == indexed
    old_payload = original.model_dump()
    del old_payload["structure"]
    assert Document.model_validate(old_payload).structure is None


@pytest.mark.parametrize("change", ["text_hash", "cell", "row", "heading", "grid"])
def test_stale_or_tampered_structure_is_rejected(change):
    text = "# Trial\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    structure = build_structure(text)
    if change == "text_hash":
        structure.text_sha256 = "0" * 64
    elif change == "cell":
        structure.tables[0].cells[-1].span = SourceSpan(start=0, end=1)
    elif change == "row":
        structure.tables[0].rows[-1].end -= 1
    elif change == "heading":
        structure.headings[0].span.end = len(text) + 1
    else:
        structure.tables[0].cells[-1].column = 0
    with pytest.raises(ValueError):
        Document.from_text(text, structure=structure)
    with pytest.raises(ValueError):
        validate_structure(text, structure)
