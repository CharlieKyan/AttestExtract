"""Offline installed-package acceptance for Python and CLI model selection."""

import argparse
import json
import os
from pathlib import Path

import httpx

import attestextract
from attestextract import backends, cli, extract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    output = parser.parse_args().output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # This executable smoke test uses only synthetic keys and MockTransport.
    for key in list(os.environ):
        if key.startswith(("ATTESTEXTRACT_", "DEEPSEEK_", "QWEN_", "DASHSCOPE_", "VLM_")):
            del os.environ[key]
    os.environ.update(DEEPSEEK_API_KEY="synthetic-deepseek-key", VLM_API_KEY="synthetic-qwen-key")
    text = "Lamp A uses 8 W."
    task = {"name": "synthetic-power", "record_types": {"power": {"fields": {
        "value": {"type": "integer", "required": True},
    }}}}
    source = output / "source.txt"
    task_path = output / "task.json"
    source.write_text(text, encoding="utf-8")
    task_path.write_text(json.dumps(task), encoding="utf-8")
    observed = []
    provider_default = False

    def handler(request):
        body = json.loads(request.content)
        expected_key = "synthetic-deepseek-key" if body["model"] == "deepseek-flash" else "synthetic-qwen-key"
        assert request.headers["Authorization"] == "Bearer " + expected_key
        assert json.loads(body["messages"][-1]["content"])["source"] == text
        if provider_default:
            assert "max_tokens" not in body and "max_completion_tokens" not in body
        else:
            assert body["max_tokens"] == 8192
        assert body["response_format"] == {"type": "json_object"}
        if body["model"] == "deepseek-flash":
            assert request.url.host == "api.deepseek.com" and body["thinking"] == {"type": "disabled"}
        else:
            assert request.url.host == "dashscope.aliyuncs.com" and body["enable_thinking"] is False
        observed.append({"model": body["model"], "host": request.url.host})
        return httpx.Response(200, json={"model": body["model"], "choices": [{
            "finish_reason": "stop", "message": {"content": json.dumps({"records": [{
                "record_type": "power", "values": {"value": 8}, "quotes": {"value": "8"},
            }]})},
        }]})

    original = backends.OpenAICompatibleBackend
    backends.OpenAICompatibleBackend = lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler))
    results = []
    try:
        for index, selector in enumerate(("deepseek:deepseek-flash", "qwen:qwen3.8-27b")):
            result = extract(text, task, model=selector)
            result_path = output / f"cli-{index}.json"
            assert cli.main(["extract", "--model-id", selector, "--input", str(source), "--task", str(task_path),
                             "--output", str(result_path)]) == 0
            cli_result = json.loads(result_path.read_text(encoding="utf-8"))
            minimal = ["extract", "--model-id", selector, "--input", str(source), "--task", str(task_path)]
            calls_before_check = len(observed)
            assert cli.main(minimal + ["--check"]) == 0
            assert len(observed) == calls_before_check
            assert cli.main(minimal) == 0
            completed = list((output / "outputs").glob("*/result.json"))
            assert len(completed) == index + 1
            for path in completed:
                minimal_result = json.loads(path.read_text(encoding="utf-8"))
                assert minimal_result["candidates"] == cli_result["candidates"]
                assert (path.parent / "report.md").is_file()
                assert (path.parent / "diagnostics/manifest.json").is_file()
            assert result.model_dump(mode="json")["candidates"] == cli_result["candidates"]
            candidate = result.candidates[0]
            assert len(result.candidates) == 1 and candidate.fields["value"].value == 8
            evidence = candidate.fields["value"].evidence
            assert evidence.status == "matched" and text[evidence.start:evidence.end] == "8"
            results.append({"selector": selector, "python_passed": True, "cli_passed": True,
                            "value": 8, "exact_source_span": [evidence.start, evidence.end]})
        provider_default = True
        provider_args = ["extract", "--model-id", "deepseek:deepseek-flash", "--input", str(source),
                         "--task", str(task_path), "--max-output-tokens", "provider"]
        assert cli.main(provider_args + ["--check"]) == 0
        assert len(observed) == 6
        assert cli.main(provider_args) == 0
        completed = list((output / "outputs").glob("*/result.json"))
        assert len(completed) == 3
    finally:
        backends.OpenAICompatibleBackend = original
    assert len(observed) == 7
    summary = {"package_version": attestextract.__version__, "module_path": attestextract.__file__,
               "real_endpoint_calls": 0, "mock_http_calls": len(observed), "passed": True, "cases": results}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
