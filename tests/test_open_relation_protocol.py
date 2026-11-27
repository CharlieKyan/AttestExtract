import json

import pytest

from niceextract import extract
from niceextract.dataset_snapshot import checked_snapshot
from niceextract.errors import InvalidResponseError
from niceextract.models import BackendResponse, DocumentChunk, EvidenceRef, FieldValue, SourceSpan
from niceextract.pipeline import _parse_response, _value_body_location
from niceextract.table_routes import parse_route_response_partial
from niceextract.task_input import resolve_task


class LiveFixture:
    name = "open-relations-fixture"
    mode = "live"
    output_protocol = "flat-v1"

    def __init__(self, records):
        self.records = records
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return BackendResponse(text=json.dumps({"records": self.records}))


SOURCE = "Core A from site K measured 12 N. Core B measured 5 N."
TASK = resolve_task({"prompt_description": "Collect loads.", "field_list": {"load": "Load on the core."}}).model_copy(
    update={"row_subject": "Sites"})
LINK = {"target": "site K", "explanation": "Core A was taken from site K.", "field_applicability": "included",
        "target_contains_direct": True, "evidence": [{"ref": None, "quote": "Core A from site K"}]}


def reading(entity="Core A", value="12", quote="Core A from site K measured 12 N."):
    return {"record_type": "load", "name_handling": "as_reported", "values": {"entity": entity, "value": value},
            "quotes": {"entity": quote, "value": quote}, "statement_basis": "reported", "reported_use": "measured"}


def test_open_explanation_and_direct_wire_variants_preserve_original_evidence():
    backend = LiveFixture([dict(reading(), row_relation=LINK, name_handling="related"),
                           reading("Core B", "5", "Core B measured 5 N.")])
    result = extract(SOURCE, TASK, backend=backend)
    assert len(backend.requests) == 1
    variants = backend.requests[0].output_schema["properties"]["records"]["items"]["anyOf"]
    direct, related = variants
    assert "row_relation" not in direct["properties"]
    contract = related["properties"]["row_relation"]["anyOf"][0]
    assert "predicate" not in contract["properties"] and contract["properties"]["explanation"]["type"] == "string"
    observations = checked_snapshot(SOURCE, result.model_dump(mode="json"), None)[0]["observations"]
    by_value = {item["value"]: item for item in observations}
    receipt, = by_value["12"]["row_relations"]
    assert receipt["target_contains_direct"] is True and receipt["explanation"] == LINK["explanation"]
    entry, = receipt["evidence"]
    assert SOURCE[entry["start"]:entry["end"]] == LINK["evidence"][0]["quote"]
    assert "row_relations" not in by_value["5"]


def test_free_relation_quotes_use_only_visible_exact_sources():
    target = {**LINK, "evidence": ["Core A from site K", "measured 12 N."]}
    result = extract(SOURCE, TASK, backend=LiveFixture([dict(reading(), row_relation=target)]))
    receipt, = result.candidates[0].row_relations
    assert receipt.status == "located"
    assert [SOURCE[e.start:e.end] for e in receipt.evidence] == target["evidence"]
    from niceextract.row_relations import table_relation_receipts
    entries = [{"ref": name, "start": start, "quote": quote} for name, start, quote in [
        ("cell-A", 2, "site K"), ("cell-B", 20, "sample A"), ("cell-C", 40, "sample A")]]
    raw = {"row_relation": {**LINK, "evidence": ["cell-A", "sample A", "never shown"]}}
    table, = table_relation_receipts(raw, {"entries": entries})
    assert table["status"] == "unresolved"
    assert table["evidence"] == [
        {"ref": "cell-A", "quote": "site K", "start": 2, "end": 8},
        {"ref": None, "quote": "sample A", "start": None, "end": None},
        {"ref": None, "quote": "never shown", "start": None, "end": None},
    ]


@pytest.mark.parametrize("bad", ["belongs to", {"target": "site K", "evidence": []},
                                 {"alternatives": [LINK, {"target": "site Q"}]}])
