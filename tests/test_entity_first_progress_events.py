"""Block progress describes real work and shared generation reservations."""

import json

import pytest

from attestextract.entity_first import run_entity_first
from attestextract.models import BackendResponse
from test_entity_first_engine import card, fact
from test_entity_first_truncation import DOCUMENT


def run(source, reply, events, **options):
    class Backend:
        def generate(self, request):
            packet = json.loads(request.messages[1]["content"])
            assert events[-1]["event"] == "call_started"
            assert events[-1]["phase"] == packet["stage"]
            return reply(packet)

    def observe(stage, event, **details):
        assert stage == "entity_first"
        events.append({"event": event, **details})

    return run_entity_first(source, {"load": "Measured load"}, "named equipment", Backend(),
                            on_progress=observe, **options)


def response(answer, tokens=7):
    return BackendResponse(text=json.dumps(answer), usage={"total_tokens": tokens, "untrusted metric": 2})


def test_completed_blocks_report_verified_local_counts_and_empty_answers():
    source = "Machine Alpha load 80.\n\n# Appendix\nNo further measurements."
    events = []

    def reply(p):
        w = p["windows"][0]
        if p["stage"] == "inventory":
            return response({"objects": [card(w, name="Machine Alpha", proof="Machine Alpha load 80.")]})
        facts = []
        if "load 80" in w["text"]:
            oid = p["objects"][0]["object_id"]
            facts = [fact(w, oid, name="Machine Alpha", line="Machine Alpha load 80.", value=v)
                     for v in ("80", "8")]
        return response({"nominations": [], "facts": facts})

    report = run(source, reply, events, max_calls=6)
    starts = [e for e in events if e["event"] == "phase_started"]
    assert [(e["phase"], e["total"], e["readable_blocks"]) for e in starts] == [
        ("inventory", 1, 2), ("attributes", 2, 2)]
    done = [e for e in events if e["event"] == "block_finished"]
    assert [(e["phase"], e["index"], e["total"], e["status"], e["block_count"]) for e in done] == [
        ("inventory", 1, 1, "read", 2), ("attributes", 1, 2, "read", 1), ("attributes", 2, 2, "read", 1)]
    assert [(e["proposals"], e["eligible"], e["pending"]) for e in done[1:]] == [(2, 1, 1), (0, 0, 0)]
    assert [e["roster_count"] for e in done] == [1, 1, 1]
    assert [e["usage"] for e in done] == [{"total_tokens": n} for n in (7, 14, 21)]
    assert all(e["elapsed_seconds"] >= 0 for e in done)
    assert [e["used"] for e in done] == [1, 2, report["budget"]["calls_used"]] == [1, 2, 3]
    assert all(e["limit"] == 6 for e in events)
    assert "Machine Alpha" not in json.dumps(events) and "untrusted metric" not in json.dumps(events)


def test_each_actual_format_attempt_reserved_and_only_completed_work_counted():
    events = []

    def reply(p):
        if p["stage"] == "attributes":
            if events[-1]["format_attempt"] == 1:
                return BackendResponse(text="broken", usage={"total_tokens": 13})
            return response({"nominations": [], "facts": []})
        return response({"objects": []})

    run("A load 80.", reply, events, max_calls=4, format_retries=1)
    assert [(e["format_attempt"], e["used"]) for e in events if e["event"] == "call_started"] == [
        (1, 1), (1, 2), (2, 3)]
    assert [e["event"] for e in events if e["phase"] == "attributes"] == [
        "phase_started", "call_started", "call_returned", "call_started", "call_returned", "block_finished"]
    block, = [e for e in events if e["event"] == "block_finished" and e["phase"] == "attributes"]
    assert (block["index"], block["total"], block["usage"]["total_tokens"]) == (1, 1, 27)


def test_budget_limited_work_has_no_invented_dispatch_or_success():
    events = []
    report = run("A load 80.", lambda _: pytest.fail("budget exhausted"), events, max_calls=0)
    assert report["status"] == "partial"
    assert all(e["event"] != "call_started" for e in events)
    assert [(e["phase"], e["status"], e["used"]) for e in events if e["event"] == "block_finished"] == [
        ("inventory", "budget_exhausted", 0), ("attributes", "budget_exhausted", 0)]


def test_truncated_block_and_later_recovery_are_explicit():
    events = []

    def reply(p):
        if p["stage"] == "inventory":
            return response({"objects": []})
        if p["windows"][0]["kind"] == "table" and not events[-1]["recovery"]:
            return BackendResponse(text="cut", finish_reason="length", usage={"total_tokens": 51})
        return response({"nominations": [], "facts": []})

    report = run(DOCUMENT, reply, events, max_calls=10)
    results = [e for e in events if e["event"] == "block_finished" and e["phase"] == "attributes"]
    assert [(e["status"], e["recovery"]) for e in results] == [
        ("truncated_response", False), ("read", False), ("read", True), ("read", True)]
    assert results[0]["usage"]["total_tokens"] == 58
    assert results[-1]["index"] == results[-1]["total"] == 4
    assert results[-1]["used"] == report["budget"]["calls_used"] == 5


def test_interrupt_leaves_last_dispatch_open():
    events = []

    def interrupt(_):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run("A load 80.", interrupt, events)
    assert [e["event"] for e in events] == ["phase_started", "call_started"]
    assert events[-1]["used"] == 1
