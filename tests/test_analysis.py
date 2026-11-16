"""Frozen v0.1 analysis behavior remains available for legacy checkpoints."""

from copy import deepcopy
import json

import pytest

from attestextract import RunTrace, extract
from attestextract.analysis import _build_legacy_analysis as build_analysis
from attestextract.backends import ReplayBackend
from attestextract.errors import BackendError, ConfigurationError
from attestextract.execution import digest
from attestextract.models import BackendResponse, GenerationOptions, ReviewItem


SOURCE = "East contains reactor R. West contains reactor R. East R yields 18%. West R yields 32%."


def extraction(count=2):
    records = []
    for index in range(count):
        values = {"entity": "East R" if index % 2 == 0 else "West R", "value": str(18 + index),
                  "unit": "%", "conditions": None}
        records.append({"record_type": "yield", "values": values,
                        "quotes": {key: SOURCE if value is not None else "" for key, value in values.items()}})
    return extract(SOURCE, prompt_description="Extract reported yields without changing owners.",
                   field_list={"yield": "Original yield of each reactor occurrence"},
                   backend=ReplayBackend([{"records": records}], output_protocol="flat-v1"))


def discovery():
    return {"objects": [
        {"object_id": "east", "name": "R", "kind": "instance", "scope": "East",
         "is_table_subject": True, "mentions": [{"quote": "East contains reactor R."}]},
        {"object_id": "west", "name": "R", "kind": "instance", "scope": "West",
         "is_table_subject": True, "mentions": [{"quote": "West contains reactor R."}]},
    ], "relations": [{"source_id": "east", "target_id": "west", "predicate": "different_from",
                        "evidence": [{"quote": "East contains reactor R. West contains reactor R."}]}]}


class Backend:
    name, max_attempts = "synthetic-analysis", 1

    def __init__(self, change=None, fail_at=None):
        self.requests, self.change, self.fail_at = [], change, fail_at

    def generate(self, request):
        self.requests.append(request)
        if len(self.requests) == self.fail_at:
            raise BackendError("synthetic read timeout", code="timeout", attempts=1)
        payload = json.loads(request.messages[-1]["content"])
        if request.chunk_id == "analysis-discovery":
            response = discovery()
        else:
            response = {"bindings": [{
                "observation_id": item["observation_id"],
                "subject_id": next(obj["object_id"] for obj in payload["objects"] if obj["scope"] in item["entity"]),
                "status": "supported", "evidence": [{"quote": SOURCE}], "reason": "Explicit source owner.",
            } for item in payload["observations"]]}
        if self.change is not None:
            self.change(response, payload)
        return BackendResponse(text=json.dumps(response), model="synthetic-model", usage={"completion_tokens": 20})


def analyse(result=None, backend=None, **options):
    return build_analysis(SOURCE, result or extraction(), table_subject="reactor occurrence", backend=backend or Backend(),
                          backend_identity="synthetic-analysis-v1", generation=GenerationOptions(max_output_tokens=2048), **options)


def test_distinct_occurrences_and_values_preserved_without_expert_acceptance():
    original = extraction()
    before = original.model_dump(mode="json")
    first = analyse(original)
    second = analyse(original)
    assert first["status"] == "complete"
    assert len({obj["object_id"] for obj in first["objects"]}) == 2
    assert [obj["name"] for obj in first["objects"]] == ["R", "R"]
    assert first["objects"] == second["objects"]
    assert len({obs["subject_id"] for obs in first["observations"]}) == 2
    assert [obs["value"] for obs in first["observations"]] == ["18", "19"]
    assert all(obs["unit"] == "%" and obs["conditions"] is None for obs in first["observations"])
    assert first["semantic_status"] == "model_proposed"
    assert original.model_dump(mode="json") == before
    for evidence in first["evidence"]:
        assert SOURCE[evidence["start"]:evidence["end"]] == evidence["quote"]


