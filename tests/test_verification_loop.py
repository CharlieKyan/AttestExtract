"""Synthetic public acceptance of bounded, resumable source verification."""

from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from attestextract import RunTrace, extract
from attestextract.analysis import build_analysis
from attestextract.backends import OpenAICompatibleBackend, ReplayBackend
from attestextract.errors import BackendError, ConfigurationError, InvalidResponseError
from attestextract.execution import digest
from attestextract.models import BackendResponse, GenerationOptions


MEASUREMENT = "Sample A has a mass of 17 g."
RELATION = "The coded specimen Sample A was collected from Reactor North."
SHORT = MEASUREMENT + "\n\nBackground methods are reported separately.\n\n" + RELATION
LONG = MEASUREMENT + "\n\n" + ("Unrelated background describes apparatus maintenance and storage.\n\n" * 1200) + RELATION


def frozen_extraction(source=SHORT, count=1):
    """Known source-linked observations, independent of a live extraction model."""
    records = [{"record_type": "mass", "values": {"entity": "Sample A", "value": str(17 + n),
                                                "unit": "g", "conditions": None},
                "quotes": {"entity": MEASUREMENT, "value": MEASUREMENT,
                           "unit": MEASUREMENT, "conditions": ""}} for n in range(count)]
    result = extract(MEASUREMENT, prompt_description="Extract explicitly reported specimen mass.",
                     field_list={"mass": "The reported mass of the specific specimen."},
                     backend=ReplayBackend([{"records": records}], output_protocol="flat-v1"))
    # Source extension keeps the deliberately fixed observation offsets intact.
    # More than one record is used only to exercise scheduling, not quality.
    sha = hashlib.sha256(source.encode()).hexdigest()
    result.source = result.source.model_copy(update={"sha256": sha, "character_count": len(source),
                                                     "document_id": "synthetic-" + sha[:12]})
    return result


def action(name, **changes):
    return {"action": name, "question": "Where did this specimen originate?", "query": None,
            "search_offset": 0, "passage_ids": [], "neighbors": 0,
            "objects": [], "relations": [], "bindings": [], **changes}


def shown(payload):
    passages = list(payload["initial_context"]["passages"])
    for frame in payload["lookup_history"]:
        passages.extend(frame["result"].get("matches", []))
        passages.extend(frame["result"].get("passages", []))
    return passages


def cite(payload, text):
    passage = next(part for part in shown(payload) if text in part["text"])
    return {"passage_id": passage["passage_id"], "quote": text, "occurrence": None}


def final_decision(payload, target_match="supported"):
    nearby, distant = cite(payload, MEASUREMENT), cite(payload, RELATION)
    objects = [{"object_id": "specimen", "name": "Sample A from Reactor North", "kind": "sample",
                "scope": "This document's coded specimen", "is_table_subject": True, "mentions": [nearby, distant]},
               {"object_id": "reactor", "name": "Reactor North", "kind": "instance",
                "scope": "This document's reactor", "is_table_subject": False, "mentions": [distant]}]
    bindings = [{"observation_id": item["observation_id"], "subject_id": "specimen", "status": "supported",
                 "evidence": [nearby, distant], "reason": "The measured specimen and origin are explicitly connected.",
                 "target_match": target_match, "property_name": "mass", "statistic": None}
                for item in payload["observations"]]
    return action("decide", objects=objects, bindings=bindings,
                  relations=[{"source_id": "specimen", "target_id": "reactor",
                              "predicate": "sample_of", "evidence": [distant]}])


