"""Synthetic public acceptance of bounded, resumable source verification."""

from copy import deepcopy
import hashlib
import json

import pytest

from attestextract import RunTrace, extract
from attestextract.analysis import build_analysis
from attestextract.backends import ReplayBackend
from attestextract.errors import BackendError, ConfigurationError
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


def test_long_document_search_read_decide_has_bounded_requests_and_cross_paragraph_evidence(tmp_path):
    backend = LookupBackend()
    result = frozen_extraction(LONG)
    before = result.model_dump(mode="json")
    package = run(LONG, result, backend, trace=RunTrace(tmp_path), input_budget=30000)
    assert len(LONG) > 60000
    assert package["status"] == "complete" and package["analysis_version"] == "0.2"
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
