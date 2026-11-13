"""Table windows share source identity, not response-local record identities."""

from copy import deepcopy
import json

from attestextract import ExtractionResult, extract
from attestextract.annotation_view import save_annotation
from attestextract.backends import ReplayBackend
from attestextract.expert_review import build_expert_review


def table_result(chunk_id, value):
    record = {
        "record_id": "reading", "record_type": "mass", "scope": "individual",
        "scope_sources": [], "anchors": [], "fields": {
            "entity": {"value": "Sample A", "sources": [], "relation": None},
            "value": {"value": value, "sources": [], "relation": None},
            "unit": {"value": "g", "sources": [], "relation": None},
            "conditions": {"value": None, "sources": [], "relation": None},
        },
    }
    return {
        "table_id": "table-0001", "chunk_id": chunk_id, "status": "partial_format_error",
        "candidates": [record], "evidence": {}, "issues": [],
        "rejections": [{"index": 1, "raw": {**deepcopy(record), "record_id": "rejected"},
                        "errors": [{"code": "synthetic_rejection"}]}],
        "parsed": {}, "response": {"text": "synthetic saved response"},
    }


def saved_result(*tables):
    result = extract(
        "Synthetic source.", prompt_description="Extract reported mass.",
        field_list={"mass": "Reported mass"},
        backend=ReplayBackend([{"records": []}], output_protocol="flat-v1"),
    ).model_dump(mode="json")
    result.update(schema_version="0.6", table_results=list(tables), format_status="partial_format_error")
    result["run"]["chunk_count"] = len(tables)
    return ExtractionResult.model_validate(result)


def test_unsplit_table_keeps_existing_candidate_and_rejection_ids():
    review = build_expert_review(saved_result(table_result("table-0001", "3")))
    item = review["rows"][0]["fields"]["mass"][0]
    assert item["candidate_id"] == "table-0001:reading"
    assert review["quarantined"][0]["candidate_id"] == "table-0001:rejected"


def test_window_local_record_ids_remain_distinct_in_review_and_annotation(tmp_path):
    result = saved_result(table_result("table-0001.1", "3"), table_result("table-0001.2", "5"))
    before = result.model_dump(mode="json")
    review = build_expert_review(result)
    observations = review["rows"][0]["fields"]["mass"]
    assert [item["candidate_id"] for item in observations] == [
        "table-0001.1:reading", "table-0001.2:reading"]
    assert [item["value"] for item in observations] == ["3", "5"]
    assert [item["table_id"] for item in observations] == ["table-0001", "table-0001"]
    assert [item["source_record"] for item in observations] == [
        "/table_results/0/candidates/0", "/table_results/1/candidates/0"]
    assert review["rows"][0]["identity_verified"] is False
    assert [item["candidate_id"] for item in review["quarantined"]] == [
        "table-0001.1:rejected", "table-0001.2:rejected"]
    save_annotation(result, tmp_path)
    mapping = json.loads((tmp_path / "annotation.sources.json").read_text(encoding="utf-8"))
    assert len({entry["candidate_id"] for entry in mapping["entries"]}) == 4
    assert result.model_dump(mode="json") == before
