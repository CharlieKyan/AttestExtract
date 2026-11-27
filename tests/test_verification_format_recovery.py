"""One explicit verification regeneration preserves history and all call limits."""

import json

import pytest

from niceextract import RunTrace
from niceextract.errors import BackendError, ConfigurationError
from niceextract.execution import digest
from niceextract.models import BackendResponse
from niceextract.verification import build_verification
from test_verification_loop import SHORT, MEASUREMENT, action, frozen_extraction, cite


class Answers:
    name, max_attempts = "synthetic-verification-recovery", 1

    def __init__(self, *modes):
        self.modes, self.requests = iter(modes), []

    def generate(self, request):
        self.requests.append(request)
        mode = next(self.modes)
        payload = json.loads(request.messages[-1]["content"])
        if mode == "transport":
            raise BackendError("Synthetic unknown transport outcome", code="transport_error", attempts=1)
        if mode == "interrupt":
            raise KeyboardInterrupt()
        if mode == "search":
            answer = action("search", query="Sample")
        else:
            evidence = cite(payload, MEASUREMENT)
            answer = action("decide", objects=[{"object_id": "specimen", "name": "Sample A", "kind": "sample",
                "scope": "Synthetic specimen", "is_table_subject": True, "mentions": [evidence]}], bindings=[{
                "observation_id": item["observation_id"], "subject_id": "specimen", "status": "supported",
                "evidence": [evidence], "reason": "The source names the measured sample.",
                "target_match": "supported", "property_name": "mass", "statistic": None}
                for item in payload["observations"]])
        if mode == "scope":
            answer["bindings"] = []
        if mode == "contract":
            answer["neighbors"] = "not-an-integer"
        if mode == "wrong_enum":
            answer["relations"] = [{"source_kind": "object", "source_id": "specimen", "target_id": "specimen",
                                    "predicate": "private unrecognized relation", "evidence": [evidence]}]
        text = json.dumps(answer)
        if mode == "bad":
            text = text[:-1] + ', "objects": []}'
        return BackendResponse(text=text, finish_reason="length" if mode == "truncated" else "stop")


def run(path, backend, *, count=1, secrets=(), **kwargs):
    return build_verification(SHORT, frozen_extraction(SHORT, count), table_subject="individual specimens",
        backend=backend, backend_identity="synthetic-recovery-v1", trace=RunTrace(path, secrets=secrets), **kwargs)