def test_malformed_optional_explanation_keeps_both_observations_and_original_raw(bad):
    result = extract(SOURCE, TASK, backend=LiveFixture([dict(reading(), row_relation=bad),
                       reading("Core B", "5", "Core B measured 5 N.")]))
    obs = checked_snapshot(SOURCE, result.model_dump(mode="json"), None)[0]["observations"]
    assert len(obs) == 2
    by_value = {row["value"]: row for row in obs}
    issue, = by_value["12"]["row_relation_issues"]
    assert issue["raw"] == bad and issue["status"] == "malformed"
    assert "row_relations" not in by_value["12"] and "row_relation_issues" not in by_value["5"]


def test_prior_predicate_is_verbatim_text_only_under_the_open_contract():
    raw = {k: v for k, v in LINK.items() if k not in {"explanation", "target_contains_direct"}}
    raw["predicate"] = "属于"
    text = json.dumps({"records": [dict(reading(), row_relation=raw)]})
    proposal = _parse_response(text, row_relation_protocol="explanation-v1").records[0]._open_relation
    assert proposal.explanation == raw["predicate"] and proposal.target_contains_direct is None
    with pytest.raises(InvalidResponseError):
        _parse_response(text, row_relation_protocol="evidence-v1")
    with pytest.raises(InvalidResponseError):
        _parse_response(json.dumps({"records": [dict(reading(), values="broken", row_relation=1)]}),
                        row_relation_protocol="explanation-v1")


def test_table_quarantines_only_the_relation_while_preserving_direct_reading():
    item = {"record_id": "r", "record_type": "load", "fields": {}, "anchors": [{"ref": "a", "quote": "12"}],
            "scope": "individual", "scope_sources": []}
    data = {"records": [dict(item, row_relation={"target": 45}), dict(item, record_id="r2"),
                        dict(item, record_id="r3", anchors=[])]}
    parsed = parse_route_response_partial("A", json.dumps(data), row_relations="explanation-v1", source_statements=True)
    assert parsed["record_indices"] == [0, 1] and parsed["rejections"][0]["index"] == 2
    assert parsed["records"][0]["row_relation_issues"][0]["raw"] == {"target": 45}
    assert "row_relation" not in parsed["records"][1]


def test_body_ownership_uses_value_occurrence_independently_of_citation_window():
    text = "Core specimens in N. Core A measured 12 N."
    boundary = text.index("Core A")
    chunk = DocumentChunk(chunk_id="test", text=text, segments=[{"start": 0, "end": len(text)}],
                          body_segments=[{"start": 0, "end": boundary}],
                          context_segments=[{"role": "neighbor_after", "span": {"start": boundary, "end": len(text)}}])
    cited = EvidenceRef(quote=text, status="matched", matches=[SourceSpan(start=0, end=len(text))])
    assert _value_body_location(text, FieldValue(value="12", evidence=cited), chunk) == "context"
    assert _value_body_location(text, FieldValue(value="123", evidence=cited), chunk) == "unverified"
    assert _value_body_location(text, FieldValue(value="specimens", evidence=cited), chunk) == "body"


@pytest.mark.parametrize("clause,flags", [
    ("B8 reached 60 °C.", True), ("B7 reached 60 °C.", False),
    ("It reached 60 °C.", False), ("B7 and B8 reached 60 °C.", False),
    ("B8 and its companions reached 60 °C.", False)])
def test_cited_numeric_subject_conflict_is_explicit_and_preserves_original_owner(clause, flags):
    source = "B7 was cool. " + clause
    item = {"record_type": "load", "name_handling": "as_reported", "values": {"entity": "B7", "value": "60"},
            "quotes": {"entity": "B7 was cool.", "value": clause}}
    result = extract(source, TASK, backend=LiveFixture([item]))
    candidate, = result.candidates
    assert candidate.fields["entity"].value == "B7"
    assert any(c.code == "value_owner_conflict" and not c.passed for c in candidate.checks) == flags
    if flags:
        assert any(r.code == "value_owner_conflict" and "B8" in r.message for r in result.review)
