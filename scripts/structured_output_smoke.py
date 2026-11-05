"""Installed CLI acceptance: localized feedback and DeepSeek Responses Schema.

Uses synthetic data and MockTransport only. Copy outside the checkout to verify
an installed wheel. Never reads .env or sends requests to a real endpoint.
"""

import argparse
import json
import os
from pathlib import Path

import httpx

import attestextract
from attestextract import cli
from attestextract.backends import OpenAICompatibleBackend
from attestextract.registry import BackendRegistry
from attestextract.responses_backend import DeepSeekResponsesBackend


TEXT = "Sample A reads 12."
TASK = {"name": "Synthetic Schema acceptance", "record_types": {"reading": {
    "fields": {"subject": {"type": "string", "required": True},
               "amount": {"type": "number", "required": True}},
}}}
GOOD = json.dumps({"records": [{"record_type": "reading",
    "values": {"subject": "Sample A", "amount": 12}, "quotes": {"subject": TEXT, "amount": TEXT},
}]})
BAD = ('{"records":[{"record_type":"reading","values":{"subject":'
       '"SYNTHETIC-PRIVATE-VALUE",{"amount":12}},'
       '"quotes":{"subject":"SYNTHETIC-PRIVATE-QUOTE","amount":"Sample A reads 12."}}]}')


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    root = parser.parse_args(argv).output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    for key in list(os.environ):
        if key.startswith(("ATTESTEXTRACT_", "DEEPSEEK_", "QWEN_", "DASHSCOPE_", "VLM_")):
            del os.environ[key]
    os.environ["DEEPSEEK_API_KEY"] = "synthetic-key"
    source, task = root / "source.txt", root / "task.json"
    source.write_text(TEXT, encoding="utf-8")
    task.write_text(json.dumps(TASK), encoding="utf-8")
    calls = []
    truncated = False

    def endpoint(request):
        body = json.loads(request.content)
        calls.append(body)
        assert request.headers["Authorization"] == "Bearer synthetic-key"
        if request.url.path == "/chat/completions":
            assert body["response_format"] == {"type": "json_object"}
            if len(calls) == 1:
                content = BAD
            else:
                feedback = body["messages"][0]["content"]
                assert '"unexpected_object_in_object"' in feedback
                assert '"records[0].values"' in feedback and '"correct_shape"' in feedback
                assert "SYNTHETIC-PRIVATE" not in feedback
                content = GOOD
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})
        assert request.url.path == "/responses"
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["schema"]["properties"]["records"]["type"] == "array"
        assert "strict" not in body["text"]["format"]
        assert not {"messages", "response_format", "max_tokens", "thinking"} & body.keys()
        assert body["max_output_tokens"] == 8192 and body["reasoning"] == {"effort": "none"}
        assert json.loads(body["input"][-1]["content"])["source"] == TEXT
        return httpx.Response(200, json={
            "object": "response", "id": "synthetic-response", "model": "deepseek-flash",
            "status": "incomplete" if truncated else "completed",
            "incomplete_details": {"reason": "max_output_tokens"} if truncated else None,
            "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": GOOD[:20]},
                                    {"type": "output_text", "text": GOOD[20:]}]}],
        })

    registry = BackendRegistry()

    def factory(backend_type):
        def create(*, config, api_key, trace):
            return backend_type(
                config.base_url, config.model, api_key=api_key, trace=trace,
                timeout=config.timeout, max_attempts=config.max_attempts,
                structured_output=config.structured_output, provider_options=config.provider_options,
                output_token_parameter=config.output_token_parameter, tls_mode=config.tls_mode,
                transport=httpx.MockTransport(endpoint),
            )
        return create

    registry.register("openai-compatible", factory(OpenAICompatibleBackend))
    registry.register("deepseek-responses", factory(DeepSeekResponsesBackend))

    def invoke(selector, status=0, extra=()):
        outputs = root / "outputs"
        before = set(outputs.iterdir()) if outputs.exists() else set()
        args = ["extract", "--input", str(source), "--task", str(task),
                "--model-id", selector, "--max-split-depth", "0", *extra]
        assert cli.main(args, registry=registry) == status
        created = set(outputs.iterdir()) - before
        assert len(created) == 1
        return created.pop()

    chat = invoke("deepseek:deepseek-flash", extra=("--format-retries", "1"))
    assert len(calls) == 2
    hint = read(chat / "diagnostics/validation/chunk-0001.errors.json")["errors"][0]["structure"]
    assert hint["path"] == "records[0].values"
    assert (chat / "diagnostics/responses/chunk-0001.txt").read_text(encoding="utf-8") == BAD
    schema = invoke("deepseek-responses:deepseek-flash")
    assert len(calls) == 3
    result = read(schema / "result.json")
    assert result["review"] == [] and result["candidates"][0]["fields"]["amount"]["value"] == 12
    assert result["run"]["usage"] == {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
    info = read(schema / "diagnostics/schema/chunk-0001.request.json")
    assert info["schema_sent"] and info["server_enforcement"] == "not_verified"
    resumed = invoke("deepseek-responses:deepseek-flash", extra=("--resume", str(schema)))
    assert len(calls) == 3
    assert read(resumed / "result.json")["candidates"] == result["candidates"]
    truncated = True
    failed = invoke("deepseek-responses:deepseek-flash", status=1)
    assert len(calls) == 4 and not (failed / "result.json").exists()
    assert read(failed / "diagnostics/error.json")["code"] == "truncated_response"
    summary = {"passed": True, "package_version": attestextract.__version__,
               "module_path": attestextract.__file__, "real_endpoint_calls": 0,
               "mock_http_calls": 4, "localized_format_feedback": True,
               "responses_schema_sent": True, "server_enforcement": "not_verified",
               "completed_resume_new_calls": 0, "truncation_rejected": True}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
