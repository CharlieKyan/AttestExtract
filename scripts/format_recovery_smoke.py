"""Installed-package acceptance for bounded format regeneration and recovery.

Only synthetic inputs, credentials and httpx.MockTransport are used. The script
can be copied outside the checkout to verify an installed wheel.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re

import httpx

import attestextract
from attestextract import backends, cli


_TASK = {"name": "Synthetic format recovery", "record_types": {"reading": {
    "fields": {"subject": {"type": "string", "required": True},
               "amount": {"type": "integer", "required": True}},
    "identity_fields": ["subject"],
}}}
# An object closes too early, leaving a field at the wrong structural level.
# This reproduces the failure shape without including any research material.
_BAD_JSON = ('{"records":[{"record_type":"reading","values":{"subject":'
             '"Item B"}},"amount":22},"quotes":{"subject":"Item B reads 22."}}]}')


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
    assert saved["checkpoint_version"] == "2" and saved["calls_used"] == 2
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    root = parser.parse_args(argv).output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    # Only this process's environment is changed; no real .env file is read.
    for key in list(os.environ):
        if key.startswith(("ATTESTEXTRACT_", "DEEPSEEK_", "QWEN_", "DASHSCOPE_", "VLM_")):
            del os.environ[key]
    os.environ["DEEPSEEK_API_KEY"] = "synthetic-key"
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
    backends.OpenAICompatibleBackend = lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(endpoint))
    try:
        recovery = _recovery_case(root, observed)
        observed.clear()
        always_invalid = True
        exhausted = _exhausted_case(root, observed)
    finally:
        backends.OpenAICompatibleBackend = original
    summary = {"passed": True, "package_version": attestextract.__version__,
               "module_path": attestextract.__file__, "real_endpoint_calls": 0,
               "mock_http_calls": recovery["mock_http_calls"] + exhausted["mock_http_calls"],
               "recovery": recovery, "exhausted": exhausted}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