def test_unknown_is_valid_and_original_blockers_remain():
    result = extraction()
    result.review.append(ReviewItem(candidate_id=result.candidates[0].candidate_id,
                                   code="missing_required_field", message="Required identity is missing."))
    def unknown(response, payload):
        for binding in response.get("bindings", []):
            binding.update(status="unknown", subject_id=None, evidence=[], reason="Cannot establish owner.")
    package = analyse(result, Backend(unknown))
    assert package["status"] == "complete"
    assert all(item["subject_id"] is None and item["binding_status"] == "unknown" for item in package["observations"])
    assert package["observations"][0]["blockers"][0]["code"] == "missing_required_field"
    assert len(package["review"]) == 2


def test_public_dispatch_preserves_saved_v01_contract(tmp_path):
    from attestextract.analysis import build_analysis as public_analysis

    original = extraction()
    analyse(original, max_calls=1, trace=RunTrace(tmp_path / "old"))
    backend = Backend()
    resumed = public_analysis(SOURCE, original, table_subject="reactor occurrence", backend=backend,
        backend_identity="synthetic-analysis-v1", generation=GenerationOptions(max_output_tokens=2048),
        max_calls=2, resume_from=tmp_path / "old/analysis/checkpoint.json",
        trace=RunTrace(tmp_path / "new"))
    assert resumed["analysis_version"] == "0.1" and resumed["status"] == "complete"
    assert len(backend.requests) == 1
    assert backend.requests[0].chunk_id == "analysis-binding-0001"


@pytest.mark.parametrize("fault", ["unknown", "missing", "duplicate", "changed_value", "bad_quote"])
def test_invalid_binding_never_applies_partial_batch(fault):
    def invalid(response, payload):
        if "bindings" not in response:
            return
        if fault == "unknown":
            response["bindings"][0]["subject_id"] = "nonexistent"
        elif fault == "missing":
            response["bindings"].pop()
        elif fault == "duplicate":
            response["bindings"].append(deepcopy(response["bindings"][0]))
        elif fault == "changed_value":
            response["bindings"][0]["value"] = "999"
        else:
            response["bindings"][0]["evidence"] = [{"quote": "Not in the paper."}]
    package = analyse(backend=Backend(invalid))
    assert package["status"] == "failed"
    assert all(item["subject_id"] is None for item in package["observations"])
    assert [item["value"] for item in package["observations"]] == ["18", "19"]


def test_ambiguous_quote_is_not_assigned_to_first_match():
    def invalid(response, payload):
        if "objects" in response:
            response["objects"][0]["mentions"] = [{"quote": "reactor R."}]
    backend = Backend(invalid)
    package = analyse(backend=backend)
    assert package["status"] == "failed"
    assert len(backend.requests) == 1
    assert package["objects"] == []


def test_explicit_occurrence_resolves_repeated_quote():
    def choose(response, payload):
        if "objects" in response:
            response["objects"][0]["mentions"] = [{"quote": "reactor R.", "occurrence": 0}]
    package = analyse(backend=Backend(choose))
    assert package["status"] == "complete"
    obj = package["objects"][0]
    evidence = next(item for item in package["evidence"] if item["evidence_id"] == obj["mention_evidence_ids"][0])
    assert evidence["start"] == SOURCE.index("reactor R.")


def test_budget_resume_reuses_discovery_and_does_not_modify_history(tmp_path):
    first_trace = RunTrace(tmp_path / "first")
    first_backend = Backend()
    first = analyse(backend=first_backend, trace=first_trace, max_calls=1)
    assert first["status"] == "pending" and len(first_backend.requests) == 1
    checkpoint = tmp_path / "first/analysis/checkpoint.json"
    old = {p.relative_to(tmp_path / "first"): p.read_bytes() for p in (tmp_path / "first").rglob("*") if p.is_file()}
    second_backend = Backend()
    second = analyse(backend=second_backend, trace=RunTrace(tmp_path / "second"), resume_from=checkpoint, max_calls=2)
    assert second["status"] == "complete"
    assert [item.chunk_id for item in second_backend.requests] == ["analysis-binding-0001"]
    assert second["budget"]["reserved"] == 2
    assert old == {p.relative_to(tmp_path / "first"): p.read_bytes() for p in (tmp_path / "first").rglob("*") if p.is_file()}


