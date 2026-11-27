"""Regression cases for scoped reread annotations on immutable original measurements."""

from copy import deepcopy
from hashlib import sha256

import pytest

from niceextract.dataset_reading_review import apply_reading_review


SOURCE = "A specimen from station Willow had a depth range of 22–27 m."
REVIEW = {"source_sha256": sha256(SOURCE.encode()).hexdigest(), "reviewed_by": "test reader"}
ENTRY = {"entity": "specimen", "field": "top", "value": 22, "unit": "m", "target": "Willow",
         "statement_basis": "inferred", "reported_use": "measured",
         "reason": "The text gives a depth interval.", "quote": SOURCE, "start": 0, "end": len(SOURCE)}


def test_reread_preserves_source_range_and_prior_filing_on_disagreement():
    details = []
    apply_reading_review(SOURCE, details, {**REVIEW, "readings": [ENTRY]}, {"top"}, {})
    obs, = details
    assert obs["statement_basis"] == "inferred"
    assert obs["citation_status"] == "located"
    assert obs["target"]["label"] == "Willow" and obs["direct"]["label"] == "specimen"
    previous = deepcopy(obs["filing_subjects"])
    second = {**ENTRY, "observation_id": obs["observation_id"], "target": "specimen"}
    apply_reading_review(SOURCE, details, {**REVIEW, "readings": [second]}, {"top"}, {})
    assert obs["filing_subjects"] == previous
    assert obs["filing_disagreement"]["reread_suggestion"]["label"] == "specimen"
    assert len(obs["reading_reviews"]) == 2
    assert len(details) == 1 and obs["value"] == 22


def test_reread_cannot_change_value_or_attach_to_another_document():
    details = []
    packet = {**REVIEW, "readings": [ENTRY]}
    apply_reading_review(SOURCE, details, packet, {"top"}, {})
    oid = details[0]["observation_id"]
    with pytest.raises(ValueError, match="unmodified"):
        apply_reading_review(SOURCE, details, {**REVIEW, "readings": [{**ENTRY, "observation_id": oid, "value": 27}]},
                             {"top"}, {})
    with pytest.raises(ValueError, match="different document"):
        apply_reading_review(SOURCE + " ", [], packet, {"top"}, {})
    with pytest.raises(ValueError, match="duplicates"):
        apply_reading_review(SOURCE, details, packet, {"top"}, {})


@pytest.mark.parametrize("applicability", ["uncertain", "outside"])
def test_contested_filing_without_replacement_is_still_visible_as_disagreement(applicability):
    details = []
    apply_reading_review(SOURCE, details, {**REVIEW, "readings": [ENTRY]}, {"top"}, {})
    obs, = details
    entry = {**ENTRY, "observation_id": obs["observation_id"], "target": None, "row_applicability": applicability}
    apply_reading_review(SOURCE, details, {**REVIEW, "readings": [entry]}, {"top"}, {})
    assert obs["filing_disagreement"] and not obs["eligible"]
    assert obs["filing_subjects"][0]["label"] == "Willow"
