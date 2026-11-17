"""Public entry boundaries: opt-in cost, legacy artifacts, failed analysis."""

import json

import pytest

from attestextract import extract, cli
from attestextract.errors import ConfigurationError
from attestextract.models import BackendResponse


TEXT = "Specimen A has mass 17 g."
TASK = {"prompt_description": "Extract specimen properties.",
        "field_list": {"mass": "Reported mass"}}


class Backend:
    name = "synthetic-analysis-entry"
    output_protocol = "flat-v1"
    mode = "replay"
    max_attempts = 1

    def __init__(self):
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        return BackendResponse(text=json.dumps({"records": [{"record_type": "mass",
            "values": {"entity": "Specimen A", "value": "17", "unit": "g", "conditions": None},
            "quotes": {"entity": TEXT, "value": TEXT, "unit": TEXT, "conditions": ""}}]}))


@pytest.mark.parametrize("kwargs", [
    {"table_subject": " "}, {"table_subject": "specimens", "max_analysis_calls": 0},
    {"table_subject": "specimens", "max_analysis_calls": True},
    {"table_subject": "specimens", "analysis_max_steps": 0},
    {"table_subject": "specimens", "analysis_max_steps": 13},
    {"table_subject": "specimens", "analysis_max_steps": True},
    {"table_subject": "specimens", "analysis_input_budget": 11999},
    {"table_subject": "specimens", "analysis_input_budget": 256001},
    {"analysis_resume_from": "missing.json"},
])
def test_invalid_analysis_options_fail_before_any_generation(kwargs):
    backend = Backend()
    with pytest.raises(ConfigurationError):
        extract(TEXT, TASK, backend=backend, **kwargs)
    assert backend.calls == []


def test_long_document_passes_analysis_precheck_without_truncation():
    from attestextract.pipeline import validate_analysis_request
    from attestextract.task_input import resolve_task

    # New policy bounds each request rather than refusing the whole source.
    source = "Original source paragraph.\n\n" * 3000
    assert len(source) > 60000
    validate_analysis_request(source, resolve_task(TASK), "specimens", 4,
                              max_steps=3, input_budget=24000)


def test_hidden_http_retry_rejected_before_extraction():
    backend = Backend()
    backend.max_attempts = 2
    with pytest.raises(ConfigurationError, match="max_attempts"):
        extract(TEXT, TASK, backend=backend, table_subject="specimens")
    assert backend.calls == []


def test_analysis_opt_in_is_not_serialized_into_legacy_result(tmp_path, monkeypatch):
    from attestextract import analysis

    def proposed(text, result, **kwargs):
        assert text == TEXT and result.candidates[0].fields["value"].value == "17"
        assert kwargs["table_subject"] == "specimens"
        assert kwargs["max_calls"] == 3
        assert kwargs["max_steps"] == 3
        assert kwargs["input_budget"] == 24000
        return {"analysis_version": "0.1", "status": "complete"}

    monkeypatch.setattr(analysis, "build_analysis", proposed)
    result = extract(TEXT, TASK, backend=Backend(), backend_identity="synthetic-v1", table_subject="specimens",
                     max_analysis_calls=3, analysis_max_steps=3, analysis_input_budget=24000)
    assert result.analysis["status"] == "complete"
    assert "analysis" not in result.model_dump(mode="json")
    assert result.candidates[0].fields["value"].value == "17"


def test_failed_analysis_keeps_extraction_and_marks_manifest(tmp_path, monkeypatch):
    from attestextract import analysis, analysis_export

    failed = {"analysis_version": "0.1", "status": "failed", "error_code": "transport_error"}
    monkeypatch.setattr(analysis, "build_analysis", lambda *a, **k: failed)

    def save(payload, directory, **kwargs):
        directory.mkdir()
        (directory / "analysis.json").write_text(json.dumps(payload))

    monkeypatch.setattr(analysis_export, "save_analysis", save)
    result = extract(TEXT, TASK, backend=Backend(), backend_identity="synthetic-v1",
                     table_subject="specimens", output_dir=tmp_path / "run", progress="quiet")
    assert result.analysis["status"] == "failed"
    payload = json.loads((tmp_path / "run/result.json").read_text())
    assert len(payload["candidates"]) == 1 and "analysis" not in payload
    manifest = json.loads((tmp_path / "run/diagnostics/manifest.json").read_text())
    assert manifest["status"] == "analysis_incomplete"
    assert manifest["analysis_status"] == "failed"
    assert (tmp_path / "run/diagnostics/extraction-result.json").is_file()