def test_timeout_job_not_reissued_and_pending_later_job_can_resume(tmp_path):
    result = extraction(13)
    first = analyse(result, Backend(fail_at=2), trace=RunTrace(tmp_path / "first"))
    assert first["status"] == "failed" and first["budget"]["reserved"] == 2
    second_backend = Backend()
    second = analyse(result, second_backend, trace=RunTrace(tmp_path / "second"), resume_from=tmp_path / "first/analysis/checkpoint.json")
    assert [request.chunk_id for request in second_backend.requests] == ["analysis-binding-0002"]
    assert second["status"] == "failed" and second["budget"]["reserved"] == 3
    assert sum(item["binding_status"] == "failed" for item in second["observations"]) == 12


def test_all_caches_checked_before_new_request_and_check_is_read_only(tmp_path):
    analyse(trace=RunTrace(tmp_path / "first"), max_calls=1)
    checkpoint = tmp_path / "first/analysis/checkpoint.json"
    backend = Backend()
    plan = analyse(backend=backend, resume_from=checkpoint, check_only=True)
    assert not backend.requests and plan["reusable_jobs"] == 1
    cache = tmp_path / "first/analysis/cache/analysis-discovery.json"
    cache.write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="damaged"):
        analyse(backend=backend, resume_from=checkpoint, check_only=True)
    assert not backend.requests


def test_semantically_invalid_cache_rejected_even_with_updated_checksums(tmp_path):
    analyse(trace=RunTrace(tmp_path / "first"), max_calls=1)
    checkpoint = tmp_path / "first/analysis/checkpoint.json"
    cache = tmp_path / "first/analysis/cache/analysis-discovery.json"
    raw = json.loads(cache.read_text(encoding="utf-8"))
    bad = discovery()
    bad["objects"][0]["mentions"] = [{"quote": "Fabricated source quote"}]
    raw["text"] = json.dumps(bad)
    cache.write_text(json.dumps(raw), encoding="utf-8")
    envelope = json.loads(checkpoint.read_text(encoding="utf-8"))
    envelope["state"]["jobs"][0]["cache_hash"] = digest(raw)
    envelope["sha256"] = digest(envelope["state"])
    checkpoint.write_text(json.dumps(envelope), encoding="utf-8")
    backend = Backend()
    with pytest.raises(ConfigurationError, match="contract"):
        analyse(backend=backend, resume_from=checkpoint, check_only=True)
    assert not backend.requests


def test_input_and_transport_limits_fail_before_call():
    result, backend = extraction(), Backend()
    with pytest.raises(ConfigurationError, match="checksum"):
        build_analysis(SOURCE + "changed", result, table_subject="reactor", backend=backend, backend_identity="test")
    with pytest.raises(ConfigurationError, match="60,000"):
        build_analysis("x" * 60001, result, table_subject="reactor", backend=backend, backend_identity="test")
    backend.max_attempts = 2
    with pytest.raises(ConfigurationError, match="max_attempts"):
        analyse(result, backend)
    assert not backend.requests


def test_interrupt_is_saved_and_never_replayed(tmp_path):
    class Interrupted(Backend):
        def generate(self, request):
            self.requests.append(request)
            raise KeyboardInterrupt
    first = analyse(backend=Interrupted(), trace=RunTrace(tmp_path / "first"))
    assert first["status"] == "failed" and first["jobs"][0]["error_code"] == "interrupted"
    backend = Backend()
    second = analyse(backend=backend, trace=RunTrace(tmp_path / "second"), resume_from=tmp_path / "first/analysis/checkpoint.json")
    assert second["status"] == "failed" and not backend.requests


