"""Synthetic fault injection for bounded subdivision and checkpoint recovery."""

import hashlib
import io
import json
import re
from pathlib import Path

import httpx
import pytest

from attestextract import ExecutionOptions, cli, extract
from attestextract.backends import OpenAICompatibleBackend
from attestextract.chunking import chunk_document
from attestextract.diagnostics import RunTrace
from attestextract.errors import BackendError, ConfigurationError, InvalidResponseError
from attestextract.execution import check_resume_paths, digest
from attestextract.models import BackendResponse, GenerationOptions, TaskSpec
from attestextract.planning import plan_chunks, split_chunk
from attestextract.progress import ConsoleProgress


TEXT = "Item A reads 11.\n\nItem B reads 22.\n\nItem C reads 33."
TASK = {"name": "Synthetic readings", "record_types": {"reading": {
    "fields": {"subject": {"type": "string", "required": True},
               "amount": {"type": "integer", "required": True}},
    "identity_fields": ["subject"],
}}}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def output(source):
    records = []
    for match in re.finditer(r"Item ([A-Z]) reads (\d+)\.", source):
        records.append({"record_type": "reading", "fields": {
            "subject": {"value": "Item " + match[1], "quote": match[0]},
            "amount": {"value": int(match[2]), "quote": match[0]},
        }})
    return json.dumps({"records": records})


def reply(content, *, finish="stop", **message):
    return httpx.Response(200, json={"model": "synthetic", "usage": {"total_tokens": 10},
        "choices": [{"finish_reason": finish, "message": {"content": content, **message}}]})


def run(directory, *, budget=10, resume=None, handler=None, text=TEXT,
        max_chars=18, depth=3, retries=0, max_attempts=1, **kwargs):
    calls = []
    trace = RunTrace(directory, secrets=["synthetic-credential"])

    def endpoint(request):
        source = json.loads(json.loads(request.content)["messages"][-1]["content"])["source"]
        calls.append(source)
        return handler(source, len(calls)) if handler else reply(output(source))

    backend = OpenAICompatibleBackend("https://synthetic.invalid/v1", "synthetic",
        api_key="synthetic-credential", trace=trace, max_attempts=max_attempts,
        transport=httpx.MockTransport(endpoint), sleep_fn=lambda _: None)
    # Expose calls even when extraction raises.
    run.calls = calls
    result = extract(text, TASK, backend=backend, trace=trace, max_chunk_chars=max_chars,
        execution=ExecutionOptions(adaptive_chunking=False, min_chunk_chars=1,
                                   max_split_depth=depth, max_model_calls=budget),
        resume_from=resume, format_retries=retries, **kwargs)
    trace.finish(result.model_dump(mode="json"))
    return result


