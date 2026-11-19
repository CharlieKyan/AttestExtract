"""The received answer is an audit record even when its scientific attribution fails."""

import json

import pytest

from attestextract.admission_receipts import collect_receipts
from attestextract.entity_first import run_entity_first
from attestextract.models import BackendResponse


SOURCE = "Unit A carried 80 boxes."


class Recorder:
    def __init__(self, *, retry=False, escaped_secret=False):
        self.retry = retry
        self.escaped_secret = escaped_secret
        self.replies = {}

    def generate(self, request):
        packet = json.loads(request.messages[1]["content"])
        if packet["stage"] == "inventory":
            text = '{"objects":[]}'
        elif self.retry and request.format_attempt == 1:
            text = "{"
        else:
            window = packet["windows"][0]["id"]
            citation = {"window": window, "quote": "80"}
            # Deliberate missing source and duplicate entries. Both still belong to this answer.
            fact = {"field": "amount", "value": "99", "value_source": citation, "property_source": citation,
                    "owner": None, "owner_source": None, "link_source": None, "fit_source": None,
                    "purpose": "unspecified", "fit": "unknown", "direct": False, "reason": "Unknown."}
            text = json.dumps({"facts": [fact, fact], "nominations": []})
            if self.escaped_secret:
                text = text.replace("Unknown.", r"\u0073ecret-example")
        self.replies[request.artifact_stem] = (request, text)
        return BackendResponse(text=text, model="fixture", usage={"total_tokens": 2})


def recorded(backend):
    return run_entity_first(SOURCE, {"amount": "Item count"}, "units", backend, max_calls=8,
                            format_retries=int(backend.retry), secrets=["secret-example"])


def legacy(run, report, backend, *, responses=False):
    del report["admission_receipt_version"], report["admission_run_id"], report["admission_task"]
    report["snapshot_id"] = "fixture-snapshot"
    requests = run / "diagnostics/requests"
    replies = run / "diagnostics/responses"
    requests.mkdir(parents=True)
    replies.mkdir()
    for call in report["calls"]:
        call.pop("assignment_receipt", None)
        stem = call["artifact_stem"]
        request, text = backend.replies[stem]
        payload = ({"input": [{"type": "message", **m} for m in request.messages]} if responses
                   else {"messages": request.messages})
        (requests / f"{stem}.request.json").write_text(json.dumps(payload), encoding="utf-8")
        (replies / f"{stem}.txt").write_text(text, encoding="utf-8")
        meta = {"model": "fixture", "finish_reason": None, "attempts": 1,
                "usage": {"total_tokens": 2}, "format_attempt": request.format_attempt}
        (replies / f"{stem}.meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_new_report_retains_exact_proposals_before_scientific_validation():
    backend = Recorder()
    report = recorded(backend)
    assert not report["observations"]
    receipts, coverage = collect_receipts(SOURCE, json.loads(json.dumps(report)))
    (receipt,) = receipts
    assert receipt["answer"] == json.loads(backend.replies[receipt["artifact_stem"]][1])
    assert len(receipt["answer"]["facts"]) == 2 and receipt["call"] == 2
    request = backend.replies[receipt["artifact_stem"]][0]
    assert receipt["packet"] == json.loads(request.messages[1]["content"])
    assert coverage[0]["status"] == "complete" and coverage[0]["proposals"] == 2
    another, _ = collect_receipts(SOURCE, recorded(Recorder()))
    assert another[0]["id"] != receipt["id"]
    with pytest.raises(ValueError, match="source"):
        collect_receipts(SOURCE + " ", report)


@pytest.mark.parametrize("responses", [False, True])
def test_old_retry_uses_the_actual_attempt_and_original_user_packet(tmp_path, responses):
    backend = Recorder(retry=True)
    report = recorded(backend)
    legacy(tmp_path, report, backend, responses=responses)
    receipts, coverage = collect_receipts(SOURCE, report, tmp_path)
    assert [r["status"] for r in coverage] == ["gap", "complete"]
    assert coverage[0]["reason"] == "invalid_response_contract"
    assert receipts[0]["id"] == "fixture-snapshot/entity-attributes-000003.format-2"
    assert receipts[0]["packet"]["windows"][0]["text"] == SOURCE
    assert len(receipts[0]["answer"]["facts"]) == 2


@pytest.mark.parametrize("failure,reason", [
    ("missing", "missing_call_artifact"), ("meta", "response_metadata_mismatch"),
    ("digest", "request_digest_mismatch"), ("range", "primary_window_mismatch"),
    ("count", "reported_fact_count_mismatch"), ("truncated", "truncated_response"),
    ("invalid", "invalid_call_artifact"), ("stem", "invalid_artifact_stem"),
    ("envelope", "unsupported_request_envelope"),
])
def test_unverified_answers_report_a_gap_not_a_verified_empty_answer(tmp_path, failure, reason):
    backend = Recorder()
    report = recorded(backend)
    legacy(tmp_path, report, backend)
    call = report["calls"][-1]
    text_path = tmp_path / "diagnostics/responses" / (call["artifact_stem"] + ".txt")
    if failure == "missing":
        text_path.unlink()
    elif failure == "meta":
        call["attempts"] += 1
    elif failure == "digest":
        call["request_digest"] = "bad-digest"
    elif failure == "range":
        report["blocks"][0]["end"] += 1
    elif failure == "count":
        text_path.write_text('{"facts":[],"nominations":[]}')
    elif failure == "invalid":
        text_path.write_text('{"facts":[')
    elif failure == "stem":
        call["artifact_stem"] = "../unrelated"
    elif failure == "envelope":
        request_path = tmp_path / "diagnostics/requests" / (call["artifact_stem"] + ".request.json")
        request_path.write_text("[]", encoding="utf-8")
    else:
        call["state"] = "truncated_response"
    receipts, coverage = collect_receipts(SOURCE, report, tmp_path)
    assert receipts == []
    assert coverage[0]["proposals"] is None and coverage[0]["reason"] == reason


def test_json_escaped_credential_never_reaches_the_archive():
    report = recorded(Recorder(escaped_secret=True))
    assert "secret-example" not in json.dumps(report)
    receipts, coverage = collect_receipts(SOURCE, report)
    assert not receipts and coverage[0]["reason"] == "credential_in_response"