def test_schema_requires_all_properties_and_unknown_never_becomes_accepted():
    backend = Backend()
    analyse(backend=backend)
    for request in backend.requests:
        payload = json.loads(request.messages[-1]["content"])
        assert payload["response_contract"] == request.output_schema
        if request.chunk_id != "analysis-discovery":
            evidence = {item["evidence_id"]: item for item in payload["evidence"]}
            assert all(key in evidence for obj in payload["objects"] for key in obj["mention_evidence_ids"])
        def check(schema):
            if isinstance(schema, dict):
                if schema.get("type") == "object":
                    assert set(schema["required"]) == set(schema["properties"])
                    assert schema["additionalProperties"] is False
                for value in schema.values():
                    check(value)
            elif isinstance(schema, list):
                for value in schema:
                    check(value)
        check(request.output_schema)


def test_empty_generation_keeps_a_finite_analysis_output_limit():
    backend = Backend()
    build_analysis(SOURCE, extraction(), table_subject="reactor", backend=backend,
                   backend_identity="synthetic-analysis-v1", generation=GenerationOptions())
    assert all(request.generation.max_output_tokens == 8192 for request in backend.requests)


def test_verified_extraction_reservations_are_passed_separately_from_result():
    result = extraction()
    before = result.model_dump(mode="json")
    unknown = analyse(result, max_calls=1)
    assert unknown["budget"]["extraction_reserved"] is None
    assert unknown["budget"]["total_reserved"] is None
    known = analyse(result, max_calls=1, extraction_reserved=8)
    assert known["budget"]["extraction_reserved"] == 8
    assert known["budget"]["total_reserved"] == 9
    assert result.model_dump(mode="json") == before


def test_wrong_stored_quote_does_not_become_valid_evidence():
    result, backend = extraction(), Backend()
    result.candidates[0].fields["value"].evidence.quote = "fabricated quotation"
    with pytest.raises(ConfigurationError, match="quote"):
        analyse(result, backend)
    assert not backend.requests


def test_backend_programming_error_is_not_disguised_as_a_model_failure():
    class Broken(Backend):
        def generate(self, request):
            raise RuntimeError("programming error")
    with pytest.raises(RuntimeError, match="programming error"):
        analyse(backend=Broken())


def test_orphan_relation_rejected_before_binding_calls():
    def invalid(response, payload):
        if "relations" in response:
            response["relations"][0]["target_id"] = "missing"
    backend = Backend(invalid)
    package = analyse(backend=backend)
    assert package["status"] == "failed" and len(backend.requests) == 1
    assert package["objects"] == []


def test_automatic_analysis_uses_successful_route_and_rejects_changed_endpoint_model(tmp_path):
    from attestextract.automatic import AutomaticBackend, AutomaticRoute
    from attestextract.config import BackendConfig
    from attestextract.models import ExecutionOptions

    class Hybrid(Backend):
        def generate(self, request):
            if request.chunk_id.startswith("analysis-"):
                return super().generate(request)
            values = {"entity": "East R", "value": "18", "unit": "%", "conditions": None}
            record = {"record_type": "yield", "values": values,
                      "quotes": {key: SOURCE if value is not None else "" for key, value in values.items()}}
            return BackendResponse(text=json.dumps({"records": [record]}), model="synthetic-model")

    config = BackendConfig(base_url="https://synthetic.invalid/v1", model="synthetic-model", structured_output="json_object")
    fixed = Hybrid()
    backend = AutomaticBackend([AutomaticRoute("primary", "primary", config, fixed)], use_cache=False)
    result = extract(SOURCE, prompt_description="Extract yields", field_list={"yield": "Reactor yield"},
                     backend=backend, execution=ExecutionOptions())
    first = build_analysis(SOURCE, result, table_subject="reactor", backend=backend, max_calls=1,
                           trace=RunTrace(tmp_path / "first"))
    assert first["status"] == "pending" and first["jobs"][0]["route_id"] == "primary"
    config.timeout = 120
    second = build_analysis(SOURCE, result, table_subject="reactor", backend=backend,
                            resume_from=tmp_path / "first/analysis/checkpoint.json", trace=RunTrace(tmp_path / "second"))
    assert second["status"] == "complete"
    before = len(fixed.requests)
    config.model = "other-model"
    with pytest.raises(ConfigurationError, match="snapshot"):
        build_analysis(SOURCE, result, table_subject="reactor", backend=backend)
    assert len(fixed.requests) == before