def hashes(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def checkpoint(directory):
    return read(directory / "execution/checkpoint.json")["state"]


def test_budget_resume_reuses_completed_and_preserves_old_run(tmp_path):
    first, second, third = [tmp_path / name / "diagnostics" for name in ("first", "second", "third")]
    with pytest.raises(BackendError, match="budget exhausted") as error:
        run(first, budget=1)
    assert error.value.code == "call_budget_exhausted" and len(run.calls) == 1
    saved = checkpoint(first)
    assert saved["calls_used"] == 1
    assert [node["status"] for node in saved["nodes"].values()] == ["completed", "failed", "pending"]
    assert not (first / "result.json").exists()
    original = hashes(first)

    result = run(second, budget=3, resume=first.parent)
    assert len(run.calls) == 2 and all("Item A" not in value for value in run.calls)
    assert [item.fields["amount"].value for item in result.candidates] == [11, 22, 33]
    assert result.run.request_attempts == result.run.response_count == 3
    assert result.run.usage == {"total_tokens": 30}
    assert read(second / "execution/summary.json")["reused_chunks"] == 1
    assert hashes(first) == original
    replayed = run(third, budget=None, resume=second)
    assert run.calls == []
    assert [c.model_dump() for c in replayed.candidates] == [c.model_dump() for c in result.candidates]
    assert checkpoint(third)["calls_used"] == 3
    assert read(third / "execution/summary.json")["reused_chunks"] == 3
    for item in replayed.candidates:
        evidence = item.fields["amount"].evidence
        assert TEXT[evidence.start:evidence.end] == f"{item.fields['subject'].value} reads {item.fields['amount'].value}."


def test_truncated_parent_is_discarded_then_children_are_grounded(tmp_path):
    directory = tmp_path / "trace"
    bad = '{"records":[{"record_type":"reading","fields":{"amount":{"value":999}}},'
    result = run(directory, budget=4, max_chars=1000,
        handler=lambda source, number: reply(bad, finish="length") if number == 1 else reply(output(source)))
    assert len(run.calls) == 4
    assert [item.fields["amount"].value for item in result.candidates] == [11, 22, 33]
    assert result.run.chunk_count == 3
    assert result.run.response_count == result.run.request_attempts == 4
    assert result.run.usage == {"total_tokens": 40}
    assert (directory / "responses/chunk-0001.txt").read_text(encoding="utf-8") == bad
    assert read(directory / "responses/chunk-0001.meta.json")["finish_reason"] == "length"
    assert read(directory / "chunks/chunk-0001.json")["text"] == TEXT
    state = checkpoint(directory)
    assert state["nodes"]["chunk-0001"]["status"] == "split"
    assert state["nodes"]["chunk-0001"]["error_code"] == "truncated_response"
    assert not (directory / "execution/cache/chunk-0001.json").exists()


def test_budget_can_stop_midway_through_children_then_resume(tmp_path):
    with pytest.raises(BackendError, match="budget exhausted"):
        run(tmp_path / "first", budget=2, max_chars=1000,
            handler=lambda source, number: reply("partial", finish="length") if number == 1 else reply(output(source)))
    result = run(tmp_path / "second", budget=4, max_chars=1000, resume=tmp_path / "first")
    assert len(run.calls) == 2
    assert len(result.candidates) == 3
    assert result.run.response_count == 4
    assert checkpoint(tmp_path / "second")["calls_used"] == 4


@pytest.mark.parametrize("text,depth", [(TEXT, 0), ("| Item | Amount |\n| --- | --- |\n| A | 11 |\n", 3)])
def test_depth_limit_and_atomic_table_fail_explicitly(tmp_path, text, depth):
    with pytest.raises(InvalidResponseError) as error:
        run(tmp_path / "trace", text=text, depth=depth, max_chars=1000,
            handler=lambda *_: reply("partial", finish="length"))
    assert error.value.code == "truncated_response"
    assert len(run.calls) == 1
    assert read(tmp_path / "trace/error.json")["stage"] == "model_response"


def test_format_regeneration_shares_budget(tmp_path):
    with pytest.raises(BackendError, match="budget exhausted"):
        run(tmp_path / "trace", budget=1, retries=1, max_chars=1000,
            handler=lambda *_: reply("invalid synthetic JSON"))
    assert len(run.calls) == 1
    assert checkpoint(tmp_path / "trace")["calls_used"] == 1
    assert (tmp_path / "trace/validation/chunk-0001.errors.json").is_file()
    result = run(tmp_path / "resumed", budget=2, retries=1, max_chars=1000, resume=tmp_path / "trace")
    assert len(run.calls) == 1 and len(result.candidates) == 3
    assert result.run.response_count == 2


def test_inflight_interrupt_keeps_reservation(tmp_path):
    def interrupted(*_):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path / "first", budget=2, max_chars=1000, handler=interrupted)
    state = checkpoint(tmp_path / "first")
    assert state["calls_used"] == 1 and state["nodes"]["chunk-0001"]["status"] == "in_flight"
    result = run(tmp_path / "second", budget=None, max_chars=1000, resume=tmp_path / "first")
    assert len(run.calls) == 1 and len(result.candidates) == 3
    assert checkpoint(tmp_path / "second")["calls_used"] == 2
    assert result.run.request_attempts == 1  # Only the newly observed completion, not unknown billing.


