from niceextract.chunking import _protected_positions, _sentence_ends
from niceextract.dataset_filing import FilingContext


def context(source):
    first = source.index("M7")
    bound = {"entity": "M7", "subject_id": "red-sample", "binding_status": "supported",
             "source_evidence": [{"fields": ["entity"], "span": {"start": first, "end": first + 2}}]}
    snapshot = {"objects": [{"object_id": "red-sample", "name": "Red M7"}], "relations": [],
                "evidence": [], "observations": [bound]}
    ends = [0, *_sentence_ends(source, 0, len(source), *_protected_positions(source))]
    return FilingContext(source, snapshot, ends)


def test_same_code_in_two_scopes_does_not_propagate_another_samples_binding():
    for separator in (". At", ", while at"):
        source = f"At Red, M7 absorbed 11 g{separator} Blue, M7 released 19 g."
        index = source.rindex("M7")
        obs = {"entity": "M7", "source_evidence": [{"fields": ["entity"],
                "span": {"start": index, "end": index + 2}}]}
        assert context(source)._context_bound(obs) is None


def test_common_subject_in_one_sentence_can_link_two_measurements():
    source = "M7 absorbed 11 g and released 19 g."
    index = source.index("released")
    obs = {"entity": "M7", "source_evidence": [{"fields": ["entity"],
            "span": {"start": index, "end": len(source) - 1}}]}
    assert context(source)._context_bound(obs) == "red-sample"
