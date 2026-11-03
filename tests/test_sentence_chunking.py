"""Hand-specified boundaries and original-source invariants for sentence chunks."""

import pytest

from attestextract.chunking import chunk_document
from attestextract.models import ExecutionOptions, GenerationOptions, TaskSpec
from attestextract.planning import plan_chunks, split_chunk


@pytest.mark.parametrize("source,limit,expected", [
    ("Alpha is stable. Beta is stable. Gamma is stable.", 20,
     ["Alpha is stable. ", "Beta is stable. ", "Gamma is stable."]),
    ("样品甲稳定。样品乙稳定。样品丙稳定。", 10,
     ["样品甲稳定。", "样品乙稳定。", "样品丙稳定。"]),
    ('甲说：“样品稳定！”乙说：“继续。”', 11, ['甲说：“样品稳定！”', '乙说：“继续。”']),
    ("Dr. Chen measured 12.75 MPa. Next sample passed.", 28,
     ["Dr. Chen measured 12.75 MPa. ", "Next sample passed."]),
    ("See Fig. 2 for data. Results follow.", 20,
     ["See Fig. 2 for data. ", "Results follow."]),
    ("See e.g. the sample. Next result.", 21,
     ["See e.g. the sample. ", "Next result."]),
    ("First line\r\nsecond line and more", 17,
     ["First line\r\n", "second line and ", "more"]),
    ("first clause, second clause; third clause.", 18,
     ["first clause, ", "second clause; ", "third clause."]),
    ("Pressure was 12.75 MPa at the initial state.", 18,
     ["Pressure was ", "12.75 MPa at the ", "initial state."]),
    ("无空格中文内容过长需要继续按字符切分", 8,
     ["无空格中文内容过", "长需要继续按字符", "切分"]),
    ("Alpha.\r\n\r\nBeta.\r\n\r\nGamma.", 10,
     ["Alpha.\r\n\r\n", "Beta.\r\n\r\n", "Gamma."]),
    ("abc.5", 3, ["abc", ".5"]),
    ("Done!.5 MPa.", 6, ["Done!", ".5 ", "MPa."]),
    ("abc,.75 MPa", 4, ["abc,", ".75 ", "MPa"]),
    ("prefix .123456 tail", 7, ["prefix ", ".123456 ", "tail"]),
    ("前。\u0301后", 2, ["前", "。\u0301", "后"]),
])
def test_readable_units_have_expected_boundaries(source, limit, expected):
    chunks = chunk_document(source, max_chars=limit)
    assert [chunk.text for chunk in chunks] == expected
    assert "".join(chunk.text for chunk in chunks) == source
    for chunk in chunks:
        assert chunk.text == "".join(source[span.start:span.end] for span in chunk.segments)


@pytest.mark.parametrize("atom", [
    "123456789.012345", "-1.23456789e-123", "Supercalifragilistic",
    r"$p = 12.75 \times 10^{3}$", r"\(a + b = 12.75\)",
    "$$\r\np = 12.75\r\n\r\nq = 3\r\n$$",
    "\\[\np = 12.75\n\nq = 3\n\\]",
    "\\begin{align}\np = 1\n\nq = 2\n\\end{align}",
])
def test_oversized_atoms_remain_whole_and_cannot_be_subdivided(atom):
    chunks = chunk_document(atom, max_chars=6)
    assert len(chunks) == 1 and chunks[0].text == atom
    assert split_chunk(atom, chunks[0], min_chars=1) == []
    surrounded = "Before " + atom + " after."
    pieces = chunk_document(surrounded, max_chars=10)
    assert sum(atom in piece.text for piece in pieces) == 1
    assert "".join(piece.text for piece in pieces) == surrounded


@pytest.mark.parametrize("source,limit", [("前🙂后a\u0301末", 1), ("前👩\u200d🔬后", 2), ("前👍🏽后", 2)])
def test_unicode_clusters_are_not_split(source, limit):
    chunks = chunk_document(source, max_chars=limit)
    assert "".join(chunk.text for chunk in chunks) == source
    for cluster in ("a\u0301", "👩\u200d🔬", "👍🏽"):
        if cluster in source:
            assert any(cluster in chunk.text for chunk in chunks)


def test_truncation_subdivision_uses_sentence_boundaries():
    source = "Alpha is stable. Beta is stable. Gamma is stable."
    parent = chunk_document(source)[0]
    children = split_chunk(source, parent, min_chars=1)
    assert [child.text for child in children] == ["Alpha is stable. ", "Beta is stable. ", "Gamma is stable."]
    assert all(len(child.text) < len(parent.text) for child in children)


def test_repeated_table_headers_and_recursive_splits_keep_source_spans():
    header = "| Sample | Amount |\r\n| --- | --- |\r\n"
    rows = [f"| Sample {index} | {index}.75 |\r\n" for index in range(6)]
    source = "前言🙂。\r\n\r\n" + header + "".join(rows)
    parents = chunk_document(source, max_chars=110, table_rows_per_chunk=2)
    queue = list(parents)
    while queue:
        parent = queue.pop(0)
        assert parent.text == "".join(source[s.start:s.end] for s in parent.segments)
        for row in rows:
            if f"Sample {rows.index(row)}" in parent.text:
                assert row in parent.text and header in parent.text
        children = split_chunk(source, parent, min_chars=1)
        if children:
            assert all(len(child.text) < len(parent.text) for child in children)
            assert {p for child in children for s in child.segments for p in range(s.start, s.end)} == {
                p for s in parent.segments for p in range(s.start, s.end)}
            queue.extend(children)


def test_complexity_planning_preserves_sentences_and_source_coverage():
    sentences = [f"Sample {index} has amount {index + 10}. " for index in range(30)]
    source = "".join(sentences)
    task = TaskSpec.model_validate({"name": "Synthetic", "record_types": {
        "reading": {"fields": {"subject": {"type": "string"}, "amount": {"type": "number"}}}}})
    chunks = plan_chunks(source, task, GenerationOptions(max_output_tokens=2048),
                         ExecutionOptions(min_chunk_chars=40))
    assert len(chunks) > 1
    assert "".join(chunk.text for chunk in chunks) == source
    assert all(sum(sentence in chunk.text for chunk in chunks) == 1 for sentence in sentences)


def test_trailing_whitespace_does_not_create_its_own_chunk():
    source = "Alpha. "
    assert [chunk.text for chunk in chunk_document(source, max_chars=6)] == [source]
