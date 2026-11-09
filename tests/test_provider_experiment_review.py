"""Source-bound review acceptance using only the synthetic D1 examples."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

from attestextract.errors import ConfigurationError
from attestextract.models import BackendResponse, ExtractionRequest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import provider_experiment_bundle as bundles
from provider_experiment_reporting import score_response
from provider_experiment_review import REFERENCES, validate_review
from provider_experiment_runner import digest, load_results, save_record


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_review_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(bundles, "runtime_identity", lambda: {"engine": "synthetic"})
    monkeypatch.setattr(bundles, "network_identity", lambda: {"network": "synthetic"})
    source, task = tmp_path / "source.md", tmp_path / "task.json"
    source.write_text("\n\n".join(f"Item {i:02} reads 11. " + "Synthetic background. " * 170
                                  for i in range(16)), encoding="utf-8")
    _write(task, {"name": "Synthetic paper", "record_types": {"reading": {
        "fields": {"value": {"type": "integer"}}}}})
    root = tmp_path / "bundle"
    bundles.prepare_bundle(root, source, task, {})
    bundle = bundles.load_bundle(root)
    review = {"review_version": "0.1", "origin": "assistant_semantic_review", "expert_gold": False,
              "rubric_basis": "Synthetic accountable review; object names and condition passages checked.",
              "bundle_id": bundle["manifest"]["bundle_id"],
              "reference_checksums": {n: bundle["manifest"]["files"][n] for n in REFERENCES}, "findings": []}
    for row in bundle["rows"]:
        if row["stage"] != "C":
            continue
        case = bundle["cases"][row["case_id"]]
        records = [{"record_type": record["record_type"],
                    "values": {k: v["value"] for k, v in record["fields"].items()},
                    "quotes": {k: v["quote"] for k, v in record["fields"].items()}}
                   for record in case["gold"]]
        names = []
        if row["case_id"] == "d1-observation":
            for index, record in enumerate(records):
                record["values"]["sample"] = "青禾"
                names.append({"record_index": index, "field": "sample", "model_value": "青禾",
                              "gold_value": "试样青禾", "judgment": "equivalent_in_this_source",
                              "reason": "The source names one sample; its category prefix is not a second identity."})
        text = json.dumps({"records": records}, ensure_ascii=False)
        response = BackendResponse(text=text, finish_reason="stop").model_dump(mode="json")
        scores = score_response(text, ExtractionRequest.model_validate(row["request"]), case)
        folder = root / "results/trials" / row["id"]
        response_path = folder / "diagnostics/unified-response.json"
        _write(response_path, response)
        raw = folder / "diagnostics/responses/chunk-0001.txt"
        raw.parent.mkdir(parents=True)
        raw.write_text(text, encoding="utf-8")
        saved = {"id": row["id"], "row_sha256": digest(row), "provider": row["provider"],
                 "stage": "C", "path_id": row["path_id"], "status": "completed", "sent": True,
                 "status_code": 200, "http_complete": True, "transport_unknown": False,
                 "request_outcome": "response_received", "scores": scores}
        save_record(folder / "reserved.json", {"id": row["id"], "row_sha256": digest(row), "reserved_calls": 1})
        save_record(folder / "result.json", saved)
        review["findings"].append({"trial_id": row["id"], "path_id": row["path_id"],
            "case_id": row["case_id"], "round": row["round"],
            "original_result_file": (folder / "result.json").relative_to(root).as_posix(),
            "original_result_sha256": _sha(folder / "result.json"),
            "response_file": response_path.relative_to(root).as_posix(), "response_sha256": _sha(response_path),
            "original_strict_content_passed": scores["content_passed"],
            "original_strict_evidence_passed": scores["evidence_passed"],
            "assistant_semantic_review": "pass_for_this_synthetic_case", "candidate_count": len(records),
            "quote_fields_exact_and_condition_aligned": scores["exact_source_spans"],
            "name_boundary_findings": names, "reason": "Reviewed against source and annotated conditions."})
    return root, bundle, load_results(root / "results", bundle["rows"]), review


@pytest.fixture
def case(tmp_path, monkeypatch):
    return build_review_fixture(tmp_path, monkeypatch)


def _validate(case):
    root, bundle, results, review = case
    return validate_review(review, bundle, root / "results", results)


def _observation(case):
    return next(f for f in case[3]["findings"] if f["case_id"] == "d1-observation")


def _replace_response(case, finding, change):
    """Tamper consistently to exercise semantic guards beyond file hashes."""
    root, bundle, results, _ = case
    response_path = root / finding["response_file"]
    response = json.loads(response_path.read_text(encoding="utf-8"))
    body = json.loads(response["text"])
    change(body)
    response["text"] = json.dumps(body, ensure_ascii=False)
    _write(response_path, response)
    (response_path.parent / "responses/chunk-0001.txt").write_text(response["text"], encoding="utf-8")
    row = next(row for row in bundle["rows"] if row["id"] == finding["trial_id"])
    scores = score_response(response["text"], ExtractionRequest.model_validate(row["request"]),
                            bundle["cases"][row["case_id"]])
    result = results[finding["trial_id"]]
    result["scores"] = scores
    result_path = root / finding["original_result_file"]
    _write(result_path, {"result": result, "sha256": digest(result)})
    finding.update(response_sha256=_sha(response_path), original_result_sha256=_sha(result_path),
                   original_strict_content_passed=scores["content_passed"],
                   original_strict_evidence_passed=scores["evidence_passed"],
                   candidate_count=scores.get("candidate_count", 0),
                   quote_fields_exact_and_condition_aligned=scores.get("exact_source_spans", 0))


def test_review_selects_predeclared_paths_without_changing_strict_scores(case):
    before = deepcopy(case[2])
    checksums = {str(p): _sha(p) for p in (case[0] / "results").rglob("*") if p.is_file()}
    result = _validate(case)
    assert result["protocol_selection"]["qwen"]["selected"] == "qwen-chat-object"
    assert result["protocol_selection"]["deepseek"]["selected"] == "deepseek-chat-object"
    assert len(result["protocol_selection"]["deepseek"]["eligible"]) == 3
    assert sum(r["scores"]["content_passed"] for r in case[2].values()) == 16
    assert case[2] == before
    assert all(_sha(Path(p)) == digest for p, digest in checksums.items())


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(bundle_id="another-bundle"),
    lambda r: r.update(origin="expert"),
    lambda r: r.update(expert_gold=True),
    lambda r: r["reference_checksums"].update({"gold.json": "changed"}),
    lambda r: r["findings"].pop(),
    lambda r: r["findings"].__setitem__(0, deepcopy(r["findings"][1])),
    lambda r: r["findings"][0].update(trial_id="C-foreign"),
    lambda r: r["findings"][0].update(case_id="d1-relation"),
    lambda r: r["findings"][0].update(path_id="different-path"),
    lambda r: r["findings"][0].update(round=2),
    lambda r: r["findings"][0].update(round=True),
    lambda r: r["findings"][0].update(reason=""),
    lambda r: r["findings"][0].update(assistant_semantic_review="accept"),
    lambda r: r["findings"][0].update(response_file="../secret.json"),
    lambda r: r["findings"][0].update(candidate_count=True),
    lambda r: r["findings"][0].update(quote_fields_exact_and_condition_aligned=99),
])
def test_invalid_review_rejected(case, mutation):
    mutation(case[3])
    with pytest.raises(ConfigurationError, match="review"):
        _validate(case)


@pytest.mark.parametrize("verdict", ["reject", "review"])
def test_nonpass_disqualifies_path_and_cannot_force_recommendation(case, verdict):
    case[3]["findings"][0]["assistant_semantic_review"] = verdict
    case[3]["recommended_D_selection_after_review"] = {"qwen": {"path": "qwen-chat-object"}}
    assert _validate(case)["protocol_selection"]["qwen"]["selected"] is None


def test_name_equivalence_must_be_explicit_and_exactly_bound(case):
    finding = _observation(case)
    finding["name_boundary_findings"] = []
    with pytest.raises(ConfigurationError, match="value-and-condition"):
        _validate(case)


@pytest.mark.parametrize("artifact", ["response", "raw", "result"])
def test_artifact_changes_rejected(case, artifact):
    root, _, _, review = case
    f = review["findings"][0]
    path = root / f["response_file"] if artifact == "response" else root / f["original_result_file"]
    if artifact == "raw":
        path = path.parent / "diagnostics/responses/chunk-0001.txt"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        _validate(case)


def test_review_cannot_relabel_unknown_transport_as_pass(case):
    root, _, results, review = case
    finding = review["findings"][0]
    result = results[finding["trial_id"]]
    result.update(transport_unknown=True, request_outcome="unknown", sent=None)
    path = root / finding["original_result_file"]
    _write(path, {"result": result, "sha256": digest(result)})
    finding["original_result_sha256"] = _sha(path)
    with pytest.raises(ConfigurationError, match="complete successful"):
        _validate(case)


@pytest.mark.parametrize("change", [
    lambda body: body["records"][0]["quotes"].update(flow="not a source quote"),
    lambda body: body["records"][0]["quotes"].update(flow=body["records"][1]["quotes"]["flow"]),
    lambda body: body["records"][0]["values"].update(temperature=36),
    lambda body: body["records"][0]["values"].update(flow=None),
    lambda body: body["records"].pop(),
    lambda body: body["records"].append(deepcopy(body["records"][0])),
    lambda body: body["records"][0]["values"].update(flow="wrong type"),
])
def test_review_cannot_override_wrong_evidence_conditions_null_or_structure(case, change):
    _replace_response(case, _observation(case), change)
    with pytest.raises(ConfigurationError):
        _validate(case)


def test_review_cannot_override_invented_name_even_with_matching_review(case):
    finding = _observation(case)
    _replace_response(case, finding, lambda b: b["records"][0]["values"].update(sample="invented"))
    finding["name_boundary_findings"][0]["model_value"] = "invented"
    with pytest.raises(ConfigurationError, match="value-and-condition"):
        _validate(case)


def test_reversed_record_order_is_valid_with_correct_judgment_bindings(case):
    finding = _observation(case)
    _replace_response(case, finding, lambda b: b["records"].reverse())
    assert _validate(case)["protocol_selection"]["qwen"]["selected"] == "qwen-chat-object"


def test_duplicate_name_judgment_rejected(case):
    finding = _observation(case)
    finding["name_boundary_findings"].append(deepcopy(finding["name_boundary_findings"][0]))
    with pytest.raises(ConfigurationError, match="duplicate name"):
        _validate(case)
