"""Public retry controls are explicit, validated before calls and stage-specific."""

import json

import pytest

from niceextract import cli, extract
from niceextract.errors import ConfigurationError
from niceextract.models import BackendResponse


TEXT = "Sample A weighs 7 g."
TASK = {"prompt_description": "Extract reported sample mass.", "field_list": {"mass": "Reported mass"}}


class Backend:
    name = "synthetic-verification-retry-entry"
    mode = "replay"
    output_protocol = "flat-v1"
    max_attempts = 1

    def __init__(self):
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return BackendResponse(text=json.dumps({"records": [{"record_type": "mass",
            "values": {"entity": "Sample A", "value": "7", "unit": "g", "conditions": None},
            "quotes": {"entity": TEXT, "value": TEXT, "unit": TEXT, "conditions": ""}}]}))


@pytest.mark.parametrize("selection", ["verify-0001", [1], ["invalid"], ["verify-0001", "verify-0001"], ["verify-0001"]])
def test_invalid_or_unanchored_selection_never_generates(selection):
    backend = Backend()
    with pytest.raises(ConfigurationError):
        extract(TEXT, TASK, backend=backend, retry_analysis_jobs=selection, format_retries=1)
    assert backend.calls == 0


def test_analysis_receives_the_existing_format_policy(monkeypatch):
    from niceextract import analysis

    def proposed(text, result, **kwargs):
        assert kwargs["format_retries"] == 1
        assert kwargs["retry_failed_jobs"] is None
        return {"analysis_version": "0.4", "status": "complete"}

    monkeypatch.setattr(analysis, "build_analysis", proposed)
    result = extract(TEXT, TASK, backend=Backend(), table_subject="samples", format_retries=1)
    assert result.analysis["status"] == "complete"


def test_missing_analysis_checkpoint_is_not_treated_as_a_new_job(tmp_path):
    from niceextract.pipeline import check_analysis_resume

    backend = Backend()
    saved = extract(TEXT, TASK, backend=backend, backend_identity="synthetic-retry-v1",
                    output_dir=tmp_path / "initial", progress="quiet")
    with pytest.raises(ConfigurationError, match="saved analysis checkpoint"):
        check_analysis_resume(TEXT, saved.task, backend=backend, generation=None, table_subject="samples",
            max_calls=4, resume_from=tmp_path / "initial", format_retries=1, retry_failed_jobs=["verify-0001"])
    assert backend.calls == 1


def test_resume_command_uses_the_singular_explicit_job_flag(tmp_path):
    args = cli._parser().parse_args(["extract", "--input", str(tmp_path / "source.txt"),
        "--task", str(tmp_path / "task.json"), "--retry-analysis-job", "verify-0002",
        "--format-retries", "1", "--output-dir", str(tmp_path / "new run")])
    command = cli._resume_command(args, tmp_path / "old/diagnostics/execution/checkpoint.json")
    assert command.count("--retry-analysis-job") == 1
    assert "--retry-analysis-jobs" not in command and "['verify" not in command
    assert "verify-0002" in command and "--format-retries" in command


def test_cli_rejects_retry_without_resume_before_model_setup(tmp_path, monkeypatch, capsys):
    import httpx

    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("unexpected network call"))
    source, task = tmp_path / "source.txt", tmp_path / "task.json"
    source.write_text(TEXT)
    task.write_text(json.dumps(TASK))
    code = cli.main(["extract", "--input", str(source), "--task", str(task),
        "--retry-analysis-job", "verify-0001", "--output-dir", str(tmp_path / "run"), "--check"])
    assert code == 1 and "requires --resume and --table-subject" in capsys.readouterr().err
