"""Independent fixture expectations and descriptive experiment denominators."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from attestextract.models import DocumentChunk, ExtractionRequest, SourceSpan


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("provider_reporting", ROOT / "scripts/provider_experiment_reporting.py")
reporting = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporting)


def fixture(name):
    path = ROOT / "src/attestextract/examples/d1" / name
    source = (path / "source.txt").read_bytes().decode("utf-8")
    task = json.loads((path / "task.json").read_text(encoding="utf-8"))
    gold = json.loads((path / "expected.json").read_text(encoding="utf-8"))["records"]
    chunk = DocumentChunk(chunk_id="chunk-0001", text=source, segments=[SourceSpan(start=0, end=len(source))])
    case = {"source": source, "task": task, "gold": gold, "chunk": chunk.model_dump()}
    records = [{"record_type": item["record_type"],
                "values": {key: field["value"] for key, field in item["fields"].items()},
                "quotes": {key: field["quote"] for key, field in item["fields"].items()}}
               for item in gold]
    return case, {"records": records}


def score(payload, case):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return reporting.score_response(text, ExtractionRequest(messages=[], chunk_id="chunk-0001"), case)


@pytest.mark.parametrize("name,count", [("attribute", 1), ("observation", 2), ("relation", 1)])
def test_hand_expected_records_and_exact_spans(name, count):
    case, payload = fixture(name)
    result = score(payload, case)
    assert result["candidate_count"] == result["matching_records"] == count
    assert result["missing_records"] == result["unexpected_records"] == 0
    assert result["content_passed"] is result["evidence_passed"] is result["format_passed"] is True


def test_condition_values_do_not_merge_and_wrong_condition_evidence_is_rejected():
    case, payload = fixture("observation")
    payload["records"][1]["quotes"] = deepcopy(payload["records"][0]["quotes"])
    result = score(payload, case)
    assert result["matching_records"] == 2
    assert result["content_passed"] is True
    assert result["evidence_passed"] is False
    assert all(check["fixture_support"] is False for check in result["evidence_checks"] if check["record_index"] == 1)


def test_value_present_elsewhere_is_not_support_for_temperature():
    case, payload = fixture("observation")
    payload["records"][0]["quotes"]["temperature"] = case["gold"][0]["fields"]["temperature"]["quote"].replace("18", "36")
    result = score(payload, case)
    assert result["content_passed"] is True
    assert result["evidence_passed"] is False


def test_short_unique_field_quotes_are_valid_without_repeating_gold_sentence():
    case, payload = fixture("attribute")
    payload["records"][0]["quotes"] = {"product": "云杉灯", "rated_power": "8", "power_unit": "W"}
    assert score(payload, case)["evidence_passed"] is True


def test_repeated_short_number_is_ambiguous_and_not_accepted():
    case, payload = fixture("observation")
    payload["records"][0]["quotes"]["flow"] = "7"
    result = score(payload, case)
    assert result["evidence_passed"] is False
    assert any(check["location_status"] == "ambiguous" for check in result["evidence_checks"])


def test_duplicate_and_missing_condition_records_are_separate_errors():
    case, payload = fixture("observation")
    payload["records"][1] = deepcopy(payload["records"][0])
    result = score(payload, case)
    assert result["matching_records"] == 1
    assert result["missing_records"] == result["unexpected_records"] == result["duplicate_records"] == 1
    assert result["content_passed"] is False


@pytest.mark.parametrize("payload,json_passed,contract,task", [
    ('{"records": [{]', False, None, None),
    ({"records": "wrong"}, True, False, None),
    ({"records": [{"record_type": "other", "values": {}, "quotes": {}}]}, True, True, False),
])
def test_failure_layers_do_not_fabricate_content_scores(payload, json_passed, contract, task):
    case, _ = fixture("attribute")
    result = score(payload, case)
    assert result["json_passed"] is json_passed
    assert result["contract_passed"] is contract
    assert result["task_passed"] is task
    assert result["format_passed"] is False
    assert result["content_passed"] is result["evidence_passed"] is None


def test_numeric_coercion_boundary_and_number_field_float_equivalence():
    case, payload = fixture("observation")
    payload["records"][0]["values"]["flow"] = 7.0
    assert score(payload, case)["content_passed"] is True
    payload["records"][0]["values"]["flow"] = "7"
    assert score(payload, case)["task_passed"] is False
    payload["records"][0]["values"]["flow"] = True
    assert score(payload, case)["task_passed"] is False


def test_paper_without_gold_never_gets_content_accuracy_or_semantic_pass():
    case, payload = fixture("attribute")
    case["gold"] = None
    result = score(payload, case)
    assert result["content_passed"] is None
    assert result["evidence_scope"] == "location_only"
    assert result["fixture_supported_fields"] is None
    assert result["manual_semantic_review"] == "not_performed"
    empty = score({"records": []}, case)
    assert empty["format_passed"] is True
    assert empty["content_passed"] is empty["evidence_passed"] is None


def b_rows():
    return [{"id": f"B-{provider}-{mode}-{index}", "stage": "B", "provider": provider,
             "path_id": f"{provider}-chat-object", "tls_mode": mode,
             "case_id": "d1-observation" if index % 2 == 0 else "paper-chunk-0012"}
            for provider in ("qwen", "deepseek") for mode in ("auto", "1.2", "auto_fallback") for index in range(6)]


def http_result(row):
    return {"id": row["id"], "status": "completed", "sent": True, "http_complete": True,
            "status_code": 200, "elapsed_seconds": 2.0, "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def test_connection_gates_six_complete_requests_and_retains_presets():
    rows = b_rows()
    results = {row["id"]: http_result(row) for row in rows}
    chosen = reporting.select_connections(rows, results)
    assert chosen["qwen"]["selected"] == "auto_fallback"
    assert chosen["deepseek"]["selected"] == "1.2"
    results["B-qwen-auto_fallback-0"]["http_complete"] = False
    assert reporting.select_connections(rows, results)["qwen"]["selected"] == "auto"
    results["B-qwen-auto-0"]["status_code"] = 401
    assert reporting.select_connections(rows, results)["qwen"]["selected"] == "1.2"
    del results["B-qwen-1.2-5"]
    assert reporting.select_connections(rows, results)["qwen"]["selected"] is None


def c_rows():
    return [{"id": f"C-{path}-{index}", "stage": "C", "provider": provider, "path_id": path,
             "case_id": f"d1-{('attribute', 'observation', 'relation')[index % 3]}"}
            for provider, paths in {"qwen": ["qwen-chat-object"], "deepseek": [
                "deepseek-chat-object", "deepseek-responses-object", "deepseek-responses-schema"]}.items()
            for path in paths for index in range(6)]


def c_result(row):
    result = http_result(row)
    result["scores"] = {"format_passed": True, "content_passed": True, "evidence_passed": True,
                        "matching_records": 2 if row["case_id"] == "d1-observation" else 1,
                        "unexpected_records": 0}
    return result


def test_protocol_gate_has_fixed_chat_then_responses_then_schema_order():
    rows = c_rows()
    results = {row["id"]: c_result(row) for row in rows}
    assert reporting.select_protocols(rows, results)["deepseek"]["selected"] == "deepseek-chat-object"
    results["C-deepseek-chat-object-0"]["scores"]["evidence_passed"] = False
    assert reporting.select_protocols(rows, results)["deepseek"]["selected"] == "deepseek-responses-object"
    results["C-deepseek-responses-object-0"]["scores"]["unexpected_records"] = 1
    assert reporting.select_protocols(rows, results)["deepseek"]["selected"] == "deepseek-responses-schema"
    results["C-deepseek-responses-schema-0"]["scores"]["matching_records"] = 0
    assert reporting.select_protocols(rows, results)["deepseek"]["selected"] is None


def test_summary_preserves_unexecuted_unknown_and_not_evaluated_denominators():
    rows = b_rows()[:6]
    good = http_result(rows[0])
    good["scores"] = {"json_passed": False, "format_passed": False}
    failed = {"id": rows[1]["id"], "status": "failed", "sent": True, "http_complete": False,
              "usage": None, "scores": None, "transport_unknown": True, "billing_unknown": True, "elapsed_seconds": 180.0}
    unknown = {"id": rows[2]["id"], "status": "reserved_unknown", "sent": None, "billing_unknown": True}
    report = reporting.summarize(rows, [good, failed, unknown])
    counts = report["counts"]
    assert counts["planned"] == 6 and counts["sent"] == 2 and counts["http_success"] == 1
    assert counts["not_run"] == 3 and counts["reserved_unknown"] == 1
    assert counts["evaluation"]["json_passed"] == {"passed": 0, "failed": 1, "not_evaluated": 5}
    assert counts["evaluation"]["content_passed"]["not_evaluated"] == 6
    assert counts["known_usage"] == {"prompt_tokens": 10, "completion_tokens": 5}
    assert counts["usage_unknown_sent"] == 1 and counts["billing_unknown"] == 2
    assert counts["elapsed_seconds"]["median"] == 91
    assert {group["load"] for group in report["groups"]} == {"short", "long"}
    assert "Planned: 6; sent: 2" in reporting.render_report(report)


def test_unknown_usage_is_not_reported_as_zero():
    row = b_rows()[0]
    result = http_result(row)
    result["usage"] = {}
    counts = reporting.summarize([row], [result])["counts"]
    assert counts["known_usage"] is None and counts["usage_unknown_sent"] == 1


def test_unknown_post_attempt_is_not_claimed_as_confirmed_send():
    row = b_rows()[0]
    result = {"id": row["id"], "status": "failed", "sent": None, "model_post_attempts": 1,
              "request_outcome": "unknown", "elapsed_seconds": 180.0, "scores": None,
              "usage": None, "billing_unknown": True}
    counts = reporting.summarize([row], [result])["counts"]
    assert counts["sent"] == 0 and counts["possibly_sent"] == 1
    assert counts["model_post_attempts"] == 1 and counts["known_not_sent"] == 0
    assert counts["elapsed_seconds"]["median"] == 180
    assert counts["usage_unknown_attempts"] == counts["billing_unknown"] == 1


def test_observed_tls_never_uses_probe_version_or_configured_policy():
    rows = b_rows()[:3]
    results = {row["id"]: http_result(row) for row in rows}
    results[rows[0]["id"]]["connection_observation"] = {"request_kind": "model_post", "actual_tls_version": "TLSv1.2", "actual_http_version": "HTTP/1.1"}
    results[rows[1]["id"]]["connection_observation"] = {"request_kind": "tls_probe", "actual_tls_version": "TLSv1.3"}
    counts = reporting.summarize(rows, results)["counts"]
    assert counts["actual_tls_versions"] == {"TLSv1.2": 1, "unknown": 2}
    assert counts["actual_http_versions"] == {"HTTP/1.1": 1, "unknown": 2}


def test_cross_task_report_keeps_three_task_kinds_separate():
    rows = c_rows()
    report = reporting.summarize(rows, {row["id"]: c_result(row) for row in rows})
    assert len(report["groups"]) == 12
    assert {row["case_id"] for row in report["groups"]} == {"d1-attribute", "d1-observation", "d1-relation"}


def test_unknown_and_duplicate_result_ids_fail_closed():
    rows = b_rows()
    with pytest.raises(ValueError, match="unknown"):
        reporting.summarize(rows, {"foreign": {}})
    result = http_result(rows[0])
    with pytest.raises(ValueError, match="Duplicate"):
        reporting.summarize(rows, [result, result])


def test_fresh_full_run_requires_all_sixteen_chunks():
    rows = [{"id": f"D-{index}", "stage": "D", "provider": "qwen", "path_id": "qwen-recommended",
             "case_id": f"paper-chunk-{index:04d}"} for index in range(1, 17)]
    results = {row["id"]: {**http_result(row), "scores": {"format_passed": True}} for row in rows}
    full_run = reporting.summarize(rows, results)["full_runs"][0]
    assert full_run["trials_passed"] is True
    assert full_run["passed"] is None  # The whole-pipeline result is a separate artifact.
    del results[rows[-1]["id"]]
    assert reporting.summarize(rows, results)["full_runs"][0]["trials_passed"] is False
