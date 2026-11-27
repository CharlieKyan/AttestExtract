"""Installed-package acceptance for bounded format regeneration and recovery.

Only synthetic inputs, credentials and httpx.MockTransport are used. The script
can be copied outside the checkout to verify an installed wheel.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
from unittest.mock import patch

import httpx

import niceextract
from niceextract import ExecutionOptions, backends, cli, extract
from niceextract.errors import InvalidResponseError


_TASK = {"name": "Synthetic format recovery", "record_types": {"reading": {
    "fields": {"subject": {"type": "string", "required": True},
               "amount": {"type": "integer", "required": True}},
    "identity_fields": ["subject"],
}}}
# An object closes too early, leaving a field at the wrong structural level.
# This reproduces the failure shape without including any research material.
_BAD_JSON = ('{"records":[{"record_type":"reading","values":{"subject":'
             '"Item B"}},"amount":22},"quotes":{"subject":"Item B reads 22."}}]}')
# One valid prefix record, then missing quotes and missing record boundaries.
# A parser's detection position does not tell it how these records should close.
_BAD_BOUNDARY_JSON = (
    '{"records":[{"record_type":"reading","values":{"subject":"Item D","amount":44},'
    '"quotes":{"subject":"Item D reads 44.","amount":"Item D reads 44."}},'
    '{"record_type":"reading","values":{"subject":"Item D","amount":44},'
    '{"record_type":"reading","values":{"subject":"Item D","amount":44}}]}'
)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _hashes(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def _good_json(source):
    records = [{"record_type": "reading",
                "values": {"subject": "Item " + match[1], "amount": int(match[2])},
                "quotes": {"subject": match[0], "amount": match[0]}}
               for match in re.finditer(r"Item ([A-Z]) reads (\d+)\.", source)]
    return json.dumps({"records": records})


def _case(directory, text):
    directory.mkdir()
    source, task = directory / "source.md", directory / "task.json"
    source.write_bytes(text.encode("utf-8"))
    task.write_text(json.dumps(_TASK), encoding="utf-8")
    return ["extract", "--input", str(source), "--task", str(task),
            "--model-id", "deepseek:deepseek-flash", "--chunking", "fixed",
            "--max-chunk-chars", "18", "--format-retries", "1"]


def _invoke(directory, arguments, expected_exit):
    outputs = directory / "outputs"
    before = set(outputs.iterdir()) if outputs.exists() else set()
    assert cli.main(arguments) == expected_exit
    created = set(outputs.iterdir()) - before
    assert len(created) == 1, "Every invocation must create a distinct run."
    return created.pop()


def _recovery_case(root, observed):
    directory = root / "recovery"
    text = "Item A reads 11.\n\nItem B reads 22."
    arguments = _case(directory, text)
    first = _invoke(directory, arguments + ["--max-model-calls", "2"], 1)
    assert [call["source"].strip() for call in observed] == ["Item A reads 11.", "Item B reads 22."]
    assert _read(first / "diagnostics/error.json")["code"] == "call_budget_exhausted"
    assert not (first / "result.json").exists()
    saved = _read(first / "diagnostics/execution/checkpoint.json")["state"]
    assert saved["checkpoint_version"] == "3" and saved["calls_used"] == 2
    assert saved["nodes"]["chunk-0001"]["status"] == "completed"
    assert saved["nodes"]["chunk-0002"]["format_failures"] == 1
    assert saved["nodes"]["chunk-0002"]["format_retries_reserved"] == 0
    old = _hashes(first)

    second = _invoke(directory, arguments + ["--resume", str(first), "--max-model-calls", "3"], 0)
    assert len(observed) == 3 and observed[-1]["source"].strip() == "Item B reads 22."
    assert "previous output failed local format validation" in observed[-1]["system"]
    assert _hashes(first) == old
    result = _read(second / "result.json")
    assert [item["fields"]["amount"]["value"] for item in result["candidates"]] == [11, 22]
    assert result["run"]["request_attempts"] == result["run"]["response_count"] == 3
    assert result["run"]["usage"] == {"total_tokens": 30}
    assert result["review"] == []
    for candidate in result["candidates"]:
        for field in candidate["fields"].values():
            evidence = field["evidence"]
            assert evidence["status"] == "matched"
            assert text[evidence["start"]:evidence["end"]] == evidence["quote"]
    summary = _read(second / "diagnostics/execution/summary.json")
    assert summary["reused_chunks"] == 1 and summary["completed_chunks"] == 2
    assert summary["format_recovered_chunks"] == 1 and summary["format_retries_reserved"] == 1
    assert (first / "diagnostics/responses/chunk-0002.txt").read_text(encoding="utf-8") == _BAD_JSON
    assert (second / "diagnostics/responses/chunk-0002.format-2.txt").is_file()

    second_hashes = _hashes(second)
    third = _invoke(directory, arguments + ["--resume", str(second)], 0)
    assert len(observed) == 3 and _hashes(second) == second_hashes and _hashes(first) == old
    assert _read(third / "diagnostics/execution/summary.json")["reused_chunks"] == 2
    assert _read(third / "result.json")["candidates"] == result["candidates"]
    return {"mock_http_calls": 3, "resume_new_calls": 1, "completed_resume_new_calls": 0,
            "candidate_values": [11, 22], "previous_runs_unchanged": True,
            "first_pass_chunks": 1, "format_recovered_chunks": 1}


def _exhausted_case(root, observed):
    directory = root / "exhausted"
    arguments = _case(directory, "Item B reads 22.")
    first = _invoke(directory, arguments, 1)
    assert len(observed) == 2
    assert _read(first / "diagnostics/error.json")["code"] == "invalid_response"
    assert not (first / "result.json").exists()
    state = _read(first / "diagnostics/execution/checkpoint.json")["state"]
    assert state["nodes"]["chunk-0001"]["format_failures"] == 2
    assert state["nodes"]["chunk-0001"]["format_retries_reserved"] == 1
    for filename in ("chunk-0001.txt", "chunk-0001.format-2.txt"):
        assert (first / "diagnostics/responses" / filename).read_text(encoding="utf-8") == _BAD_JSON
    previous = first
    frozen = {first: _hashes(first)}
    for budget in (6, 9):
        resumed = _invoke(directory, arguments + ["--resume", str(previous), "--max-model-calls", str(budget)], 1)
        assert len(observed) == 2, "Increasing the total budget must not replenish format retries."
        assert _read(resumed / "diagnostics/error.json")["code"] == "format_retry_exhausted"
        assert not (resumed / "result.json").exists()
        summary = _read(resumed / "diagnostics/execution/summary.json")
        assert summary["calls_reserved"] == 2 and summary["split_chunks"] == 0
        assert summary["completed_chunks"] == 0 and summary["failed_chunks"] == 1
        for old, hashes in frozen.items():
            assert _hashes(old) == hashes
        frozen[resumed] = _hashes(resumed)
        previous = resumed
    return {"mock_http_calls": 2, "resume_attempts": 2, "resume_new_calls": 0,
            "format_retries_reserved": 1, "split_chunks": 0, "successful_results": 0,
            "previous_runs_unchanged": True}


def _legacy_feedback_fixture(run):
    """Construct old feedback only; never label this as a historical real run."""
    path = run / "diagnostics/execution/checkpoint.json"
    envelope = _read(path)
    state = envelope["state"]
    assert state["checkpoint_version"] == "3" and state["calls_used"] == 4
    assert [node["status"] for node in state["nodes"].values()] == [
        "completed", "completed", "completed", "failed"]
    node = state["nodes"]["chunk-0004"]
    assert node["format_failures"] == 1 and node["format_retries_reserved"] == 0
    node["format_feedback"][0]["structure"] = {
        "code": "unexpected_object_in_object", "path": "records[1]",
        "actual_token": "{", "previous_key": "values", "next_key": "record_type",
        "wrong_shape": '{"values": <value>, {"record_type": <value>}}',
        "correct_shape": '{"values": <value>, "record_type": <value>}',
        "message": "Keep named properties as siblings in the same object.",
    }
    # This is the published v3 checksum representation, calculated independently
    # rather than bypassing the checkpoint reader or changing its validation.
    canonical = json.dumps(state, sort_keys=True, ensure_ascii=False,
                           allow_nan=False, separators=(",", ":"))
    envelope["sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")


def _legacy_entry_case(root, entry):
    """Exercise public entry points, original cache reuse and bounded recovery."""
    assert entry in {"cli", "python"}
    directory = root / ("legacy-" + entry)
    text = "\n\n".join(f"Item {name} reads {value}." for name, value in zip("ABCD", (11, 22, 33, 44)))
    arguments = _case(directory, text)
    # _case covers the older retry-enabled budget pause. Here the user's initial
    # run explicitly disables regeneration, then opts in while resuming.
    arguments = arguments[:-2] + ["--progress", "quiet", "--structured-output", "json_object"]
    observed = []
    response_mode = "initial"

    def endpoint(request):
        assert request.headers["Authorization"] == "Bearer synthetic-key"
        assert request.method == "POST" and request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        source = json.loads(body["messages"][-1]["content"])["source"]
        observed.append(body)
        invalid = response_mode == "invalid" or (response_mode == "initial" and "Item D" in source)
        content = _BAD_BOUNDARY_JSON if invalid else _good_json(source)
        return httpx.Response(200, json={"model": "synthetic", "usage": {"total_tokens": 10},
            "choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    def invoke(name, *, retries, budget, resume=None, expected_error=None):
        output = directory / name
        if entry == "cli":
            argv = arguments + ["--output-dir", str(output), "--format-retries", str(retries),
                                "--max-model-calls", str(budget)]
            if resume is not None:
                argv.extend(["--resume", str(resume)])
            assert cli.main(argv) == (1 if expected_error else 0)
        else:
            try:
                extract(text, _TASK, model="deepseek:deepseek-flash", api_key="synthetic-key",
                        model_options={"structured_output": "json_object"}, output_dir=output,
                        progress="quiet", max_chunk_chars=18, format_retries=retries,
                        execution=ExecutionOptions(adaptive_chunking=False, max_model_calls=budget),
                        resume_from=resume)
            except InvalidResponseError as exc:
                assert exc.code == expected_error
            else:
                assert expected_error is None, "Invalid JSON must not become a successful partial result."
        if expected_error:
            assert _read(output / "diagnostics/error.json")["code"] == expected_error
            assert not (output / "result.json").exists()
        else:
            assert (output / "result.json").is_file()
        return output

    original = backends.OpenAICompatibleBackend
    with patch.object(backends, "OpenAICompatibleBackend", lambda **kw:
                      original(**kw, transport=httpx.MockTransport(endpoint))):
        first = invoke("initial", retries=0, budget=5, expected_error="invalid_response")
        assert len(observed) == 4
        original_messages = observed[-1]["messages"]
        assert _read(first / "diagnostics/execution/summary.json")["completed_chunks"] == 3
        assert not (first / "diagnostics/execution/cache/chunk-0004.json").exists()
        assert (first / "diagnostics/responses/chunk-0004.txt").read_text(encoding="utf-8") == _BAD_BOUNDARY_JSON
        _legacy_feedback_fixture(first)
        frozen = _hashes(first)

        response_mode = "valid"
        second = invoke("recovered", retries=1, budget=5, resume=first)
        assert len(observed) == 5
        regeneration = observed[-1]["messages"]
        assert regeneration[1:] == original_messages[1:]
        assert json.loads(regeneration[-1]["content"])["source"].strip() == "Item D reads 44."
        assert "Synthetic format recovery" in json.dumps(regeneration)
        system = regeneration[0]["content"]
        assert "previous output failed local format validation" in system
        for forbidden in ("wrong_shape", "correct_shape", "<value>", "Keep named properties as siblings"):
            assert forbidden not in system
        result = _read(second / "result.json")
        assert [c["fields"]["amount"]["value"] for c in result["candidates"]] == [11, 22, 33, 44]
        assert result["review"] == []
        for candidate in result["candidates"]:
            for field in candidate["fields"].values():
                evidence = field["evidence"]
                assert evidence["status"] == "matched"
                assert text[evidence["start"]:evidence["end"]] == evidence["quote"]
        summary = _read(second / "diagnostics/execution/summary.json")
        assert summary["completed_chunks"] == 4 and summary["reused_chunks"] == 3
        assert summary["format_recovered_chunks"] == 1 and summary["calls_reserved"] == 5
        assert _read(second / "diagnostics/execution/checkpoint.json")["state"]["policy_changes"] == [
            {"format_retries": {"from": 0, "to": 1}}]
        assert (second / "diagnostics/responses/chunk-0004.format-2.txt").is_file()
        completed_hashes = _hashes(second)
        invoke("completed-replay", retries=1, budget=5, resume=second)
        assert len(observed) == 5 and _hashes(second) == completed_hashes

        # A second independent branch demonstrates that valid prefix records
        # are never applied when the complete regenerated JSON remains invalid.
        response_mode = "invalid"
        failed = invoke("failed-regeneration", retries=1, budget=5, resume=first,
                        expected_error="invalid_response")
        assert len(observed) == 6
        assert (failed / "diagnostics/responses/chunk-0004.format-2.txt").read_text(encoding="utf-8") == _BAD_BOUNDARY_JSON
        failed_state = _read(failed / "diagnostics/execution/checkpoint.json")["state"]
        assert failed_state["calls_used"] == 5
        assert failed_state["nodes"]["chunk-0004"]["format_retries_reserved"] == 1
        assert _read(failed / "diagnostics/execution/summary.json")["completed_chunks"] == 3
        assert not (failed / "diagnostics/execution/cache/chunk-0004.json").exists()
        failed_hashes = _hashes(failed)
        invoke("exhausted-replay", retries=1, budget=9, resume=failed,
               expected_error="format_retry_exhausted")
        assert len(observed) == 6 and _hashes(failed) == failed_hashes
        assert _hashes(first) == frozen
    return {"mock_http_calls": 6, "initial_calls": 4, "initial_completed_chunks": 3,
            "resume_new_calls": 1, "reused_chunks": 3, "completed_chunks": 4,
            "completed_resume_new_calls": 0, "exhausted_resume_new_calls": 0,
            "legacy_feedback_removed_from_prompt": True, "original_messages_retained": True,
            "partial_prefix_not_applied": True, "previous_runs_unchanged": True,
            "fixture": "Synthetic v3 checkpoint with pre-dev40 structural feedback fields"}


@contextmanager
def _offline_only():
    def blocked(*args, **kwargs):
        raise AssertionError("Network and .env access are forbidden in this synthetic acceptance.")

    environment = {key: value for key, value in os.environ.items() if not key.startswith(
        ("NICEEXTRACT_", "ATTESTEXTRACT_", "DEEPSEEK_", "QWEN_", "DASHSCOPE_", "VLM_", "OPENAI_"))}
    environment["DEEPSEEK_API_KEY"] = "synthetic-key"
    with patch.dict(os.environ, environment, clear=True), \
            patch("socket.socket.connect", blocked), patch("socket.socket.connect_ex", blocked), \
            patch("socket.create_connection", blocked), patch("socket.getaddrinfo", blocked), \
            patch("niceextract.envconfig.load_env_file", blocked), patch.object(cli, "load_env_file", blocked):
        yield


def _existing_cases(root):
    observed = []
    always_invalid = False

    def endpoint(request):
        assert request.headers["Authorization"] == "Bearer synthetic-key"
        body = json.loads(request.content)
        source = json.loads(body["messages"][-1]["content"])["source"]
        observed.append({"source": source, "system": body["messages"][0]["content"]})
        invalid = always_invalid or len(observed) == 2
        content = _BAD_JSON if invalid else _good_json(source)
        return httpx.Response(200, json={"model": "synthetic", "usage": {"total_tokens": 10},
            "choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    original = backends.OpenAICompatibleBackend
    with patch.object(backends, "OpenAICompatibleBackend", lambda **kwargs:
                      original(**kwargs, transport=httpx.MockTransport(endpoint))):
        recovery = _recovery_case(root, observed)
        observed.clear()
        always_invalid = True
        exhausted = _exhausted_case(root, observed)
    return recovery, exhausted


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    root = parser.parse_args(argv).output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    with _offline_only():
        recovery, exhausted = _existing_cases(root)
        legacy_entries = {entry: _legacy_entry_case(root, entry) for entry in ("cli", "python")}
    summary = {"passed": True, "package_version": niceextract.__version__,
               "module_path": niceextract.__file__, "real_endpoint_calls": 0,
               "mock_http_calls": recovery["mock_http_calls"] + exhausted["mock_http_calls"] +
                   sum(case["mock_http_calls"] for case in legacy_entries.values()),
               "recovery": recovery, "exhausted": exhausted, "legacy_entry_cases": legacy_entries}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