class LookupBackend:
    name, max_attempts = "synthetic-bounded-verification", 1

    def __init__(self, *, target_match="supported", repeat_search=False, transport_failure=False, unknown=False):
        self.requests = []
        self.target_match, self.repeat_search = target_match, repeat_search
        self.transport_failure, self.unknown = transport_failure, unknown

    def generate(self, request):
        self.requests.append(request)
        if self.transport_failure:
            raise BackendError("Synthetic connection closed with unknown outcome.", code="transport_error", attempts=1)
        payload = json.loads(request.messages[-1]["content"])
        if self.unknown:
            response = action("decide", bindings=[{
                "observation_id": item["observation_id"], "subject_id": None, "status": "unknown", "evidence": [],
                "reason": "This synthetic decision does not establish the owner.", "target_match": "unknown",
                "property_name": None, "statistic": None} for item in payload["observations"]])
        elif self.repeat_search or not payload["lookup_history"]:
            response = action("search", query="collected from")
        elif payload["lookup_history"][-1]["action"] == "search":
            match = payload["lookup_history"][-1]["result"]["matches"][0]
            response = action("read", passage_ids=[match["passage_id"]])
        else:
            response = final_decision(payload, self.target_match)
        return BackendResponse(text=json.dumps(response), model="synthetic-verifier",
                               usage={"prompt_tokens": 10, "completion_tokens": 10})


def run(source=SHORT, result=None, backend=None, **options):
    return build_analysis(source, result or frozen_extraction(source), table_subject="individual specimens",
                          backend=backend or LookupBackend(), backend_identity="synthetic-verifier-v1",
                          generation=GenerationOptions(max_output_tokens=2048), **options)


AMBIGUOUS_SOURCE = MEASUREMENT + " " + MEASUREMENT + "\n\n" + RELATION


class AmbiguousGroupBackend:
    name, max_attempts = "synthetic-bounded-verification", 1

    def __init__(self, hard_error=False):
        self.requests = []
        self.hard_error = hard_error

    def generate(self, request):
        self.requests.append(request)
        payload = json.loads(request.messages[-1]["content"])
        assert payload["known_objects"] == []
        paragraph = payload["initial_context"]["passages"][0]
        full = {"passage_id": paragraph["passage_id"], "quote": paragraph["text"], "occurrence": None}
        mention = {**full, "quote": "Sample A"} if request.chunk_id == "verify-0001" else full
        response = action("decide", objects=[{"object_id": "specimen", "name": "Sample A", "kind": "sample",
            "scope": "synthetic group", "is_table_subject": True, "mentions": [mention]}], bindings=[{
                "observation_id": item["observation_id"], "subject_id": "specimen", "status": "supported",
                "evidence": [full], "reason": "Explicit synthetic statement.", "target_match": "supported",
                "property_name": "mass", "statistic": None} for item in payload["observations"]])
        if self.hard_error:
            response["bindings"][-1]["observation_id"] = "foreign-observation"
        return BackendResponse(text=json.dumps(response), model="synthetic-verifier")


def test_ambiguous_shared_object_holds_dependents_while_later_group_completes_and_resume_spends_zero(tmp_path):
    result = frozen_extraction(AMBIGUOUS_SOURCE, count=7)
    backend = AmbiguousGroupBackend()
    first = run(AMBIGUOUS_SOURCE, result, backend, trace=RunTrace(tmp_path / "first"), max_calls=2)
    assert first["status"] == "partial" and first["review_only"] is True
    assert [job["status"] for job in first["jobs"]] == ["review_required", "completed"]
    assert first["budget"]["reserved"] == len(backend.requests) == 2
    assert len(first["objects"]) == 1 and first["relations"] == []
    isolated = [item for item in first["observations"] if item["binding_status"] == "review_required"]
    assert len(isolated) == 6 and all(item["subject_id"] is None and item["binding_evidence_ids"] == [] for item in isolated)
    group = first["review_groups"][0]
    assert set(group["observation_ids"]) == {item["observation_id"] for item in isolated}
    assert json.loads(group["response_text"])["objects"][0]["object_id"] == "specimen"
    assert group["diagnostic"]["reviews"][0]["issues"][0]["visible_match_count"] == 2
    assert all(item["artifact_id"] == "verify-0002.step-01" for item in first["evidence_contexts"])
    files = {str(p.relative_to(tmp_path / "first")): p.read_bytes() for p in (tmp_path / "first").rglob("*") if p.is_file()}
    resumed_backend = AmbiguousGroupBackend()
    resumed = run(AMBIGUOUS_SOURCE, result, resumed_backend, trace=RunTrace(tmp_path / "second"), max_calls=2,
                  resume_from=tmp_path / "first/analysis/checkpoint.json")
    assert resumed["status"] == "partial" and resumed["review_only"] is True
    assert resumed_backend.requests == [] and resumed["budget"]["reserved"] == 2
    assert resumed["objects"] == first["objects"] and resumed["evidence_contexts"] == first["evidence_contexts"]
    assert (tmp_path / "second" / group["validation_details"]).is_file()
    assert {str(p.relative_to(tmp_path / "first")): p.read_bytes() for p in (tmp_path / "first").rglob("*") if p.is_file()} == files


