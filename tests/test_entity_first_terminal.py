import io
import threading

import pytest

from attestextract.progress import ConsoleProgress


def event(name, **data):
    return {"stage": "entity_first", "event": name, "used": 1, "limit": 9, **data}


JOB = {"phase": "attributes", "index": 2, "total": 3, "recovery": False, "kind": "table",
       "block_count": 1, "block_id": "block-003"}


@pytest.mark.parametrize("mode", ["normal", "verbose", "quiet"])
def test_two_pass_console_separates_local_checks_from_final_rows(mode):
    out = io.StringIO()
    console = ConsoleProgress(mode=mode, stream=out)
    try:
        console(event("phase_started", phase="inventory", used=0, total=1, readable_blocks=3, unavailable_blocks=1))
        console(event("block_finished", phase="inventory", index=1, total=1, recovery=False, status="read",
                      elapsed_seconds=2.5, roster_count=2, usage={"prompt_tokens": 101, "completion_tokens": 3}))
        console(event("phase_started", phase="attributes", total=3, readable_blocks=3, unavailable_blocks=1))
        console(event("call_started", **JOB, used=3, format_attempt=1, source="private source"))
        console(event("call_returned", **JOB))
        assert console._waiting_since is None
        console(event("block_finished", **JOB, used=3, status="read", proposals=2, eligible=1, pending=1,
                      elapsed_seconds=4.5, usage={"total_tokens": 1206, "private usage name": 9}))
        console(event("projected", used=4, status="partial", elapsed_seconds=10.0, main_rows=1,
                      proposals=2, observations=2, pending=1, roster_count=2, usage={"total_tokens": 1211},
                      unread_inventory=0, unread_attributes=1, local_issues=2))
        console({"stage": "complete", "event": "entity_first_incomplete"})
    finally:
        console.close()
    text = out.getvalue()
    if mode == "quiet":
        assert text == ""
        return
    assert len(text.splitlines()) == 6
    assert "3 readable blocks, 1 unavailable" in text and "Pass 2/2" in text
    assert "roster 2 | 2.5s" in text and "101/3" in text
    assert "Attribute block 2/3 | table | requesting model | calls 3/9" in text
    assert "2 proposals | local checks 1 eligible, 1 held | 4.5s" in text
    assert "reported tokens so far 1,206" in text
    assert "Entity-first partial | 10.0s total" in text
    assert "2 observations (1 detail only) | main rows 1 | calls 4/9" in text
    assert "unread blocks (inventory/attributes) 0/1 | local issues 2" in text
    assert "private" not in text


def test_budget_skip_and_recovery_keep_actual_outcomes():
    out = io.StringIO()
    console = ConsoleProgress(stream=out)
    job = {**JOB, "recovery": True, "used": 9}
    try:
        console(event("call_started", **job, format_attempt=2))
        console(event("block_finished", **job, status="truncated_response", elapsed_seconds=5.0, usage={}))
        console(event("block_finished", **JOB, used=9, status="budget_exhausted", elapsed_seconds=0.0, usage={}))
    finally:
        console.close()
    text = out.getvalue()
    assert "Attribute block 2/3 (recovery) | table | requesting model | format attempt 2 | calls 9/9" in text
    assert "truncated_response | 5.0s" in text and "budget_exhausted | 0.0s" in text
    assert "reported tokens" not in text and "eligible" not in text


def test_first_request_heartbeat_has_real_work_context_and_transport_failure_clears_wait():
    heartbeat = threading.Event()

    class Stream(io.StringIO):
        def write(self, text):
            if "waiting for model" in text:
                heartbeat.set()
            return super().write(text)

    out = Stream()
    console = ConsoleProgress(stream=out, heartbeat_seconds=0.01)
    try:
        console(event("call_started", phase="inventory", index=1, total=3, block_count=8, recovery=False,
                      format_attempt=1))
        assert heartbeat.wait(2)
        console({"stage": "http", "event": "transport_failed", "request_outcome": "unknown"})
        assert console._waiting_since is None
    finally:
        console.close()
    assert not console._thread.is_alive()
    text = out.getvalue()
    assert "Inventory batch 1/3 waiting for model" in text
    assert "server outcome unknown" in text and "resum" not in text and "[0/0]" not in text


def test_heartbeat_waits_one_interval_for_each_new_request(monkeypatch):
    out = io.StringIO()
    console = ConsoleProgress(stream=out, heartbeat_seconds=15)
    console._waiting_since = 100
    console._entity_work = "Attribute block 1/2"
    ticks = iter((100.2, 115.2))
    stops = iter((False, False, True))
    monkeypatch.setattr("attestextract.progress.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr(console._stop, "wait", lambda _: next(stops))
    console._heartbeat()
    assert out.getvalue().splitlines() == ["Attribute block 1/2 waiting for model; 15s elapsed"]
