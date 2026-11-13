"""Document reports distinguish original tables from their processing windows."""

from attestextract import ExtractionResult, extract
from attestextract.backends import ReplayBackend
from attestextract.document_routing import table_input_for_chunk
from attestextract.models import DocumentChunk
from attestextract.reporting import render_markdown_report
from attestextract.structure import build_structure
from attestextract.table_windows import split_table_chunk


SOURCE = ("<table><tr><th>Sample</th><th>Mass/g</th></tr>"
          "<tr><td>A</td><td>11</td></tr><tr><td>B</td><td>22</td></tr></table>\n\n"
          "<table><tr><th>Sample</th><th>Mass/g</th></tr><tr><td>C</td><td>33</td></tr></table>")


def report_result(*, split, include_second=False):
    structure = build_structure(SOURCE)
    chunks = []
    for table in structure.tables[:2 if include_second else 1]:
        span = table.span
        root = DocumentChunk(chunk_id=table.table_id, text=SOURCE[span.start:span.end],
                             segments=[span], body_segments=[span])
        chunks.extend(split_table_chunk(SOURCE, root, structure=structure)
                      if split and table.table_id == "table-0001" else [root])
    result = extract("Synthetic source.", prompt_description="Extract reported mass.",
                     field_list={"mass": "Reported mass"},
                     backend=ReplayBackend([{"records": []}], output_protocol="flat-v1")).model_dump(mode="json")
    result.update(schema_version="0.6", table_results=[])
    result["run"]["chunk_count"] = len(chunks)
    for chunk in chunks:
        table = table_input_for_chunk(SOURCE, chunk, structure)
        result["table_results"].append({
            "table_id": table["table_id"], "chunk_id": chunk.chunk_id, "status": "checked",
            "candidates": [], "evidence": {"table_input": table}, "issues": [], "rejections": [],
            "parsed": {"records": []}, "response": {"text": '{"records":[]}'},
        })
    return ExtractionResult.model_validate(result)


def test_unsplit_table_report_keeps_existing_count_and_heading():
    report = render_markdown_report(report_result(split=False))
    assert "- 表格：1；表格记录：0；隔离记录：0；格式状态：complete" in report
    assert "### table\\-0001\n" in report
    assert "细分窗口" not in report and "本窗口负责" not in report


def test_sibling_windows_show_original_table_and_distinct_window_ids():
    result = report_result(split=True)
    before = result.model_dump(mode="json")
    report = render_markdown_report(result)
    assert "- 原表格：1；表格处理块：2；细分窗口：2；" in report
    for number in (1, 2):
        assert f"### 原表 table\\-0001 · 窗口 table\\-0001\\.{number}\n" in report
    assert "### table\\-0001\n" not in report
    assert report.count("本窗口负责的原格") == 2
    assert "完整原表作为共享上下文" in report
    assert result.model_dump(mode="json") == before


def test_mixed_whole_tables_and_windows_have_separate_source_and_window_counts():
    report = render_markdown_report(report_result(split=True, include_second=True))
    assert "- 原表格：2；表格处理块：3；细分窗口：2；" in report
    assert "### table\\-0002\n" in report