def test_ambiguity_without_trace_retains_raw_proposal_and_alternatives():
    backend = AmbiguousGroupBackend()
    package = run(AMBIGUOUS_SOURCE, frozen_extraction(AMBIGUOUS_SOURCE), backend, max_calls=1)
    group = package["review_groups"][0]
    assert package["status"] == "partial" and package["review_only"] is True
    assert group["response_details"] is None and group["validation_details"] is None
    assert json.loads(group["response_text"])["bindings"][0]["status"] == "supported"
    assert len(group["diagnostic"]["reviews"][0]["issues"][0]["matches"]) == 2
    assert package["objects"] == [] and package["evidence_contexts"] == []


def test_ambiguity_mixed_with_hard_error_stops_without_review_or_followup_call(tmp_path):
    backend = AmbiguousGroupBackend(hard_error=True)
    package = run(AMBIGUOUS_SOURCE, frozen_extraction(AMBIGUOUS_SOURCE, count=7), backend,
                  trace=RunTrace(tmp_path), max_calls=2)
    assert package["status"] == "failed" and not package["review_only"]
    assert package["review_groups"] == [] and package["objects"] == []
    assert len(backend.requests) == 1 and package["jobs"][1]["status"] == "pending"


@pytest.mark.parametrize("damage", ["cache", "diagnostic", "coordinates", "summary"])
def test_review_cache_and_candidates_are_validated_before_pending_calls(tmp_path, damage):
    result = frozen_extraction(AMBIGUOUS_SOURCE, count=7)
    first = run(AMBIGUOUS_SOURCE, result, AmbiguousGroupBackend(), trace=RunTrace(tmp_path / "first"), max_calls=1)
    assert not first["review_only"] and first["jobs"][1]["status"] == "pending"
    path = tmp_path / "first/analysis/checkpoint.json"
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    step = checkpoint["state"]["jobs"][0]["steps"][0]
    if damage == "cache":
        (tmp_path / "first/analysis/cache/verify-0001.step-01.json").write_text("{}", encoding="utf-8")
    else:
        diagnostic_path = tmp_path / "first" / step["validation_details"]
        diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        if damage == "summary":
            diagnostic["applied_count"] += 1
        else:
            diagnostic["reviews"][0]["issues"][0]["matches"][0]["start"] += 1
        diagnostic_path.write_text(json.dumps(diagnostic), encoding="utf-8")
        if damage in {"coordinates", "summary"}:
            step["validation_hash"] = digest(diagnostic)
            checkpoint["sha256"] = digest(checkpoint["state"])
            path.write_text(json.dumps(checkpoint), encoding="utf-8")
    backend = AmbiguousGroupBackend()
    with pytest.raises(ConfigurationError):
        run(AMBIGUOUS_SOURCE, result, backend, resume_from=path, trace=RunTrace(tmp_path / "second"), max_calls=2)
    assert backend.requests == []