@pytest.mark.parametrize("kind", ["empty", "refusal", "tool_calls", "message", "network"])
def test_failed_calls_are_accounted_for_after_resume(tmp_path, kind):
    def failure(source, _):
        if "Item B" not in source:
            return reply(output(source))
        if kind == "network":
            raise httpx.ConnectError("synthetic failure")
        if kind == "message":
            return httpx.Response(200, json={"usage": {"total_tokens": 10},
                                            "choices": [{"finish_reason": "stop", "message": None}]})
        return reply("" if kind == "empty" else output(source),
                     **({kind: "synthetic refusal" if kind == "refusal" else [{}]} if kind in {"refusal", "tool_calls"} else {}))
    with pytest.raises(BackendError):
        run(tmp_path / "first", budget=4, handler=failure)
    assert len(run.calls) == 2
    result = run(tmp_path / "second", budget=None, resume=tmp_path / "first")
    assert len(run.calls) == 2 and len(result.candidates) == 3
    assert result.run.request_attempts == 4
    assert result.run.response_count == (3 if kind == "network" else 4)
    assert result.run.usage == {"total_tokens": 30 if kind == "network" else 40}


def test_http_retry_count_is_distinct_from_generation_budget(tmp_path):
    result = run(tmp_path / "trace", budget=1, max_chars=1000, max_attempts=2,
        handler=lambda source, count: httpx.Response(503) if count == 1 else reply(output(source)))
    assert len(run.calls) == 2 and result.run.request_attempts == 2
    assert checkpoint(tmp_path / "trace")["calls_used"] == 1


@pytest.mark.parametrize("change", [{"text": TEXT + " extra"}, {"max_chars": 19}, {"depth": 2},
    {"generation": GenerationOptions(max_output_tokens=1000)}, {"backend_identity": "changed"}])
def test_resume_identity_mismatch_is_rejected_without_new_calls(tmp_path, change):
    run(tmp_path / "first")
    with pytest.raises(ConfigurationError, match="identity mismatch"):
        run(tmp_path / "second", resume=tmp_path / "first", **change)
    assert run.calls == []


@pytest.mark.parametrize("damage", ["state", "cache", "missing", "tree", "cache_schema"])
def test_corrupt_checkpoint_is_rejected_before_any_new_calls(tmp_path, damage):
    run(tmp_path / "first")
    path = tmp_path / "first/execution/checkpoint.json"
    envelope = read(path)
    cache = tmp_path / "first/execution/cache/chunk-0003.json"
    if damage == "state":
        envelope["state"]["calls_used"] += 1
    elif damage == "cache":
        cache.write_text("{}", encoding="utf-8")
    elif damage == "missing":
        cache.unlink()
    elif damage == "tree":
        envelope["state"]["nodes"]["chunk-0003"]["segments"][0]["start"] = 0
        envelope["sha256"] = digest(envelope["state"])
    else:
        payload = read(cache)
        payload["text"] = '{"records":[{"record_type":"undeclared","fields":{}}]}'
        cache.write_text(json.dumps(payload), encoding="utf-8")
        envelope["state"]["nodes"]["chunk-0003"]["cache_hash"] = digest(payload)
        # Earlier leaf pending, later cache invalid: all caches must be checked first.
        node = envelope["state"]["nodes"]["chunk-0001"]
        node["status"], node["cache_hash"] = "failed", None
        envelope["sha256"] = digest(envelope["state"])
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises((ConfigurationError, InvalidResponseError)):
        run(tmp_path / "second", resume=tmp_path / "first")
    assert run.calls == []


def test_resume_protects_previous_runs_and_history(tmp_path):
    first = tmp_path / "first/diagnostics"
    second = tmp_path / "second/diagnostics"
    run(first)
    run(second, resume=first.parent)
    original = hashes(tmp_path / "first"), hashes(tmp_path / "second")
    for destination in (first / "execution/checkpoint.json", first.parent / "result.json",
                        second / "nested", first.parent, tmp_path):
        with pytest.raises(ConfigurationError, match="outside previous run"):
            check_resume_paths(second.parent, [destination])
    assert original == (hashes(tmp_path / "first"), hashes(tmp_path / "second"))


