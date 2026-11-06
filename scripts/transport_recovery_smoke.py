"""Installed-package acceptance: read timeout, offline check, transport-only resume.

Synthetic text, synthetic credentials and httpx.MockTransport only. Copy this
script outside the checkout to test an installed wheel. TLS negotiation itself
is covered by transport tests; an injected transport deliberately skips probes.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import httpx

import attestextract
from attestextract import Document, create_archive, backends, cli
from attestextract.models import ExtractionResult


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _hashes(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def _invoke(root, arguments, expected_exit):
    outputs = root / "outputs"
    before = set(outputs.iterdir()) if outputs.exists() else set()
    assert cli.main(arguments) == expected_exit
    created = set(outputs.iterdir()) - before
    assert len(created) == 1
    return created.pop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    root = parser.parse_args(argv).output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    for key in list(os.environ):
        if key.startswith(("ATTESTEXTRACT_", "DEEPSEEK_", "QWEN_", "DASHSCOPE_", "VLM_")):
            del os.environ[key]
    os.environ["QWEN_API_KEY"] = "synthetic-key"
    source, task_path = root / "source.md", root / "task.json"
    paragraphs = [f"Item {chr(65 + index)} reads {101 + index}." for index in range(16)]
    text = "\n\n".join(paragraphs)
    source.write_bytes(text.encode("utf-8"))
    task_path.write_text(json.dumps({"name": "Synthetic transport recovery", "record_types": {"reading": {
        "fields": {"subject": {"type": "string", "required": True},
                   "amount": {"type": "integer", "required": True}},
        "identity_fields": ["subject"],
    }}}), encoding="utf-8")
    observed, timeouts = [], []

    def endpoint(request):
        assert request.method == "POST", "Injected transport must not receive TLS probes."
        body = json.loads(request.content)
        chunk = json.loads(body["messages"][-1]["content"])["source"].strip()
        observed.append(chunk)
        timeouts.append(request.extensions["timeout"])
        match = re.fullmatch(r"Item ([A-P]) reads (\d+)\.", chunk)
        assert match is not None, "One complete synthetic paragraph is expected per chunk."
        if len(observed) == 12:
            raise httpx.ReadTimeout("synthetic delayed response", request=request)
        record = {"record_type": "reading",
                  "values": {"subject": f"Item {match[1]}", "amount": int(match[2])},
                  "quotes": {"subject": chunk, "amount": chunk}}
        return httpx.Response(200, json={"model": "qwen-plus", "usage": {"total_tokens": 10},
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"records": [record]})}}]})

    original = backends.OpenAICompatibleBackend
    backends.OpenAICompatibleBackend = lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(endpoint))
    args = ["extract", "--input", str(source), "--task", str(task_path), "--model-id", "qwen:qwen-plus",
            "--chunking", "fixed", "--max-chunk-chars", "20", "--timeout", "60",
            "--max-attempts", "1", "--format-retries", "0", "--progress", "quiet"]
    try:
        first = _invoke(root, args + ["--tls-mode", "1.2", "--max-model-calls", "48"], 1)
        assert observed == paragraphs[:12]
        assert not (first / "result.json").exists()
        assert _read(first / "diagnostics/error.json")["code"] == "timeout"
        saved = _read(first / "diagnostics/execution/summary.json")
        assert saved["leaf_chunks"] == 16 and saved["completed_chunks"] == 11
        assert saved["failed_chunks"] == 1 and saved["pending_chunks"] == 4
        assert saved["calls_reserved"] == saved["request_attempts"] == 12
        assert saved["max_model_calls"] == 48
        old = _hashes(first)
        resumed_args = args + ["--resume", str(first), "--read-timeout", "240",
                               "--tls-mode", "auto_fallback", "--max-model-calls", "17"]

        checked = _invoke(root, resumed_args + ["--check"], 0)
        plan = _read(checked / "diagnostics/check.json")
        assert len(observed) == 12 and plan["network_calls"] == 0
        assert plan["chunk_count"] == 16
        assert plan["resume"] == {"requested": True, "reusable_chunks": 11,
                                  "remaining_chunks": 5, "remaining_call_budget": 5}
        assert plan["execution"]["calls_reserved"] == 12 and plan["execution"]["max_model_calls"] == 17
        assert plan["effective_timeouts"] == {"connect": 60, "read": 240, "write": 60, "pool": 60}
        assert _hashes(first) == old and not (checked / "result.json").exists()

        second = _invoke(root, resumed_args, 0)
        assert len(observed) == 17 and observed[12:] == paragraphs[11:]
        assert all(value["read"] == 60 for value in timeouts[:12])
        assert all(value["read"] == 240 for value in timeouts[12:])
        assert _hashes(first) == old
        final = _read(second / "diagnostics/execution/summary.json")
        assert final["completed_chunks"] == 16 and final["reused_chunks"] == 11
        assert final["calls_reserved"] == final["request_attempts"] == final["max_model_calls"] == 17
        assert final["failed_chunks"] == final["pending_chunks"] == 0
        assert final["transport_changes"], "The changed connection policy must be recorded."
        result = ExtractionResult.model_validate(_read(second / "result.json"))
        assert [item.fields["amount"].value for item in result.candidates] == list(range(101, 117))
        assert result.run.response_count == 16 and result.run.request_attempts == 17
        assert result.run.usage == {"total_tokens": 160}
        document = Document.from_text(text, source_name=source.name, representation="text/markdown")
        assert create_archive(document, result).records == []
        for candidate in result.candidates:
            for field in candidate.fields.values():
                evidence = field.evidence
                assert evidence.status == "matched" and text[evidence.start:evidence.end] == evidence.quote
        assert not any(name == "langextract" or name.startswith("langextract.") for name in sys.modules)
    finally:
        backends.OpenAICompatibleBackend = original
    summary = {"passed": True, "package_version": attestextract.__version__, "module_path": attestextract.__file__,
               "real_endpoint_calls": 0, "mock_http_calls": len(observed), "tls_probe_requests": 0,
               "initial_chunks": 16, "completed_before_timeout": 11, "timeout_requests": 1,
               "offline_check_new_calls": 0, "resume_new_calls": 5, "reused_chunks": 11,
               "completed_chunks": 16, "candidate_count": 16, "created_final_records": 0,
               "total_call_budget": 17, "previous_run_unchanged": True, "langextract_imported": False}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