def test_long_document_search_read_decide_has_bounded_requests_and_cross_paragraph_evidence(tmp_path):
    backend = LookupBackend()
    result = frozen_extraction(LONG)
    before = result.model_dump(mode="json")
    package = run(LONG, result, backend, trace=RunTrace(tmp_path), input_budget=30000)
    assert len(LONG) > 60000
    assert package["status"] == "complete" and package["analysis_version"] == "0.4"
    assert len(backend.requests) == 3
    assert [step["action"] for step in package["jobs"][0]["steps"]] == ["search", "read", "decide"]
    assert all(request.chunk_id.startswith("verify-") for request in backend.requests)
    for request in backend.requests:
        payload = json.loads(request.messages[-1]["content"])
        assert "document" not in payload and LONG not in request.messages[-1]["content"]
        size = len(json.dumps(request.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")).encode())
        assert size <= 30000
    first = json.loads(backend.requests[0].messages[-1]["content"])
    assert not any(RELATION in part["text"] for part in first["initial_context"]["passages"])
    assert package["observations"][0]["subject_id"] == package["objects"][0]["object_id"]
    assert package["relations"][0]["target_id"] == package["objects"][1]["object_id"]
    quotes = {entry["quote"] for entry in package["evidence"]}
    assert MEASUREMENT in quotes and RELATION in quotes
    assert all(LONG[e["start"]:e["end"]] == e["quote"] for e in package["evidence"])
    assert result.model_dump(mode="json") == before


def test_budget_pause_replays_tools_without_resending_and_preserves_old_directory(tmp_path):
    first_backend = LookupBackend()
    first = run(backend=first_backend, max_calls=1, extraction_reserved=7, trace=RunTrace(tmp_path / "first"))
    assert first["status"] == "partial" and first["budget"]["reserved"] == 1
    assert first["jobs"][0]["status"] == "active"
    root = tmp_path / "first"
    frozen = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    second_backend = LookupBackend()
    second = run(backend=second_backend, max_calls=3, extraction_reserved=7, trace=RunTrace(tmp_path / "second"),
                 resume_from=root / "analysis/checkpoint.json")
    assert second["status"] == "complete" and len(second_backend.requests) == 2
    assert [r.artifact_id for r in second_backend.requests] == ["verify-0001.step-02", "verify-0001.step-03"]
    assert second["budget"]["reserved"] == 3 and second["budget"]["total_reserved"] == 10
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()} == frozen


def test_completed_batches_are_reused_and_only_pending_batch_is_sent(tmp_path):
    result = frozen_extraction(count=7)
    first = run(result=result, backend=LookupBackend(unknown=True), max_calls=1,
                trace=RunTrace(tmp_path / "first"))
    assert [job["status"] for job in first["jobs"]] == ["completed", "pending"]
    second_backend = LookupBackend(unknown=True)
    second = run(result=result, backend=second_backend, max_calls=2,
                 resume_from=tmp_path / "first/analysis/checkpoint.json", trace=RunTrace(tmp_path / "second"))
    assert second["status"] == "complete" and len(second_backend.requests) == 1
    assert second_backend.requests[0].chunk_id == "verify-0002"
    assert second["budget"]["reserved"] == 2


@pytest.mark.parametrize("update_hash", [False, True])
def test_damaged_cache_or_replayed_contract_stops_before_any_new_request(tmp_path, update_hash):
    run(max_calls=1, trace=RunTrace(tmp_path / "first"))
    path = tmp_path / "first/analysis/checkpoint.json"
    cache = tmp_path / "first/analysis/cache/verify-0001.step-01.json"
    response = json.loads(cache.read_text(encoding="utf-8"))
    response["text"] = '{"action":"invented_operation"}'
    cache.write_text(json.dumps(response), encoding="utf-8")
    if update_hash:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["state"]["jobs"][0]["steps"][0]["response_hash"] = digest(response)
        envelope["sha256"] = digest(envelope["state"])
        path.write_text(json.dumps(envelope), encoding="utf-8")
    backend = LookupBackend()
    with pytest.raises(ConfigurationError, match="damaged|incompatible"):
        run(backend=backend, max_calls=3, trace=RunTrace(tmp_path / "second"), resume_from=path)
    assert backend.requests == []


def test_unknown_transport_outcome_is_reserved_and_never_automatically_resent(tmp_path):
    first_backend = LookupBackend(transport_failure=True)
    first = run(backend=first_backend, max_calls=5, trace=RunTrace(tmp_path / "first"))
    assert first["status"] == "failed" and len(first_backend.requests) == 1
    assert first["jobs"][0]["steps"][0]["outcome_unknown"] is True
    assert first["jobs"][0]["steps"][0]["request_outcome"] == "unknown"
    second_backend = LookupBackend()
    second = run(backend=second_backend, max_calls=5, trace=RunTrace(tmp_path / "second"),
                 resume_from=tmp_path / "first/analysis/checkpoint.json")
    assert second["status"] == "failed" and second["budget"]["reserved"] == 1
    assert second_backend.requests == []


def test_lookup_step_limit_keeps_unknown_attribution_and_partial_status(tmp_path):
    backend = LookupBackend(repeat_search=True)
    package = run(backend=backend, max_steps=2, max_calls=10, trace=RunTrace(tmp_path))
    assert len(backend.requests) == 2
    assert package["status"] == "partial" and package["jobs"][0]["status"] == "limited"
    assert package["jobs"][0]["error_code"] == "step_limit"
    assert package["observations"][0]["binding_status"] == "unknown"
    assert package["observations"][0]["subject_id"] is None
    assert "does not prove absence" in package["observations"][0]["reason"]


def test_empty_observations_need_no_verification_generation():
    backend = LookupBackend()
    package = run(result=frozen_extraction(count=0), backend=backend)
    assert package["status"] == "complete" and package["observations"] == []
    assert package["objects"] == [] and package["jobs"] == []
    assert package["budget"]["reserved"] == 0 and backend.requests == []


def test_target_field_mismatch_is_retained_without_rewriting_original_value():
    original = frozen_extraction()
    before = deepcopy(original.model_dump(mode="json"))
    package = run(result=original, backend=LookupBackend(target_match="unsupported"))
    item = package["observations"][0]
    assert package["status"] == "complete"
    assert item["value"] == "17" and item["unit"] == "g" and item["conditions"] is None
    assert item["binding_status"] == "supported" and item["target_match"] == "unsupported"
    assert any(blocker["code"] == "target_field_unsupported" for blocker in item["blockers"])
    assert original.model_dump(mode="json") == before


def test_offline_resume_check_spends_no_calls(tmp_path):
    run(max_calls=1, trace=RunTrace(tmp_path / "first"))
    backend = LookupBackend()
    checked = run(backend=backend, max_calls=3, check_only=True,
                  resume_from=tmp_path / "first/analysis/checkpoint.json")
    assert checked["status"] == "checked" and checked["budget"]["reserved"] == 1
    assert checked["pending_jobs"] == 1 and backend.requests == []


def test_framework_json_instruction_preflight_cannot_be_satisfied_by_source_text(monkeypatch, tmp_path):
    from attestextract import verification

    source = SHORT + "\n\nThe term JSON appears here as untrusted source data."
    backend = LookupBackend()
    monkeypatch.setattr(verification, "_SYSTEM", "Return one action satisfying response_contract.")
    with pytest.raises(ConfigurationError, match="framework verification prompt.*JSON"):
        run(source, backend=backend, trace=RunTrace(tmp_path))
    assert backend.requests == []
    assert not (tmp_path / "analysis/checkpoint.json").exists()
    assert not (tmp_path / "analysis/requests").exists()


def test_previous_analysis_revision_is_rejected_before_any_request(monkeypatch, tmp_path):
    from attestextract import verification

    with monkeypatch.context() as old_revision:
        old_revision.setattr(verification, "REVISION", "bounded-source-verification-v2")
        run(max_calls=1, trace=RunTrace(tmp_path / "old"))
    checkpoint = tmp_path / "old/analysis/checkpoint.json"
    original = checkpoint.read_bytes()
    backend = LookupBackend()
    with pytest.raises(ConfigurationError, match="incompatible analysis revision"):
        run(backend=backend, resume_from=checkpoint, trace=RunTrace(tmp_path / "new"))
    assert backend.requests == [] and checkpoint.read_bytes() == original


def test_unversioned_prompt_change_is_rejected_by_request_identity(monkeypatch, tmp_path):
    from attestextract import verification

    run(max_calls=1, trace=RunTrace(tmp_path / "old"))
    checkpoint = tmp_path / "old/analysis/checkpoint.json"
    monkeypatch.setattr(verification, "_SYSTEM", verification._SYSTEM + " Changed instruction.")
    backend = LookupBackend()
    with pytest.raises(ConfigurationError, match="incompatible or damaged"):
        run(backend=backend, resume_from=checkpoint, trace=RunTrace(tmp_path / "new"))
    assert backend.requests == []


def test_complete_http_rejection_is_distinct_from_unknown_transport_and_not_resent(tmp_path):
    sent = []
    events = []
    trace = RunTrace(tmp_path / "first", on_event=events.append)

    def reject(request):
        sent.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"code": "invalid_request_error",
                                                   "message": "Rejected synthetic request."}})

    backend = OpenAICompatibleBackend("https://synthetic.invalid/v1", "synthetic-verifier",
        api_key="synthetic-key", structured_output="json_object", max_attempts=1,
        transport=httpx.MockTransport(reject), trace=trace)
    first = run(backend=backend, max_calls=5, trace=trace)
    step = first["jobs"][0]["steps"][0]
    assert first["status"] == "failed" and len(sent) == 1
    assert first["budget"]["reserved"] == 1
    assert step["outcome_unknown"] is False and step["request_outcome"] == "http_error_received"
    assert step["http_status"] == 400 and step["http_attempts"] == 1
    assert step["http_details"] == "http/verify-0001.step-01.attempt-1.error.json"
    assert "usage" not in step  # A known HTTP outcome is not a zero-cost claim.
    diagnostic = json.loads((trace.directory / step["http_details"]).read_text(encoding="utf-8"))
    assert diagnostic["status_code"] == 400
    event = next(item for item in events if item.get("stage") == "analysis" and item.get("event") == "failed")
    assert event["http_status"] == 400 and event["request_outcome"] == "http_error_received"
    original = {str(path.relative_to(trace.directory)): path.read_bytes()
                for path in trace.directory.rglob("*") if path.is_file()}
    second = run(backend=backend, max_calls=5, trace=RunTrace(tmp_path / "second"),
                 resume_from=trace.directory / "analysis/checkpoint.json")
    assert second["status"] == "failed" and second["budget"]["reserved"] == 1 and len(sent) == 1
    assert {str(path.relative_to(trace.directory)): path.read_bytes()
            for path in trace.directory.rglob("*") if path.is_file()} == original


