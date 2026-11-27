"""A real source can support a value while explicitly ruling out its proposed field."""

import json
from hashlib import sha256

import pytest

from niceextract import BackendResponse, assemble_dataset, extract
from niceextract.dataset_admission import apply_field_verdict
from test_source_statement_protocol import FixtureBackend


TEXT = "For pump P, the design pressure is 18 kPa. The measured pressure was not reported."


@pytest.mark.parametrize("protocol", ["source-v1", "source-v2"])
def test_field_rejection_and_correction_are_independent_of_ownership(protocol):
    record = {"record_type": "pressure", "name_handling": "uncertain", "statement_basis": "reported",
              "source_property": "design pressure", "reported_use": "model_input",
              "values": {"entity": "pump P", "value": "18", "unit": "kPa", "conditions": None},
              "quotes": {"entity": TEXT, "value": TEXT, "unit": TEXT, "conditions": ""}}

    class Reviewer(FixtureBackend):
        def generate(self, request):
            if not request.chunk_id.startswith("verify-"):
                return super().generate(request)
            payload = json.loads(request.messages[-1]["content"])
            obs, = payload["observations"]
            quote = {"passage_id": payload["initial_context"]["passages"][0]["passage_id"], "quote": TEXT}
            return BackendResponse(text=json.dumps({"action": "decide", "question": "Was this measured?",
                "objects": [], "relations": [], "bindings": [
                    {"observation_id": obs["observation_id"], "subject_id": None, "status": "unknown",
                     "evidence": [quote], "reason": "The cited pressure is only a design input.",
                     "target_match": "unsupported"}]}))

    run = extract(TEXT, prompt_description="Collect actual pump pressures.", field_list={"pressure": "Measured pressure"},
                  backend=Reviewer({"records": [record]}), backend_identity="field-scope",
                  table_subject="Pumps", observation_protocol=protocol)
    obs, = run.dataset["observations"]
    assert obs["citation_status"] == "located" and not obs["row_ids"]
    assert obs["field_exclusion"]["evidence"][0]["quote"] == TEXT
    entry = {"observation_id": obs["observation_id"], "field": "pressure", "entity": "pump P", "value": "18",
             "unit": "kPa", "target": "pump P", "statement_basis": "reported", "reason": "Identifies the pump.",
             "quote": TEXT, "start": 0, "end": len(TEXT)}

    def reread(row):
        review = {"source_sha256": sha256(TEXT.encode()).hexdigest(), "reviewed_by": "test reviewer", "readings": [row]}
        return assemble_dataset(TEXT, run.model_dump(mode="json"), run.analysis, reading_review=review)["observations"][0]

    assert not reread(entry)["row_ids"]
    # A new source-bound decision on field meaning can explicitly reopen the proposal.
    revised = reread({**entry, "field_applicability": "included", "reason": "A later reviewer disputed the field decision."})
    assert revised["row_ids"] and revised["original_checks"]["target_match"] == "unsupported"


def test_later_field_review_does_not_release_an_independent_policy_or_row_exclusion():
    proof = {"start": 0, "end": len(TEXT), "quote": TEXT}
    obs = {"eligible": False, "field_excluded": True, "filing_suppressed": True,
           "projection_reason": "model_excluded_field", "reading_reviews": [
               {"evidence": proof, "reason": "Field meaning has been independently reviewed.",
                "field_applicability": "included"}]}
    apply_field_verdict(obs, {})
    assert not obs.get("field_excluded") and obs["filing_suppressed"] and not obs["eligible"]
