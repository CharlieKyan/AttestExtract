"""Independent source and ownership expectations for D4 context policies."""

import json

import pytest

from attestextract import ContextOptions, Document, ExecutionOptions, extract
from attestextract.archive import create_archive
from attestextract.backends import ReplayBackend
from attestextract.chunking import chunk_document
from attestextract.contextual_chunking import coverage_report, enrich_chunk
from attestextract.diagnostics import RunTrace
from attestextract.evidence import ground_quote
from attestextract.models import DocumentChunk, SourceSpan, TaskSpec
from attestextract.planning import split_chunk
from attestextract.prompting import build_messages
from attestextract.structure import build_structure


TASK = TaskSpec.model_validate({"name": "Synthetic measurement", "record_types": {"reading": {
    "fields": {"subject": {"type": "string"}, "value": {"type": "string"}, "condition": {"type": "string"}},
    "identity_fields": ["subject"], "condition_fields": ["condition"]}}})


def body(text, value):
    start = text.index(value)
    return DocumentChunk(chunk_id="chunk-0001", text=value,
                         segments=[SourceSpan(start=start, end=start + len(value))])


def test_none_preserves_original_split_and_prompt():
    source = "# A\n\nFirst sample.\n\nSecond sample.\n"
    old = chunk_document(source, max_chars=18)
    new = chunk_document(source, max_chars=18, context=ContextOptions())
    assert old == new
    assert build_messages(TASK, old[0]) == build_messages(TASK, new[0])


def test_heading_and_whole_adjacent_paragraphs_stay_in_section():
    source = "# Sample A\n\nAt 20 C.\n\nValue is 12 m.\n\nFollow up.\n\n# Sample B\n\nAt 90 C.\n"
    chunk = enrich_chunk(source, body(source, "Value is 12 m."), build_structure(source), ContextOptions(mode="neighbors"))
    roles = {item.role: source[item.span.start:item.span.end] for item in chunk.context_segments}
    assert roles == {"heading": "# Sample A\n", "neighbor_before": "At 20 C.", "neighbor_after": "Follow up."}
    assert "Sample B" not in chunk.text and "90 C" not in chunk.text
    assert ground_quote(source, chunk, "At 20 C.").status == "matched"
    payload = json.loads(build_messages(TASK, chunk)[1]["content"])
    assert payload["source"][0] == {"role": "body", "text": "Value is 12 m."}


def test_neighbor_allowance_keeps_complete_sentence_or_reports_omission():
    source = "# A\n\nLongFirstSentenceWithoutBreak. Short.\n\nValue.\n\nSupercalifragilistic.\n"
    chunk = enrich_chunk(source, body(source, "Value."), build_structure(source),
                         ContextOptions(mode="neighbors", neighbor_chars=8))
    neighbors = {item.role: source[item.span.start:item.span.end] for item in chunk.context_segments}
    assert neighbors["neighbor_before"] == "Short."
    assert "neighbor_after" not in neighbors
    assert "neighbor_after_exceeds_sentence_budget" in chunk.context_diagnostics


def test_noncontiguous_body_and_heading_do_not_manufacture_quote():
    source = "# A\n\nExcluded.\n\nValue."
    chunk = enrich_chunk(source, body(source, "Value."), build_structure(source), ContextOptions(mode="structure"))
    assert ground_quote(source, chunk, "# A\nValue.").status == "unmatched"
    assert ground_quote(source, chunk, "Excluded.").status == "unmatched"


def test_recursive_context_splits_keep_exact_body_coverage():
    source = "# Sample A\n\n" + "".join(f"Sentence {i} has value.\n\n" for i in range(8))
    structure = build_structure(source)
    parents = chunk_document(source, max_chars=140, structure=structure, context=ContextOptions(mode="neighbors", neighbor_chars=30))
    assert coverage_report(source, parents)["uncovered"] == []
    leaves = []
    for parent in parents:
        children = split_chunk(source, parent, min_chars=5, structure=structure)
        leaves.extend(children or [parent])
        for child in children:
            assert child.context_policy == parent.context_policy
            assert sum(s.end-s.start for s in child.body_segments) < sum(s.end-s.start for s in parent.body_segments)
    assert coverage_report(source, leaves)["uncovered"] == []
    assert coverage_report(source, leaves)["repeated_body_characters"] == 0


def test_html_rowspan_is_atomic_and_header_inherited():
    source = '<table><tr><th>Sample</th><th>Pressure MPa</th></tr><tr><td rowspan="2">A</td><td>12</td></tr><tr><td>13</td></tr><tr><td>B</td><td>24</td></tr></table>'
    structure = build_structure(source)
    chunks = chunk_document(source, structure=structure, context=ContextOptions(mode="structure"), max_chars=60, table_rows_per_chunk=1)
    assert any('rowspan="2"' in chunk.text and "<td>13</td>" in chunk.text for chunk in chunks)
    for chunk in chunks:
        if "<td>24</td>" in chunk.text:
            assert "Pressure MPa" in chunk.text
            assert any(item.role == "table_header" for item in chunk.context_segments)
    assert coverage_report(source, chunks)["uncovered"] == []


def test_coverage_rejects_gap_and_fabricated_view():
    source = "Alpha. Beta."
    with pytest.raises(ValueError, match="uncovered"):
        coverage_report(source, [body(source, "Beta.")])
    bad = body(source, source).model_copy(update={"text": "made up"})
    with pytest.raises(ValueError, match="reconstructed"):
        coverage_report(source, [bad])