@pytest.mark.parametrize("adapter_rejects", [False, True])
def test_complete_invalid_model_output_has_known_response_outcome(tmp_path, adapter_rejects):
    class InvalidOutputBackend(LookupBackend):
        def generate(self, request):
            self.requests.append(request)
            if adapter_rejects:
                raise InvalidResponseError("Invalid synthetic completion.", status_code=200,
                    response_metadata={"usage": {"prompt_tokens": 10, "completion_tokens": 2}})
            return BackendResponse(text="not a structured answer", model="synthetic-verifier")

    backend = InvalidOutputBackend()
    package = run(backend=backend, trace=RunTrace(tmp_path))
    step = package["jobs"][0]["steps"][0]
    assert package["status"] == "failed" and len(backend.requests) == 1
    assert step["outcome_unknown"] is False and step["request_outcome"] == "response_received"
    assert package["budget"]["reserved"] == 1


@pytest.mark.parametrize("details", ["../private.json", "https://synthetic.invalid/error.json",
                                     "http/error.json?token=synthetic-secret", "http/../private.json"])
def test_backend_error_cannot_publish_an_arbitrary_diagnostic_path(tmp_path, details):
    class UnsafeDetailsBackend(LookupBackend):
        def generate(self, request):
            self.requests.append(request)
            raise BackendError("Synthetic error.", status_code=400,
                http_diagnostics={"layer": "http", "status_code": 400}, http_details=details)

    package = run(backend=UnsafeDetailsBackend(), trace=RunTrace(tmp_path))
    step = package["jobs"][0]["steps"][0]
    assert step["request_outcome"] == "http_error_received"
    assert "http_details" not in step


