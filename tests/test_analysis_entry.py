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
    {"analysis_resume_from": "missing.json"},
])
def test_invalid_analysis_options_fail_before_any_generation(kwargs):
    backend = Backend()
    with pytest.raises(ConfigurationError):
        extract(TEXT, TASK, backend=backend, **kwargs)
    assert backend.calls == []


def test_oversized_document_never_silently_truncates_or_calls():
    backend = Backend()
    with pytest.raises(ConfigurationError, match="60,000"):
        extract("x" * 60001, TASK, backend=backend, table_subject="specimens")
    assert backend.calls == []


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
        return {"analysis_version": "0.1", "status": "complete"}

    monkeypatch.setattr(analysis, "build_analysis", proposed)
    result = extract(TEXT, TASK, backend=Backend(), backend_identity="synthetic-v1", table_subject="specimens", max_analysis_calls=3)
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
        "--table-subject", "specimens", "--max-analysis-calls", "3", "--check"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["network_calls"] == 0
    assert plan["analysis"]["max_analysis_calls"] == 3
    assert plan["analysis"]["combined_generation_limit"] > 3


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