def test_resume_resolves_directory_links_before_protecting_files(tmp_path):
    actual = tmp_path / "actual"
    run(actual)
    linked = tmp_path / "linked"
    linked.mkdir()
    try:
        (linked / "diagnostics").symlink_to(actual, target_is_directory=True)
    except OSError:
        # Windows directory junctions do not need Developer Mode/admin rights.
        import os
        import subprocess
        if os.name != "nt":
            raise
        quoted_link = str(linked / "diagnostics").replace("'", "''")
        quoted_target = str(actual).replace("'", "''")
        result = subprocess.run(["powershell", "-NoProfile", "-Command",
            f"$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path '{quoted_link}' -Target '{quoted_target}' | Out-Null"], capture_output=True)
        if result.returncode:
            pytest.skip("Directory links unavailable on this host")
    with pytest.raises(ConfigurationError, match="outside previous run"):
        check_resume_paths(linked, [actual / "execution/checkpoint.json"])
    with pytest.raises(ConfigurationError, match="outside previous run"):
        check_resume_paths(linked, [linked / "result.json"])
    run(tmp_path / "next", resume=linked)
    with pytest.raises(ConfigurationError, match="outside previous run"):
        check_resume_paths(tmp_path / "next", [linked / "result.json"])


def test_custom_backend_with_model_attributes_still_requires_explicit_identity(tmp_path):
    class Custom:
        name, model, base_url = "custom", "synthetic", "https://synthetic.invalid"
        def generate(self, request):
            pytest.fail("Custom backend must be rejected before generate")
    with pytest.raises(ConfigurationError, match="backend_identity"):
        extract(TEXT, TASK, backend=Custom(), trace=RunTrace(tmp_path / "trace"), execution=ExecutionOptions())


def test_planning_responds_to_output_complexity_and_is_opt_out():
    text = "\n\n".join(f"Observation {i} records amount {i + 10}." for i in range(50))
    simple = TaskSpec.model_validate(TASK)
    complex_task = simple.model_copy(deep=True)
    fields = complex_task.record_types["reading"].fields
    for i in range(10):
        fields[f"extra_{i}"] = fields["subject"].model_copy()
    settings = ExecutionOptions(min_chunk_chars=60)
    generation = GenerationOptions(max_output_tokens=2048)
    small = plan_chunks(text, simple, generation, settings)
    complex_chunks = plan_chunks(text, complex_task, generation, settings)
    assert len(complex_chunks) > len(small) > 1
    assert max(map(lambda c: len(c.text), complex_chunks)) < max(map(lambda c: len(c.text), small))
    fixed = plan_chunks(text, complex_task, generation, ExecutionOptions(adaptive_chunking=False))
    assert len(fixed) == 1
    assert len(plan_chunks(text, simple, GenerationOptions(), settings)) >= 1


@pytest.mark.parametrize("text", ["前言🙂\r\n\r\n" + TEXT, "long " * 200,
    "Intro🙂\r\n\r\n| Item | Amount |\r\n| --- | --- |\r\n" + "".join(f"| Item {i} | {i} |\r\n" for i in range(12)) + "\r\nEnd."])
def test_recursive_splits_preserve_unicode_coverage_and_atomic_table_rows(text):
    queue = chunk_document(text, max_chars=10000)
    count = 0
    while queue:
        parent = queue.pop(0)
        children = split_chunk(text, parent, min_chars=8)
        if not children:
            continue
        count += 1
        parent_positions = {i for span in parent.segments for i in range(span.start, span.end)}
        child_positions = {i for child in children for span in child.segments for i in range(span.start, span.end)}
        assert child_positions == parent_positions
        for child in children:
            assert child.text == "".join(text[span.start:span.end] for span in child.segments)
            assert len(child.text) < len(parent.text)
            if "|" in child.text:
                assert "| Item | Amount |\r\n| --- | --- |\r\n" in child.text
                assert all(line.endswith("|") for line in child.text.splitlines() if line.startswith("|"))
        queue.extend(children)
    assert count > 0