def test_reference_bindings_are_diagnosed_without_retry_or_partial_application(tmp_path):
    class OverbroadBackend(LookupBackend):
        def generate(self, request):
            self.requests.append(request)
            payload = json.loads(request.messages[-1]["content"])
            targets = payload["target_scope"]["observation_ids"]
            references = [item["reference_observation_id"] for item in payload["extraction_hints"]]
            assert len(targets) == 6 and len(references) == 12
            assert all("observation_id" not in item for item in payload["extraction_hints"])
            self.references = references
            binding = {"subject_id": None, "status": "unknown", "evidence": [], "reason": "Unresolved.",
                       "target_match": "unknown", "property_name": None, "statistic": None}
            return BackendResponse(model="synthetic-verifier", text=json.dumps(action("decide",
                bindings=[{**binding, "observation_id": key} for key in [*targets, *references]])))

    backend, events = OverbroadBackend(), []
    original = frozen_extraction(count=18)
    before = original.model_dump(mode="json")
    package = run(result=original, backend=backend, max_calls=48,
                  trace=RunTrace(tmp_path / "first", on_event=events.append))
    assert len(backend.requests) == 1 and package["budget"]["reserved"] == 1
    assert [job["status"] for job in package["jobs"]] == ["failed", "pending", "pending"]
    assert package["objects"] == package["relations"] == []
    assert original.model_dump(mode="json") == before
    step = package["jobs"][0]["steps"][0]
    assert step["error_code"] == "binding_scope_mismatch"
    assert step["request_outcome"] == "response_received"
    assert step["validation_details"] == "analysis/validation/verify-0001.step-01.errors.json"
    details = json.loads((tmp_path / "first" / step["validation_details"]).read_text(encoding="utf-8"))
    assert details["expected_count"] == 6 and details["returned_count"] == 18
    assert set(details["reference_ids"]) == set(backend.references)
    failed = next(e for e in events if e.get("stage") == "analysis" and e.get("event") == "failed")
    assert failed["validation_summary"] == step["validation_summary"]
    assert not any(key in json.dumps(failed) for key in backend.references)
    # Ordinary resume does not reopen a failed decision, even with more budget.
    checked = run(result=original, backend=LookupBackend(), max_calls=48, check_only=True,
                  resume_from=tmp_path / "first/analysis/checkpoint.json")
    assert checked["jobs"][0]["status"] == "failed" and checked["budget"]["reserved"] == 1
    assert checked["pending_jobs"] == 2