def test_cli_check_shows_additional_budget_without_network(tmp_path, monkeypatch, capsys):
    import httpx

    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("offline check attempted HTTP"))
    monkeypatch.setenv("ATTESTEXTRACT_API_KEY", "synthetic-check-key")
    source, task = tmp_path / "source.txt", tmp_path / "task.json"
    source.write_text(TEXT)
    task.write_text(json.dumps(TASK))
    assert cli.main(["extract", "--input", str(source), "--task", str(task),
        "--base-url", "https://synthetic.invalid/v1", "--model", "synthetic-model",
        "--structured-output", "json_object", "--output-dir", str(tmp_path / "check"),
        "--table-subject", "specimens", "--max-analysis-calls", "3",
        "--analysis-max-steps", "3", "--analysis-input-budget", "24000", "--check"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["network_calls"] == 0
    assert plan["analysis"]["max_analysis_calls"] == 3
    assert plan["analysis"]["combined_generation_limit"] > 3
    assert plan["analysis"]["max_steps_per_job"] == 3
    assert plan["analysis"]["request_utf8_byte_limit"] == 24000
    assert "source_character_limit" not in plan["analysis"]


def test_cli_check_rejects_retries_from_profile_before_network(tmp_path, monkeypatch, capsys):
    import httpx
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("offline check attempted HTTP"))
    monkeypatch.setenv("ATTESTEXTRACT_API_KEY", "synthetic-check-key")
    source, task, config = tmp_path / "source.txt", tmp_path / "task.json", tmp_path / "models.toml"
    source.write_text(TEXT)
    task.write_text(json.dumps(TASK))
    config.write_text('[profiles.test]\nbase_url="https://synthetic.invalid/v1"\nmodel="synthetic-model"\nmax_attempts=2\n')
    assert cli.main(["extract", "--input", str(source), "--task", str(task),
        "--config", str(config), "--profile", "test", "--table-subject", "specimens",
        "--output-dir", str(tmp_path / "check"), "--check"]) == 1
    assert "max_attempts=1" in capsys.readouterr().err


def test_cli_long_source_check_is_offline_and_retains_source_size(tmp_path, monkeypatch, capsys):
    import httpx

    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("offline check attempted HTTP"))
    monkeypatch.setenv("ATTESTEXTRACT_API_KEY", "synthetic-check-key")
    text = (TEXT + "\n\n") * 3000
    source, task = tmp_path / "long.txt", tmp_path / "task.json"
    source.write_text(text, encoding="utf-8", newline="")
    task.write_text(json.dumps(TASK), encoding="utf-8")
    assert cli.main(["extract", "--input", str(source), "--task", str(task),
        "--base-url", "https://synthetic.invalid/v1", "--model", "synthetic-model",
        "--structured-output", "json_object", "--output-dir", str(tmp_path / "check"),
        "--table-subject", "specimens", "--check"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["network_calls"] == 0
    assert plan["character_count"] == len(text) > 60000
    assert plan["analysis"]["request_utf8_byte_limit"] == 48000


def test_verification_console_distinguishes_model_steps_and_local_tools():
    import io
    from attestextract.progress import ConsoleProgress

    stream = io.StringIO()
    console = ConsoleProgress(stream=stream)
    console({"stage": "analysis", "event": "started", "limit": 8, "max_steps": 4})
    console({"stage": "analysis", "event": "call_reserved", "job_id": "verify-1", "used": 1, "limit": 8, "step": 1})
    console({"stage": "analysis", "event": "tool", "job_id": "verify-1", "operation": "search", "count": 2,
             "query": "private source query"})
    assert console._waiting_since is None
    console({"stage": "analysis", "event": "step_limit", "job_id": "verify-1"})
    console({"stage": "analysis", "event": "reused", "job_id": "verify-2", "status": "failed"})
    console.close()
    output = stream.getvalue()
    assert "step 1" in output and "local search; 2 source passages" in output
    assert "no generation for the local tool" in output and "unresolved observations retained" in output
    assert "private source query" not in output
    assert "preserved failed state; no request resent" in output
    assert "reused validated response" not in output


def test_verification_console_explains_known_rejection_without_echoing_provider_text():
    import io
    from attestextract.progress import ConsoleProgress

    stream = io.StringIO()
    console = ConsoleProgress(stream=stream)
    console({"stage": "analysis", "event": "failed", "job_id": "verify-1", "used": 1, "limit": 8,
             "error_code": "json_prompt_required", "http_status": 400,
             "request_outcome": "http_error_received", "http_details": "http/verify-1.error.json",
             "error": "PRIVATE PROVIDER PAPER CONTENT"})
    console.close()
    output = stream.getvalue()
    assert "explicit JSON output instruction" in output
    assert "No automatic retry or mode downgrade" in output
    assert "diagnostics/http/verify-1.error.json" in output
    assert "PRIVATE PROVIDER" not in output and "outcome is unknown" not in output