def test_cli_resume_and_preflight_then_budgeted_recovery(tmp_path, monkeypatch, capsys):
    source, task, env = [tmp_path / name for name in ("source.md", "task.json", ".env")]
    source.write_bytes(TEXT.encode("utf-8"))
    task.write_text(json.dumps(TASK), encoding="utf-8")
    env.write_text("DEEPSEEK_API_KEY=synthetic-credential\n", encoding="utf-8")
    calls = []
    def endpoint(request):
        source_text = json.loads(json.loads(request.content)["messages"][-1]["content"])["source"]
        calls.append(source_text)
        return reply(output(source_text))
    monkeypatch.setattr("attestextract.backends.OpenAICompatibleBackend", lambda **kw:
                        OpenAICompatibleBackend(**kw, transport=httpx.MockTransport(endpoint)))
    args = ["extract", "--input", str(source), "--task", str(task), "--env-file", str(env),
            "--model-id", "deepseek:deepseek-flash", "--chunking", "fixed", "--max-chunk-chars", "18"]
    assert cli.main(args + ["--max-model-calls", "1"]) == 1
    first = next((tmp_path / "outputs").iterdir())
    old = hashes(first)
    assert len(calls) == 1
    assert cli.main(args + ["--resume", str(first), "--max-model-calls", "3", "--check"]) == 0
    assert len(calls) == 1
    assert cli.main(args + ["--resume", str(first), "--max-model-calls", "3"]) == 0
    assert len(calls) == 3 and hashes(first) == old
    resumed = next(p for p in (tmp_path / "outputs").iterdir() if p != first and (p / "result.json").exists())
    assert read(resumed / "diagnostics/execution/summary.json")["reused_chunks"] == 1
    assert read(resumed / "result.json")["run"]["request_attempts"] == 3
    for flag, path in (("--output", first / "diagnostics/execution/checkpoint.json"),
                       ("--report", first / "report.md"), ("--log-dir", first / "nested")):
        assert cli.main(args + ["--resume", str(first), flag, str(path)]) == 1
        assert hashes(first) == old and len(calls) == 3
    stderr = capsys.readouterr().err
    assert "reused completed response" in stderr and "budget" in stderr and "Resume checkpoint:" in stderr


def test_old_run_without_checkpoint_has_actionable_error(tmp_path):
    with pytest.raises(ConfigurationError, match="older runs"):
        check_resume_paths(tmp_path, [tmp_path / "new"])


@pytest.mark.parametrize("status", ["length", "content_filter"])
def test_custom_normalized_nonstop_response_is_not_parsed_as_success(tmp_path, status):
    class Custom:
        name = "custom"
        def generate(self, request):
            return BackendResponse(text=output(TEXT), finish_reason=status, usage={"total_tokens": 7})
    with pytest.raises(InvalidResponseError):
        extract(TEXT, TASK, backend=Custom(), trace=RunTrace(tmp_path / "trace"), backend_identity="synthetic-v1",
                execution=ExecutionOptions(max_split_depth=0), format_retries=1)
    state = checkpoint(tmp_path / "trace")
    assert state["calls_used"] == state["response_count"] == 1
    assert state["usage"] == {"total_tokens": 7}
    assert state["nodes"]["chunk-0001"]["status"] == "failed"
    assert not (tmp_path / "trace/result.json").exists()


@pytest.mark.parametrize("field,value", [("response_count", 4), ("request_attempts", 0)])
def test_inconsistent_ledger_rejected_even_with_valid_checksum(tmp_path, field, value):
    run(tmp_path / "first")
    path = tmp_path / "first/execution/checkpoint.json"
    envelope = read(path)
    envelope["state"][field] = value
    envelope["sha256"] = digest(envelope["state"])
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        run(tmp_path / "second", resume=tmp_path / "first")
    assert run.calls == []