def test_internal_validation_error_is_classified_and_stops_without_retry(monkeypatch, tmp_path):
    from attestextract import verification

    def broken_state(*args, **kwargs):
        raise ValueError("Private internal-state details must not become a model error.")

    monkeypatch.setattr(verification, "_advance", broken_state)
    backend = LookupBackend()
    package = run(backend=backend, trace=RunTrace(tmp_path), max_calls=5)
    step = package["jobs"][0]["steps"][0]
    assert step["error_code"] == "verification_state_error"
    assert step["validation_summary"]["category"] == "internal_state"
    assert "Private internal-state" not in step["error"]
    assert len(backend.requests) == 1 and package["budget"]["reserved"] == 1


def test_invalid_target_state_fails_preflight_without_becoming_context_limit(monkeypatch, tmp_path):
    from attestextract import verification

    original = verification._input_observations

    def duplicate_batch(*args):
        observations, quarantine = original(*args)
        return [observations[0], deepcopy(observations[0])], quarantine

    monkeypatch.setattr(verification, "_input_observations", duplicate_batch)
    backend = LookupBackend()
    with pytest.raises(ConfigurationError, match="input state is invalid"):
        run(backend=backend, trace=RunTrace(tmp_path))
    assert backend.requests == [] and not (tmp_path / "analysis/checkpoint.json").exists()


