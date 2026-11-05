"""Installed-package acceptance: truncate, exhaust budget, resume, then reuse all.

Only synthetic documents, credentials and httpx.MockTransport are used.
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    root = parser.parse_args().output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    for key in list(os.environ):
        if key.startswith(("ATTESTEXTRACT_", "DEEPSEEK_", "QWEN_", "DASHSCOPE_", "VLM_")):
            del os.environ[key]
    os.environ["DEEPSEEK_API_KEY"] = "synthetic-key"
    source, task_path = root / "source.md", root / "task.json"
    text = "\n\n".join(f"Item {letter} reads {value}. " + "Synthetic background. " * 30
                       for letter, value in (("A", 11), ("B", 22), ("C", 33)))
    source.write_bytes(text.encode("utf-8"))
    task_path.write_text(json.dumps({"name": "Synthetic recovery", "record_types": {"reading": {
        "fields": {"subject": {"type": "string"}, "amount": {"type": "integer"}},
        "identity_fields": ["subject"],
    }}}), encoding="utf-8")
    observed = []
    truncated = '{"records":[ synthetic truncated output'

    def endpoint(request):
        body = json.loads(request.content)
        chunk = json.loads(body["messages"][-1]["content"])["source"]
        observed.append(chunk)
        records = [{"record_type": "reading",
                    "values": {"subject": match[1], "amount": int(match[2])},
                    "quotes": {"subject": match[0], "amount": match[0]}}
                   for match in re.finditer(r"Item ([A-Z]) reads (\d+)\.", chunk)]
        content = truncated if len(observed) == 1 else json.dumps({"records": records})
        return httpx.Response(200, json={"model": "synthetic", "usage": {"total_tokens": 10},
            "choices": [{"finish_reason": "length" if len(observed) == 1 else "stop",
                         "message": {"content": content}}]})

    original = backends.OpenAICompatibleBackend
    backends.OpenAICompatibleBackend = lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(endpoint))
    args = ["extract", "--input", str(source), "--task", str(task_path), "--model-id", "deepseek:deepseek-flash"]
    try:
        assert cli.main(args + ["--max-model-calls", "2"]) == 1
        first = next((root / "outputs").iterdir())
        assert len(observed) == 2 and not (first / "result.json").exists()
        old = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in first.rglob("*") if p.is_file()}
        assert cli.main(args + ["--resume", str(first), "--max-model-calls", "4"]) == 0
        assert len(observed) == 4
        assert old == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in first.rglob("*") if p.is_file()}
        second = next(p for p in (root / "outputs").iterdir() if p != first)
        result = json.loads((second / "result.json").read_text(encoding="utf-8"))
        assert [item["fields"]["amount"]["value"] for item in result["candidates"]] == [11, 22, 33]
        assert result["run"]["usage"] == {"total_tokens": 40}
        assert result["run"]["request_attempts"] == 4
        assert (first / "diagnostics/responses/chunk-0001.txt").read_text(encoding="utf-8") == truncated
        for candidate in result["candidates"]:
            evidence = candidate["fields"]["amount"]["evidence"]
            assert text[evidence["start"]:evidence["end"]] == evidence["quote"]
        assert cli.main(args + ["--resume", str(second)]) == 0
        assert len(observed) == 4
    finally:
        backends.OpenAICompatibleBackend = original
    summary = {"passed": True, "package_version": attestextract.__version__, "module_path": attestextract.__file__,
               "real_endpoint_calls": 0, "mock_http_calls": len(observed), "truncated_parent_calls": 1,
               "successful_child_calls": 3, "resume_new_calls": 2, "completed_resume_new_calls": 0,
               "candidate_values": [11, 22, 33], "previous_run_unchanged": True}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