def test_format_feedback_and_attempt_survive_budget_stop(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    with pytest.raises(BackendError, match="budget exhausted"):
        run(first, budget=1, retries=1, max_chars=1000,
            handler=lambda *_: reply('{"records":[{"record_type":"reading"} "fields":{}]}'))
    node = checkpoint(first)["nodes"]["chunk-0001"]
    assert node["format_failures"] == 1 and node["format_retries_reserved"] == 0
    assert node["format_feedback"][0]["code"] == "invalid_json"
    run(second, budget=2, retries=1, max_chars=1000, resume=first)
    assert len(run.calls) == 1
    body = read(second / "requests/chunk-0001.format-2.request.json")
    assert json.loads(body["messages"][-1]["content"])["source"] == TEXT
    assert '"code": "invalid_json"' in body["messages"][0]["content"]
    assert read(second / "responses/chunk-0001.format-2.meta.json")["format_attempt"] == 2
    summary = read(second / "execution/summary.json")
    assert summary["format_recovered_chunks"] == summary["format_retries_reserved"] == 1
    assert summary["first_pass_chunks"] == summary["failed_chunks"] == summary["pending_chunks"] == 0


def test_exhausted_format_allowance_cannot_be_reset_by_resume_or_policy_toggle(tmp_path):
    first = tmp_path / "first"
    with pytest.raises(InvalidResponseError):
        run(first, retries=1, max_chars=1000, handler=lambda *_: reply("bad synthetic JSON"))
    assert len(run.calls) == 2
    for index, retries in enumerate((1, 0, 1)):
        next_run = tmp_path / f"resume-{index}"
        with pytest.raises(InvalidResponseError) as error:
            run(next_run, budget=100, retries=retries, max_chars=1000, resume=first)
        assert error.value.code == "format_retry_exhausted" and error.value.attempts == 0
        assert run.calls == []
        assert checkpoint(next_run)["calls_used"] == 2
        assert checkpoint(next_run)["nodes"]["chunk-0001"]["format_retries_reserved"] == 1
        assert not (next_run / "result.json").exists()
        first = next_run


@pytest.mark.parametrize("failure", ["interrupt", "transport"])
def test_interrupted_or_failed_regeneration_keeps_its_reservation(tmp_path, failure):
    def handler(source, count):
        if count == 1:
            return reply("bad synthetic JSON")
        if failure == "interrupt":
            raise KeyboardInterrupt()
        raise httpx.ConnectError("synthetic connection failure")
    with pytest.raises(KeyboardInterrupt if failure == "interrupt" else BackendError):
        run(tmp_path / "first", retries=1, max_chars=1000, handler=handler)
    assert checkpoint(tmp_path / "first")["calls_used"] == 2
    with pytest.raises(InvalidResponseError) as error:
        run(tmp_path / "second", retries=1, max_chars=1000, resume=tmp_path / "first", budget=100)
    assert error.value.code == "format_retry_exhausted" and run.calls == []


def test_enabling_unused_format_retry_is_audited_and_reuses_good_cache(tmp_path):
    def handler(source, count):
        return reply(output(source)) if count == 1 else reply("bad synthetic JSON")
    with pytest.raises(InvalidResponseError):
        run(tmp_path / "first", retries=0, handler=handler)
    result = run(tmp_path / "second", retries=1, resume=tmp_path / "first")
    assert len(run.calls) == 2 and all("Item A" not in source for source in run.calls)
    assert len(result.candidates) == 3
    assert checkpoint(tmp_path / "second")["policy_changes"] == [
        {"format_retries": {"from": 0, "to": 1}}]
    assert (tmp_path / "second/responses/chunk-0002.format-2.txt").is_file()


def test_disable_pending_format_retry_prevents_new_call(tmp_path):
    with pytest.raises(BackendError, match="budget exhausted"):
        run(tmp_path / "first", budget=1, retries=1, max_chars=1000,
            handler=lambda *_: reply("bad synthetic JSON"))
    with pytest.raises(InvalidResponseError) as error:
        run(tmp_path / "second", budget=5, retries=0, max_chars=1000, resume=tmp_path / "first")
    assert error.value.code == "format_retry_exhausted" and run.calls == []
    assert checkpoint(tmp_path / "second")["nodes"]["chunk-0001"]["format_retries_reserved"] == 0


def test_truncated_format_regeneration_can_still_subdivide(tmp_path):
    def handler(source, count):
        if count == 1:
            return reply("bad synthetic JSON")
        if count == 2:
            return reply('{"records":[]}', finish="length")
        return reply(output(source))
    result = run(tmp_path / "trace", budget=5, retries=1, max_chars=1000, handler=handler)
    assert [candidate.fields["amount"].value for candidate in result.candidates] == [11, 22, 33]
    node = checkpoint(tmp_path / "trace")["nodes"]["chunk-0001"]
    assert node["status"] == "split" and node["format_retries_reserved"] == 1


def test_checkpoint_v1_is_rejected_with_new_run_guidance(tmp_path):
    run(tmp_path / "first")
    path = tmp_path / "first/execution/checkpoint.json"
    envelope = read(path)
    envelope["state"]["checkpoint_version"] = "1"
    envelope["sha256"] = digest(envelope["state"])
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Start a new run"):
        run(tmp_path / "second", resume=tmp_path / "first")
    assert run.calls == []


def test_progress_saved_truncation_split_does_not_subtract_reused_chunk():
    stream = io.StringIO()
    progress = ConsoleProgress(stream=stream)
    try:
        progress({"stage": "chunking", "event": "completed", "chunk_count": 2})
        progress({"stage": "execution", "event": "reused", "chunk_id": "chunk-0001"})
        progress({"stage": "execution", "event": "split", "chunk_id": "chunk-0002",
                  "children": ["chunk-0002.1", "chunk-0002.2"], "leaf_chunks": 3})
        for chunk in ("chunk-0002.1", "chunk-0002.2"):
            progress({"stage": "model_request", "event": "started", "chunk_id": chunk, "format_attempt": 1})
    finally:
        progress.close()
    assert "[2/3] requesting model" in stream.getvalue()
    assert "[3/3] requesting model" in stream.getvalue()


@pytest.mark.parametrize("damage", ["missing_feedback", "impossible_retry"])
def test_inconsistent_format_ledger_is_rejected_before_calls(tmp_path, damage):
    with pytest.raises(InvalidResponseError):
        run(tmp_path / "first", retries=0, max_chars=1000, handler=lambda *_: reply("invalid JSON"))
    path = tmp_path / "first/execution/checkpoint.json"
    envelope = read(path)
    node = envelope["state"]["nodes"]["chunk-0001"]
    if damage == "missing_feedback":
        node["format_feedback"] = []
    else:
        node["format_retries_reserved"] = 1  # Only one call exists; cannot have regenerated.
    envelope["sha256"] = digest(envelope["state"])
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        run(tmp_path / "second", retries=1, max_chars=1000, resume=tmp_path / "first")
    assert run.calls == []


def test_resume_rotates_credentials_but_never_stores_key_in_state(tmp_path):
    run(tmp_path / "first", max_chars=1000)
    backend = OpenAICompatibleBackend("https://synthetic.invalid/v1", "synthetic",
        api_key="rotated-synthetic-key", max_attempts=1,
        transport=httpx.MockTransport(lambda _: pytest.fail("Completed cache must not call HTTP")))
    result = extract(TEXT, TASK, backend=backend, trace=RunTrace(tmp_path / "second"), max_chunk_chars=1000,
        execution=ExecutionOptions(adaptive_chunking=False, min_chunk_chars=1), resume_from=tmp_path / "first")
    assert len(result.candidates) == 3
    saved = (tmp_path / "first/execution/checkpoint.json").read_text(encoding="utf-8")
    assert "synthetic-credential" not in saved and "synthetic.invalid" not in saved


def test_redacted_cache_cannot_silently_change_evidence_on_resume(tmp_path):
    class Custom:
        name = "custom"
        def generate(self, request):
            return BackendResponse(text=output(TEXT))
    options = ExecutionOptions()
    extract(TEXT, TASK, backend=Custom(), trace=RunTrace(tmp_path / "first", secrets=["Item A"]),
            execution=options, backend_identity="synthetic-v1")
    with pytest.raises(ConfigurationError, match="redacted"):
        extract(TEXT, TASK, backend=Custom(), trace=RunTrace(tmp_path / "second"), execution=options,
                backend_identity="synthetic-v1", resume_from=tmp_path / "first")


def test_cli_interrupt_before_checkpoint_does_not_offer_nonexistent_resume(tmp_path, monkeypatch, capsys):
    source, task = tmp_path / "source.md", tmp_path / "task.json"
    source.write_bytes(TEXT.encode())
    task.write_text(json.dumps(TASK), encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    def interrupted(*_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(cli, "load_task", interrupted)
    assert cli.main(["extract", "--input", str(source), "--task", str(task),
                     "--model-id", "deepseek:deepseek-flash"]) == 130
    stderr = capsys.readouterr().err
    assert "No execution checkpoint" in stderr and "Resume checkpoint:" not in stderr