@pytest.mark.parametrize("damage", [None, "content", "path", "missing"])
def test_failed_validation_artifact_is_verified_and_copied_on_resume(tmp_path, damage):
    class InvalidBackend(LookupBackend):
        def generate(self, request):
            self.requests.append(request)
            return BackendResponse(text=json.dumps(action("decide")), model="synthetic-verifier")

    first = run(backend=InvalidBackend(), trace=RunTrace(tmp_path / "first"))
    step = first["jobs"][0]["steps"][0]
    artifact = tmp_path / "first" / step["validation_details"]
    checkpoint = tmp_path / "first/analysis/checkpoint.json"
    if damage == "content":
        artifact.write_text('{}', encoding="utf-8")
    elif damage == "missing":
        artifact.unlink()
    elif damage == "path":
        envelope = json.loads(checkpoint.read_text(encoding="utf-8"))
        envelope["state"]["jobs"][0]["steps"][0]["validation_details"] = "../outside.json"
        envelope["sha256"] = digest(envelope["state"])
        checkpoint.write_text(json.dumps(envelope), encoding="utf-8")
    backend = LookupBackend()
    if damage:
        with pytest.raises(ConfigurationError, match="damaged or incompatible"):
            run(backend=backend, resume_from=checkpoint, trace=RunTrace(tmp_path / "second"))
    else:
        resumed = run(backend=backend, resume_from=checkpoint, trace=RunTrace(tmp_path / "second"))
        assert resumed["status"] == "failed" and resumed["budget"]["reserved"] == 1
        assert (tmp_path / "second" / step["validation_details"]).read_bytes() == artifact.read_bytes()
    assert backend.requests == []


@pytest.mark.parametrize("redact_first", [False, True])
def test_redacted_validation_artifact_hash_can_be_resumed(tmp_path, redact_first):
    secret = "synthetic-secret-in-invalid-id"

    class InvalidBackend(LookupBackend):
        def generate(self, request):
            self.requests.append(request)
            return BackendResponse(text=json.dumps(action("decide", bindings=[{
                "observation_id": secret, "subject_id": None, "status": "unknown", "evidence": [],
                "reason": "Unknown.", "target_match": "unknown", "property_name": None, "statistic": None,
            }])), model="synthetic-verifier")

    first = run(backend=InvalidBackend(), trace=RunTrace(tmp_path / "first", secrets=[secret] if redact_first else []))
    details = first["jobs"][0]["steps"][0]["validation_details"]
    assert (secret not in (tmp_path / "first" / details).read_text(encoding="utf-8")) == redact_first
    backend = LookupBackend()
    resumed = run(backend=backend, resume_from=tmp_path / "first/analysis/checkpoint.json",
                  trace=RunTrace(tmp_path / "second", secrets=[secret]))
    assert resumed["status"] == "failed" and backend.requests == []
    assert secret not in (tmp_path / "second" / details).read_text(encoding="utf-8")
    checked = run(backend=backend, check_only=True,
                  resume_from=tmp_path / "second/analysis/checkpoint.json")
    assert checked["jobs"][0]["status"] == "failed" and checked["budget"]["reserved"] == 1