def files(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_unknown_relation_is_reviewed_without_regenerating_the_bindings_and_replays(tmp_path):
    backend = Answers("wrong_enum")
    package = run(tmp_path / "first", backend, count=2, format_retries=1, max_calls=2)
    assert len(backend.requests) == package["budget"]["reserved"] == 1
    request, = backend.requests
    contract = json.loads(request.messages[-1]["content"])["response_contract"]
    assert "relations" in contract["properties"] and "name_resolution" not in json.dumps(contract)
    assert "name_resolution" not in request.messages[0]["content"]
    assert package["format_retries_reserved"] == 0
    assert package["status"] == "partial" and package["review_only"]
    assert package["relations"] == []
    assert [obs["binding_status"] for obs in package["observations"]] == ["supported", "supported"]
    group, = package["review_groups"]
    review, = group["diagnostic"]["reviews"]
    assert review["proposal_type"] == "relation" and review["path"] == "relations[0]"
    assert review["raw_proposal"] == json.loads(group["response_text"])["relations"][0]
    assert group["observation_ids"] == []
    resumed = Answers()
    verified = run(tmp_path / "replayed", resumed, count=2, format_retries=1, max_calls=2,
                   resume_from=tmp_path / "first/analysis/checkpoint.json")
    assert resumed.requests == [] and verified["review_groups"] == package["review_groups"]


def migrate_fixture(path):
    checkpoint = path / "analysis/checkpoint.json"
    data = json.loads(checkpoint.read_text(encoding="utf-8"))
    state = data["state"]
    state["version"] = "0.4"
    state.pop("format_retries")
    for job in state["jobs"]:
        for step in job["steps"]:
            step.pop("rejected_response_hash", None)
    data["sha256"] = digest(state)
    checkpoint.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("invalid", ["bad", "contract"])
def test_complete_format_failure_regenerates_once_without_repair(tmp_path, invalid):
    backend = Answers(invalid, "good")
    result = run(tmp_path / "first", backend, format_retries=1, max_calls=2)
    assert result["status"] == "complete" and result["budget"]["reserved"] == 2
    steps = result["jobs"][0]["steps"]
    assert steps[0]["status"] == "failed" and steps[1]["retry_of"] == 1
    assert result["format_retries_reserved"] == 1
    original, retry = backend.requests
    assert original.output_schema == retry.output_schema and original.generation == retry.generation
    original_payload, retry_payload = [json.loads(r.messages[-1]["content"]) for r in backend.requests]
    assert retry_payload.pop("remaining_steps") == original_payload.pop("remaining_steps") - 1
    assert retry_payload == original_payload
    assert "Each property name must occur exactly once" in retry.messages[0]["content"]
    assert (tmp_path / "first/analysis/responses/verify-0001.step-01.txt").is_file()
    assert (tmp_path / "first/analysis/responses/verify-0001.step-02.txt").is_file()
    again = Answers()
    replay = run(tmp_path / "again", again, format_retries=0, max_calls=2,
                 resume_from=tmp_path / "first/analysis/checkpoint.json")
    assert again.requests == [] and replay["budget"]["reserved"] == 2
    assert replay["objects"] == result["objects"]


def test_legacy_tail_recovery_reuses_completed_jobs_and_preserves_history(tmp_path):
    first = Answers("good", "bad")
    old = tmp_path / "old"
    result = run(old, first, count=13, max_calls=4)
    assert [j["status"] for j in result["jobs"]] == ["completed", "failed", "pending"]
    migrate_fixture(old)
    before = files(old)
    check = run(tmp_path / "check", Answers(), count=13, max_calls=4, check_only=True,
        format_retries=1, retry_failed_jobs=["verify-0002"], resume_from=old / "analysis/checkpoint.json")
    assert check["retry_jobs"] == check["retryable_jobs"] == ["verify-0002"]
    assert check["budget"]["reserved"] == 2 and check["reusable_jobs"] == 1
    backend = Answers("good", "good")
    recovered = run(tmp_path / "new", backend, count=13, max_calls=4, format_retries=1,
        retry_failed_jobs=["verify-0002"], resume_from=old / "analysis/checkpoint.json")
    assert recovered["status"] == "complete" and recovered["budget"]["reserved"] == 4
    assert [r.chunk_id for r in backend.requests] == ["verify-0002", "verify-0003"]
    assert backend.requests[0].artifact_id == "verify-0002.step-02"
    assert files(old) == before
    assert json.loads((tmp_path / "new/analysis/checkpoint.json").read_text())["state"]["version"] == "0.5"


def test_ordinary_resume_cannot_repair_failed_job_or_allow_late_reordering(tmp_path):
    old = tmp_path / "old"
    run(old, Answers("good", "bad"), count=13, max_calls=4)
    backend = Answers("good")
    continued = run(tmp_path / "continued", backend, count=13, max_calls=4, format_retries=1,
                    resume_from=old / "analysis/checkpoint.json")
    assert continued["status"] == "failed" and [r.chunk_id for r in backend.requests] == ["verify-0003"]
    blocked = Answers()
    with pytest.raises(ConfigurationError, match="tail"):
        run(tmp_path / "blocked", blocked, count=13, max_calls=5, format_retries=1,
            retry_failed_jobs=["verify-0002"], resume_from=tmp_path / "continued/analysis/checkpoint.json")
    assert blocked.requests == []


@pytest.mark.parametrize("mode", ["transport", "truncated", "scope", "interrupt"])
def test_nonformat_failures_do_not_regenerate_or_allow_explicit_recovery(tmp_path, mode):
    backend = Answers(mode)
    package = run(tmp_path / "old", backend, format_retries=1, max_calls=3)
    assert package["status"] == "failed" and len(backend.requests) == 1
    blocked = Answers()
    with pytest.raises(ConfigurationError):
        run(tmp_path / "blocked", blocked, max_calls=4, format_retries=1,
            retry_failed_jobs=["verify-0001"], resume_from=tmp_path / "old/analysis/checkpoint.json")
    assert blocked.requests == []


def test_spent_allowance_cannot_reset_on_resume_even_after_interrupt(tmp_path):
    backend = Answers("bad", "interrupt")
    package = run(tmp_path / "old", backend, format_retries=1, max_calls=2)
    assert package["status"] == "failed" and package["format_retries_reserved"] == 1
    assert package["budget"]["reserved"] == 2
    resumed = Answers()
    replay = run(tmp_path / "again", resumed, format_retries=1, max_calls=9,
                 resume_from=tmp_path / "old/analysis/checkpoint.json")
    assert resumed.requests == [] and replay["status"] == "failed"
    with pytest.raises(ConfigurationError):
        run(tmp_path / "blocked", resumed, format_retries=1, max_calls=9,
            retry_failed_jobs=["verify-0001"], resume_from=tmp_path / "old/analysis/checkpoint.json")


def test_step_and_total_call_limits_include_regeneration(tmp_path):
    backend = Answers("search", "search", "search", "bad")
    package = run(tmp_path / "last", backend, format_retries=1, max_calls=10, max_steps=4)
    assert package["status"] == "failed" and len(backend.requests) == 4
    assert package["retryable_jobs"] == [] and package["format_retries_reserved"] == 0
    exhausted = Answers("bad")
    package = run(tmp_path / "budget", exhausted, format_retries=1, max_calls=1)
    assert package["status"] == "failed" and len(exhausted.requests) == 1
    assert package["budget"]["reserved"] == 1
    recovered = Answers("good")
    package = run(tmp_path / "increased", recovered, format_retries=1, max_calls=2,
                  retry_failed_jobs=["verify-0001"], resume_from=tmp_path / "budget/analysis/checkpoint.json")
    assert package["status"] == "complete" and package["budget"]["reserved"] == 2


@pytest.mark.parametrize("name", ["cache/verify-0001.step-01.json", "responses/verify-0002.step-01.txt"])
def test_cache_and_failed_response_damage_stop_before_new_calls(tmp_path, name):
    run(tmp_path / "old", Answers("good", "bad"), count=13, max_calls=4)
    (tmp_path / "old/analysis" / name).write_text("{}", encoding="utf-8")
    backend = Answers()
    with pytest.raises(ConfigurationError):
        run(tmp_path / "new", backend, count=13, max_calls=4, format_retries=1,
            retry_failed_jobs=["verify-0002"], resume_from=tmp_path / "old/analysis/checkpoint.json")
    assert backend.requests == []


def test_second_bad_response_retains_both_and_never_accepts_prefix(tmp_path):
    backend = Answers("bad", "bad")
    package = run(tmp_path / "old", backend, format_retries=1, max_calls=3)
    assert package["status"] == "failed" and package["objects"] == []
    assert package["format_retries_reserved"] == 1 and len(backend.requests) == 2
    assert package["observations"][0]["binding_status"] == "failed"
    resumed = Answers()
    replay = run(tmp_path / "again", resumed, format_retries=1, max_calls=8,
                 resume_from=tmp_path / "old/analysis/checkpoint.json")
    assert resumed.requests == [] and replay["status"] == "failed"


def test_retry_context_limit_stops_without_sending_pending_jobs_or_suggesting_retry(tmp_path):
    probe = Answers("bad")
    run(tmp_path / "probe", probe, count=7, max_calls=3)
    size = len(json.dumps(probe.requests[0].model_dump(mode="json"), ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8"))
    backend = Answers("bad")
    package = run(tmp_path / "limited", backend, count=7, max_calls=3,
                  format_retries=1, input_budget=size)
    assert len(backend.requests) == 1 and package["status"] == "failed"
    assert [j["status"] for j in package["jobs"]] == ["failed", "pending"]
    assert package["retryable_jobs"] == []
    checked = run(tmp_path / "check", Answers(), count=7, max_calls=3, input_budget=size,
                  check_only=True, resume_from=tmp_path / "limited/analysis/checkpoint.json")
    assert checked["retryable_jobs"] == []


def test_rejected_copy_hash_tracks_new_credential_redaction(tmp_path):
    class SecretAnswer(Answers):
        def generate(self, request):
            answer = super().generate(request)
            return answer.model_copy(update={"text": answer.text.replace(
                'Where did this specimen originate?', 'synthetic-new-secret')})
    old = tmp_path / "old"
    run(old, SecretAnswer("bad"), max_calls=2)
    before = files(old)
    second = run(tmp_path / "second", Answers("good"), secrets=["synthetic-new-secret"],
                 max_calls=2, format_retries=1, retry_failed_jobs=["verify-0001"],
                 resume_from=old / "analysis/checkpoint.json")
    assert second["status"] == "complete" and files(old) == before
    copied = (tmp_path / "second/analysis/responses/verify-0001.step-01.txt").read_text(encoding="utf-8")
    assert "synthetic-new-secret" not in copied and "[REDACTED]" in copied
    replay = Answers()
    checked = run(tmp_path / "third", replay, max_calls=2, check_only=True,
                  resume_from=tmp_path / "second/analysis/checkpoint.json")
    assert checked["reusable_jobs"] == 1 and replay.requests == []


def test_regenerated_lookup_can_continue_to_decision_within_same_step_limit(tmp_path):
    backend = Answers("bad", "search", "good")
    package = run(tmp_path / "first", backend, max_calls=3, format_retries=1)
    assert package["status"] == "complete" and package["budget"]["reserved"] == 3
    assert [step.get("action") for step in package["jobs"][0]["steps"]] == [None, "search", "decide"]
    assert "For search/read, keep objects, relations and bindings empty" in backend.requests[1].messages[0]["content"]
    assert json.loads(backend.requests[2].messages[-1]["content"])["remaining_steps"] == 2
    replay = Answers()
    again = run(tmp_path / "again", replay, max_calls=3, format_retries=1,
                resume_from=tmp_path / "first/analysis/checkpoint.json")
    assert replay.requests == [] and again["status"] == "complete"


def test_invalid_retry_metadata_fails_before_pending_generation(tmp_path):
    run(tmp_path / "old", Answers("bad"), count=7, max_calls=3)
    path = tmp_path / "old/analysis/checkpoint.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["state"]["jobs"][0]["steps"][0]["validation_summary"] = []
    data["sha256"] = digest(data["state"])
    path.write_text(json.dumps(data), encoding="utf-8")
    backend = Answers()
    with pytest.raises(ConfigurationError):
        run(tmp_path / "new", backend, count=7, max_calls=3, resume_from=path)
    assert backend.requests == []


def test_used_feedback_survives_later_redaction_that_moves_json_error_column(tmp_path):
    class SyntaxAnswer(Answers):
        def generate(self, request):
            self.requests.append(request)
            return BackendResponse(text='{"action":"decide","question":"synthetic-new-secret","objects":[],}',
                                   finish_reason="stop")
    old = tmp_path / "old"
    run(old, SyntaxAnswer(), max_calls=2)
    original_hashes = files(old)
    second = run(tmp_path / "second", Answers("good"), max_calls=2, format_retries=1,
                 retry_failed_jobs=["verify-0001"], resume_from=old / "analysis/checkpoint.json")
    assert second["status"] == "complete"
    frozen = second["jobs"][0]["steps"][1]["retry_feedback"]
    assert "column" in frozen
    no_calls = Answers()
    third = run(tmp_path / "third", no_calls, max_calls=2, secrets=["synthetic-new-secret"],
                resume_from=tmp_path / "second/analysis/checkpoint.json")
    assert third["status"] == "complete" and no_calls.requests == []
    assert third["jobs"][0]["steps"][1]["retry_feedback"] == frozen
    copied = (tmp_path / "third/analysis/responses/verify-0001.step-01.txt").read_text(encoding="utf-8")
    assert "[REDACTED]" in copied and "synthetic-new-secret" not in copied
    fourth = run(tmp_path / "fourth", no_calls, max_calls=2, check_only=True,
                 resume_from=tmp_path / "third/analysis/checkpoint.json")
    assert fourth["reusable_jobs"] == 1 and fourth["budget"]["reserved"] == 2
    assert no_calls.requests == [] and files(old) == original_hashes


@pytest.mark.parametrize("damage", ["missing", "extra", "phase", "position", "nonretry"])
def test_retry_feedback_snapshot_is_checked_before_any_pending_call(tmp_path, damage):
    run(tmp_path / "old", Answers("bad", "good"), count=7, max_calls=2, format_retries=1)
    path = tmp_path / "old/analysis/checkpoint.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    steps = data["state"]["jobs"][0]["steps"]
    feedback = steps[1]["retry_feedback"]
    if damage == "missing":
        steps[1].pop("retry_feedback")
    elif damage == "extra":
        feedback["raw_answer"] = "Never expose a rejected source value."
    elif damage == "phase":
        feedback["phase"] = "response_contract"
    elif damage == "position":
        feedback["errors"][0]["member_index"] += 1
    else:
        steps[0]["retry_feedback"] = feedback
    data["sha256"] = digest(data["state"])
    path.write_text(json.dumps(data), encoding="utf-8")
    backend = Answers()
    with pytest.raises(ConfigurationError):
        run(tmp_path / "new", backend, count=7, max_calls=3, resume_from=path)
    assert backend.requests == []