def test_context_only_record_stays_reviewable_in_archive():
    source = "# Sample A\n\nAt 20 C.\n\nValue is 12 m.\n"
    # Each source part is repeated deliberately by the replay; context-only
    # candidates must not silently acquire acceptance through source matching.
    document = Document.from_text(source, structure=build_structure(source))
    chunks = chunk_document(source, max_chars=20, structure=document.structure, context=ContextOptions(mode="neighbors"))
    response = {"records": [{"record_type": "reading", "values": {"subject": "A", "value": "12", "condition": "20 C"},
                              "quotes": {"subject": "Sample A", "value": "12", "condition": "20 C"}}]}
    result = extract(document, TASK, backend=ReplayBackend([response] * len(chunks)),
                     max_chunk_chars=20, context=ContextOptions(mode="neighbors"))
    archive = create_archive(document, result)
    assert not archive.records
    assert any(item.code == "context_only_candidate" for item in result.review)


def test_checkpoint_binds_context_policy(tmp_path):
    source = "# A\n\nValue.\n"
    trace = RunTrace(tmp_path / "first")
    extract(source, TASK, backend=ReplayBackend([{"records": []}] * 4), trace=trace,
            execution=ExecutionOptions(adaptive_chunking=False), backend_identity="synthetic",
            context=ContextOptions(mode="structure"))
    with pytest.raises(Exception, match="identity mismatch"):
        extract(source, TASK, backend=ReplayBackend([]), trace=RunTrace(tmp_path / "second"),
                execution=ExecutionOptions(adaptive_chunking=False), backend_identity="synthetic",
                context=ContextOptions(mode="neighbors"), resume_from=tmp_path / "first")


def test_oversized_heading_stays_whole_and_every_displayed_heading_is_locatable():
    heading = "# A very long heading with several words\n"
    source = heading + "\nBody text.\n"
    structure = build_structure(source)
    chunks = chunk_document(source, max_chars=20, context=ContextOptions(mode="neighbors", neighbor_chars=20))
    assert len(chunks) == 1
    assert chunks[0].text == source
    assert ground_quote(source, chunks[0], heading).status == "matched"
    assert coverage_report(source, chunks, max_chars=20)["oversized_chunks"] == ["chunk-0001"]
    # The complete context passage remains searchable even when the owned body
    # is a smaller range inside it. No concatenated artificial quote is needed.
    overlap = enrich_chunk(source, body(source, "heading"), structure, ContextOptions(mode="structure"))
    payload = json.loads(build_messages(TASK, overlap)[1]["content"])
    assert {"role": "heading", "text": heading} in payload["source"]
    assert ground_quote(source, overlap, heading).status == "matched"


def test_neighbor_prose_cannot_jump_over_an_intervening_table():
    source = ("Before paragraph.\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
              "\nAfter paragraph.\n")
    chunks = chunk_document(source, max_chars=100, table_rows_per_chunk=1,
                            context=ContextOptions(mode="neighbors", neighbor_chars=20))
    before = next(chunk for chunk in chunks if any(
        "Before paragraph." in source[span.start:span.end] for span in chunk.body_segments))
    after = next(chunk for chunk in chunks if any(
        "After paragraph." in source[span.start:span.end] for span in chunk.body_segments))
    assert not any(item.role == "neighbor_after" for item in before.context_segments)
    assert not any(item.role == "neighbor_before" for item in after.context_segments)
    assert "After paragraph." not in before.text
    assert "Before paragraph." not in after.text


def test_leading_whitespace_and_table_header_belong_to_first_data_batch():
    header = "| A | B |\n| --- | --- |\n"
    source = "\n\n" + header + "| 1 | 2 |\n| 3 | 4 |\n"
    chunks = chunk_document(source, max_chars=100, table_rows_per_chunk=1,
                            context=ContextOptions(mode="structure"))
    assert len(chunks) == 2
    assert ["".join(source[span.start:span.end] for span in chunk.body_segments) for chunk in chunks] == [
        "\n\n" + header + "| 1 | 2 |\n", "| 3 | 4 |\n",
    ]
    assert [(item.role, source[item.span.start:item.span.end]) for item in chunks[1].context_segments] == [
        ("table_header", "| A | B |\n"),
    ]
    report = coverage_report(source, chunks)
    assert report["uncovered"] == []
    assert report["repeated_body_characters"] == 0


def test_normalized_body_evidence_is_not_mislabeled_as_context_only():
    source = "# Sample A\n\nValue is 12， at 20 C.\n"
    response = {"records": [{"record_type": "reading",
        "values": {"subject": "A", "value": "12", "condition": "20 C"},
        "quotes": {"subject": "Sample A", "value": "12,", "condition": "20 C"},
    }]}
    result = extract(source, TASK, backend=ReplayBackend([response]), context=ContextOptions(mode="structure"))
    candidate = result.candidates[0]
    assert candidate.fields["value"].evidence.status == "normalized"
    assert candidate.fields["value"].evidence.source_matches[0].source_quote == "12，"
    assert candidate.needs_review
    assert any(check.code == "body_evidence" and check.passed for check in candidate.checks)
    assert not any(item.code == "context_only_candidate" for item in result.review)
    assert any(item.code == "evidence_normalized" for item in result.review)
