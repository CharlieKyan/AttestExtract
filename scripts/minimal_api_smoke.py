"""Installed-wheel acceptance of minimal inputs. Synthetic HTTP only."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx

import attestextract
from attestextract import ModelConnection, RunTrace, cli, extract
from attestextract.errors import BackendError


SOURCE = "Lamp A uses 8 W."
TASK = {"name": "synthetic-power", "record_types": {"power": {"fields": {
    "value": {"type": "integer", "required": True}}}}}
CONTENT = '{"records":[{"record_type":"power","values":{"value":8},"quotes":{"value":"8"}}]}'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    observed = []
    network_methods = []

    def handle(request):
        network_methods.append(request.method)
        if request.method == "GET":
            assert "authorization" not in request.headers
            return httpx.Response(401)
        payload = json.loads(request.content)
        observed.append({"host": request.url.host, "model": payload["model"],
                         "mode": payload.get("response_format", {}).get("type", "off")})
        assert json.loads(payload["messages"][-1]["content"])["source"] == SOURCE
        if payload["model"] == "schema-reject" and payload["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": {"code": "unsupported_value", "param": "response_format",
                                                      "message": "json_schema is not supported"}})
        if payload["model"] == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(200, json={"model": "synthetic-reported-model",
            "choices": [{"message": {"content": "{invalid" if payload["model"] == "bad-format" else CONTENT},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})

    client = httpx.Client

    def offline_client(*a, **kw):
        kw["transport"] = httpx.MockTransport(handle)
        return client(*a, **kw)

    cases = []
    services = [
        ("deepseek", "https://api.deepseek.com", "deepseek-flash", "json_object"),
        ("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.8-27b", "json_object"),
        ("wenwen-gemini", "https://api.wenwen-ai.com/v1", "gemini-2.5-flash", "json_schema"),
        ("wenwen-gpt", "https://api.wenwen-ai.com/v1", "gpt-4.1", "json_schema"),
        ("unregistered", "https://new-service.example/v1", "unlisted:research", "json_schema"),
        ("mode-fallback", "https://new-service.example/v1", "schema-reject", "json_object"),
    ]
    with patch.dict(os.environ, {"ATTESTEXTRACT_MODEL_CACHE_DIR": str(root / "cache")}), patch.object(httpx, "Client", offline_client):
        for name, address, model, expected_mode in services:
            result = extract(SOURCE, TASK, base_url=address, api_key="synthetic-key", model=model,
                             trace=RunTrace(root / name))
            assert result.schema_version == "0.5"
            assert result.candidates[0].fields["value"].value == 8
            provenance = result.run.chunk_provenance["chunk-0001"]
            assert result.run.routes[provenance.route_id]["config"]["structured_output"] == expected_mode
            cases.append({"name": name, "calls": len(result.run.generation_attempts), "passed": True})
        result = extract(SOURCE, TASK, base_url="https://new-service.example/v1", api_key="synthetic-primary",
                         model="bad-format", fallback=ModelConnection(base_url="https://backup.example/v1",
                         model="backup", api_key="synthetic-backup"), trace=RunTrace(root / "backup"))
        assert len(result.run.generation_attempts) == 2
        assert result.run.routes[result.run.chunk_provenance["chunk-0001"].route_id]["role"] == "fallback"
        before = len(observed)
        try:
            extract(SOURCE, TASK, base_url="https://new-service.example/v1", api_key="synthetic-primary",
                    model="timeout", fallback=ModelConnection(base_url="https://backup.example/v1",
                    model="backup", api_key="synthetic-backup"), trace=RunTrace(root / "timeout"))
        except BackendError as exc:
            assert exc.code == "timeout"
        else:
            raise AssertionError("Unknown timeout outcome must stop")
        assert len(observed) == before + 1
        source_path = root / "source.txt"
        task_path = root / "task.json"
        source_path.write_text(SOURCE, encoding="utf-8")
        task_path.write_text(json.dumps(TASK), encoding="utf-8")
        with patch.dict(os.environ, {"ATTESTEXTRACT_API_KEY": "synthetic-cli-key",
                                   "ATTESTEXTRACT_MODEL_CACHE_DIR": str(root / "cli-cache")}):
            for name, address, model, expected_mode in services[:5]:
                output = root / f"cli-{name}.json"
                arguments = ["extract", "--base-url", address, "--model", model,
                             "--input", str(source_path), "--task", str(task_path),
                             "--output", str(output)]
                before = len(observed)
                before_network = len(network_methods)
                assert cli.main(arguments + ["--check"]) == 0
                assert len(network_methods) == before_network
                assert cli.main(arguments) == 0
                assert len(observed) == before + 1
                saved = json.loads(output.read_text(encoding="utf-8"))
                assert saved["schema_version"] == "0.5"
                assert saved["candidates"][0]["fields"]["value"]["value"] == 8
                route = saved["run"]["chunk_provenance"]["chunk-0001"]["route_id"]
                assert saved["run"]["routes"][route]["config"]["structured_output"] == expected_mode
                cases.append({"name": f"cli-{name}", "calls": 1, "passed": True})
    assert len(observed) == 15
    assert importlib.util.find_spec("langextract") is None
    summary = {"passed": True, "package_version": attestextract.__version__,
               "module_path": attestextract.__file__, "real_endpoint_calls": 0,
               "mock_model_posts": len(observed), "cases": cases,
               "backup_calls": 2, "timeout_calls": 1, "observed": observed}
    (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
