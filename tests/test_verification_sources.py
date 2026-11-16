"""Original text remains traceable when model-guided reading is bounded."""

import pytest

from attestextract.verification_sources import SourceIndex


def assert_original(text, passages):
    for passage in passages:
        assert text[passage["start"]:passage["end"]] == passage["text"]


def test_long_document_is_indexed_without_loss_or_60000_character_limit():
    text = ("Specimen A belongs to batch X. Its measured value is 17 units.\n\n" * 1200
            + "Final specimen Z belongs to batch Y.")
    index = SourceIndex(text)
    assert len(text) > 60000
    assert "".join(passage["text"] for passage in index.passages) == text
    assert all(len(passage["text"]) <= 1600 for passage in index.passages)
    hit = index.search("Final specimen Z")["matches"][0]
    assert hit["match_start"] == text.index("Final specimen Z")
    assert_original(text, [hit])
    assert index.get(hit["passage_id"])["end"] == len(text)


def test_search_paginates_occurrences_without_claiming_all_were_read():
    text = "Reactor R yields 12%.\n\n" * 9
    index = SourceIndex(text)
    first = index.search("reactor r", limit=3)
    second = index.search("reactor r", offset=first["next_offset"], limit=3)
    third = index.search("reactor r", offset=second["next_offset"], limit=3)
    assert first["total"] == 9
    assert first["has_more"] and second["has_more"]
    assert not third["has_more"] and third["next_offset"] is None
    results = first["matches"] + second["matches"] + third["matches"]
    assert len({item["match_start"] for item in results}) == 9
    assert_original(text, results)
    assert index.search("absent")['total'] == 0
    assert index.search("reactor", offset=99)["matches"] == []


def test_casefold_expansion_preserves_original_unicode_offsets():
    text = "前文😀。 Straße 取样记录。\n\nSTRASSE 的另一处记录。"
    index = SourceIndex(text)
    result = index.search("strasse")
    assert result["total"] == 2
    assert [text[item["match_start"]:item["match_end"]] for item in result["matches"]] == ["Straße", "STRASSE"]
    assert_original(text, result["matches"])
    assert SourceIndex("ß").search("s")["total"] == 0
    assert SourceIndex("ß").search("ss")["total"] == 1


def test_search_is_literal_and_snippets_are_bounded():
    text = "a" * 700 + "[.*]" + "b" * 700
    hit = SourceIndex(text).search("[.*]")["matches"][0]
    assert len(hit["text"]) <= 400
    assert hit["match_start"] == 700
    assert_original(text, [hit])


def test_search_across_passage_boundary_exposes_continuation_ids():
    text = "alpha\n\nbeta"
    index = SourceIndex(text)
    result = index.search("alpha\n\nbeta")
    assert result["total"] == 1
    match = result["matches"][0]
    assert match["continuation_passage_ids"] == [index.passages[1]["passage_id"]]
    assert_original(text, [match])
    assert match["end"] == len("alpha\n\n")


def test_reads_prioritize_requested_passages_and_report_budget_omissions():
    text = "A" * 50 + "\n\n" + "B" * 50 + "\n\n" + "C" * 50
    index = SourceIndex(text)
    requested = index.passages[1]["passage_id"]
    result = index.read([requested], neighbors=1, max_chars=60)
    assert [item["passage_id"] for item in result["passages"]] == [requested]
    assert result["omitted_passage_ids"] == [index.passages[0]["passage_id"], index.passages[2]["passage_id"]]
    assert result["has_more"]
    assert result["returned_characters"] == 52
    assert_original(text, result["passages"])
    too_small = index.read([requested], max_chars=1)
    assert too_small["passages"] == []
    assert too_small["omitted_passage_ids"] == [requested]


def test_nearby_spans_cover_their_original_passages_without_rejoining():
    text = "Sample A.\n\nSample B.\n\nSample C."
    index = SourceIndex(text)
    start = text.index("B")
    result = index.nearby([{"start": start, "end": len(text)}])
    assert [item["text"] for item in result["passages"]] == ["Sample B.\n\n", "Sample C."]
    assert_original(text, result["passages"])
    assert len(index.nearby([])["passages"]) == 1


def test_long_unpunctuated_units_are_explicitly_marked_as_split():
    text = "x" * 4000
    passages = SourceIndex(text).passages
    assert [len(passage["text"]) for passage in passages] == [1600, 1600, 800]
    assert passages[0]["forced_split"] and passages[1]["forced_split"]
    assert all(passage["partial_paragraph"] for passage in passages)
    assert_original(text, passages)


def test_sentence_boundaries_are_preferred_to_arbitrary_cuts():
    text = "甲" * 899 + "。" + "乙" * 899 + "。"
    passages = SourceIndex(text).passages
    assert [len(passage["text"]) for passage in passages] == [900, 900]
    assert not any(passage["forced_split"] for passage in passages)


def test_single_long_paragraph_retains_complete_sentences_and_all_source_text():
    sentence = "Specimen A has mass 12 g. "
    text = sentence * 5000
    passages = SourceIndex(text).passages
    assert len(text) > 100000
    assert "".join(passage["text"] for passage in passages) == text
    assert len(passages) > 50
    assert all(len(passage["text"]) <= 1600 for passage in passages)
    assert all(passage["text"].endswith(". ") for passage in passages)
    assert all(passage["text"].replace(sentence, "") == "" for passage in passages)
    assert not any(passage["forced_split"] for passage in passages)
    assert_original(text, passages)


def test_structure_hints_are_existing_passage_ids_not_invented_quotes():
    text = "# Measurements\n\n| Sample | Value |\n| --- | --- |\n" + "| A | 17 |\n" * 220
    index = SourceIndex(text)
    result = index.search("| A |", offset=180, limit=1)
    passage = index.get(result["matches"][0]["passage_id"])
    assert passage["kind"] == "table"
    assert passage["context_passage_ids"]
    hints = [index.get(key) for key in passage["context_passage_ids"]]
    assert any("| Sample | Value |" in item["text"] for item in hints)
    assert_original(text, hints)


@pytest.mark.parametrize("query", [None, "", "  ", "x" * 257, 7])
def test_invalid_search_queries_fail_locally(query):
    with pytest.raises(ValueError):
        SourceIndex("A").search(query)


@pytest.mark.parametrize("kwargs", [{"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": 13}])
def test_search_bounds_reject_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        SourceIndex("A").search("A", **kwargs)


@pytest.mark.parametrize("ids,kwargs", [([], {}), (["missing"], {}), (["passage-000001", "missing"], {}),
                                       (["passage-000001"] * 4, {}), (["passage-000001"], {"neighbors": 2}),
                                       (["passage-000001"], {"max_chars": 0})])
def test_invalid_reads_fail_before_returning_partial_results(ids, kwargs):
    with pytest.raises(ValueError):
        SourceIndex("A").read(ids, **kwargs)


@pytest.mark.parametrize("span", [{"start": -1, "end": 1}, {"start": 0, "end": 4},
                                  {"start": True, "end": 2}, {"start": 2, "end": 1}, "invalid"])
def test_nearby_rejects_bad_source_positions(span):
    with pytest.raises(ValueError):
        SourceIndex("ABC").nearby([span])


def test_index_is_deterministic_and_public_get_results_cannot_modify_it():
    first = SourceIndex("A.\n\nB.")
    second = SourceIndex("A.\n\nB.")
    assert first.passages == second.passages
    item = first.get("passage-000001")
    item["text"] = "changed"
    assert first.get("passage-000001")["text"] == "A.\n\n"
