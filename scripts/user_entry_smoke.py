"""Installed-package acceptance of ordinary output directories. No real network."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx

import attestextract
from attestextract import cli, extract
from attestextract.errors import BackendError, ConfigurationError


SOURCE = "Sample A has mass 17 g."
TASK = {"prompt_description": "Extract explicitly reported sample properties.",
        "field_list": {"mass": "Reported mass", "length": "Reported length"},
        "examples": [{"text": "Sample B has mass 2 g.", "extractions": [
            {"field": "mass", "entity": "Sample B", "value": "2", "unit": "g",
             "quote": "Sample B has mass 2 g."}]}]}
RESPONSE = {"records": [{"record_type": "mass",
    "values": {"entity": "Sample A", "value": "17", "unit": "g", "conditions": None},
    "quotes": {"entity": SOURCE, "value": SOURCE, "unit": SOURCE, "conditions": ""}}]}


def hashes(directory: Path) -> dict:
    return {str(p.relative_to(directory)): sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    root = parser.parse_args().output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    calls = []
    network_attempts = []
    fail = False

    def blocked(*args, **kwargs):
        network_attempts.append(True)
        raise AssertionError("Real network access is forbidden")

    def handle(request):
        if request.method == "GET":
            return httpx.Response(401)
        calls.append(request)
        if fail:
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(200, json={"model": "synthetic-reported-model", "choices": [
            {"message": {"content": json.dumps(RESPONSE)}, "finish_reason": "stop"}]})

    original_client = httpx.Client

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handle)
        return original_client(*args, **kwargs)

    environment = {k: v for k, v in os.environ.items() if not k.startswith("ATTESTEXTRACT_")}
    environment.update(DEEPSEEK_API_KEY="synthetic-entry-key", ATTESTEXTRACT_MODEL_CACHE_DIR=str(root / "cache"))
    with patch.dict(os.environ, environment, clear=True), patch.object(httpx, "Client", mock_client), \
            patch("socket.socket.connect", blocked), patch("socket.create_connection", blocked), \
            patch("socket.getaddrinfo", blocked):
        options = {"model": "deepseek:deepseek-flash", "progress": "quiet", "source_name": "paper.md"}
        first = extract(SOURCE, output_dir=root / "python", **TASK, **options)
        assert first.candidates[0].fields["value"].value == "17"
        assert (root / "python/report.md").is_file()
        checkpoint = root / "python/diagnostics/execution/checkpoint.json"
        assert checkpoint.is_file()
        history = hashes(root / "python")
        resumed = extract(SOURCE, output_dir=root / "resumed", resume_from=checkpoint, **TASK, **options)
        assert resumed.candidates == first.candidates and len(calls) == 1
        assert hashes(root / "python") == history
        try:
            extract(SOURCE, output_dir=root / "python", **TASK, **options)
        except ConfigurationError:
            pass
        else:
            raise AssertionError("Existing output must be rejected")
        assert len(calls) == 1
        source = root / "paper.md"
        source.write_bytes(SOURCE.encode("utf-8"))
        task = root / "task.json"
        task.write_text(json.dumps(TASK), encoding="utf-8")
        args = ["extract", "--input", str(source), "--task", str(task),
                "--model-id", "deepseek:deepseek-flash", "--progress", "quiet"]
        status = cli.main([*args, "--output-dir", str(root / "cli")])
        assert status == 0 and len(calls) == 2
        cli_result = json.loads((root / "cli/result.json").read_text(encoding="utf-8"))
        assert cli_result["candidates"] == first.model_dump(mode="json")["candidates"]
        with redirect_stdout(io.StringIO()):
            assert cli.main([*args, "--output-dir", str(root / "checked"), "--check"]) == 0
        assert len(calls) == 2
        fail = True
        try:
            extract(SOURCE, output_dir=root / "failed", **TASK, **options)
        except BackendError:
            pass
        else:
            raise AssertionError("Read timeout must fail")
        assert len(calls) == 3
        manifest = json.loads((root / "failed/diagnostics/manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "failed" and not (root / "failed/result.json").exists()
        assert (root / "failed/diagnostics/execution/checkpoint.json").is_file()
    assert not network_attempts
    summary = {"passed": True, "package_version": attestextract.__version__,
               "module_path": attestextract.__file__, "mock_model_posts": len(calls),
               "real_model_calls": 0, "network_attempts": 0, "cli_exit_status": status,
               "completed_resume_new_calls": 0, "historical_artifacts_unchanged": True,
               "failure_checkpoint_saved": True, "python_cli_candidates_equal": True}
    (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
