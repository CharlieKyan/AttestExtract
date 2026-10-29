"""Optional reading reports share extraction results and protect local inputs."""

import json

import pytest

from attestextract import cli
from attestextract.errors import BackendError
from attestextract.models import BackendResponse


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    source, task, env = (tmp_path / name for name in ("paper.md", "task.json", ".env"))
    source.write_text("合成样品🙂的数值为 12。", encoding="utf-8")
    task.write_text(json.dumps({"name": "Synthetic report", "record_types": {
        "entry": {"fields": {"amount": {"type": "number"}}},
    }}), encoding="utf-8")
    env.write_text("ATTESTEXTRACT_MODEL=synthetic-model\n", encoding="utf-8")
    monkeypatch.setenv("ATTESTEXTRACT_BASE_URL", "https://offline.invalid/v1")
    monkeypatch.setenv("ATTESTEXTRACT_MODEL", "synthetic-model")
    monkeypatch.delenv("ATTESTEXTRACT_API_KEY", raising=False)
    return source, task, env


def arguments(inputs, output, report=None, logs=None):
    source, task, env = inputs
    args = ["extract", "--input", str(source), "--task", str(task),
            "--env-file", str(env), "--output", str(output)]
    if report is not None:
        args += ["--report", str(report)]
    if logs is not None:
        args += ["--log-dir", str(logs)]
    return args


def configured_backend(monkeypatch, *, quote="12", failure=False):
    calls = []

    class OfflineBackend:
        name = "synthetic-report-test"

        def __init__(self, **kwargs):
            calls.append("configured")

        def generate(self, request):
            calls.append(request)
            if failure:
                raise BackendError("Synthetic failure")
            return BackendResponse(text=json.dumps({"records": [{
                "record_type": "entry", "fields": {"amount": {"value": 12, "quote": quote}},
            }]}))

    monkeypatch.setattr(cli, "OpenAICompatibleBackend", OfflineBackend)
    return calls


@pytest.mark.parametrize("with_report", [False, True])
def test_optional_report_uses_the_same_result_without_an_extra_model_call(inputs, tmp_path, monkeypatch, with_report):
    output, report, logs = (tmp_path / name for name in ("extraction.json", "review.md", "diagnostics"))
    calls = configured_backend(monkeypatch)
    assert cli.main(arguments(inputs, output, report if with_report else None, logs)) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert len(calls) == 2  # One construction and one generation.
    assert result["candidates"][0]["fields"]["amount"]["value"] == 12
    assert result["run"]["response_count"] == 1
    assert json.loads((logs / "result.json").read_text(encoding="utf-8")) == result
    assert json.loads((logs / "manifest.json").read_text(encoding="utf-8"))["status"] == "succeeded"
    assert report.exists() is with_report
    if with_report:
        text = report.read_text(encoding="utf-8")
        assert "| amount | 12 | 12 | matched |" in text
        assert "候选：1；待复核候选：0" in text


@pytest.mark.parametrize("conflict", ["same-output", "source", "task", "env", "inside-log", "ancestor-log", "missing-parent", "directory"])
def test_report_path_conflicts_fail_before_backend_creation_and_preserve_files(inputs, tmp_path, monkeypatch, conflict):
    output, logs = tmp_path / "extraction.json", tmp_path / "diagnostics"
    paths = {"same-output": output, "source": inputs[0], "task": inputs[1], "env": inputs[2],
             "inside-log": logs / "review.md", "ancestor-log": tmp_path / "review.md",
             "missing-parent": tmp_path / "missing" / "review.md",
             "directory": tmp_path}
    if conflict == "ancestor-log":
        logs = paths[conflict] / "diagnostics"
    before = {path: path.read_bytes() for path in inputs}
    calls = configured_backend(monkeypatch)
    assert cli.main(arguments(inputs, output, paths[conflict], logs)) == 1
    assert calls == []
    assert all(path.read_bytes() == original for path, original in before.items())
    assert not output.exists()


def test_json_output_cannot_overwrite_the_paper_even_without_report(inputs, monkeypatch):
    original = inputs[0].read_bytes()
    calls = configured_backend(monkeypatch)
    assert cli.main(arguments(inputs, inputs[0])) == 1
    assert calls == [] and inputs[0].read_bytes() == original


def test_failed_extraction_produces_no_success_report(inputs, tmp_path, monkeypatch):
    output, report = tmp_path / "extraction.json", tmp_path / "review.md"
    configured_backend(monkeypatch, failure=True)
    assert cli.main(arguments(inputs, output, report, tmp_path / "diagnostics")) == 1
    assert not output.exists() and not report.exists()


def test_report_masks_configured_secret_before_markdown_escaping(inputs, tmp_path, monkeypatch):
    secret = 'synthetic_key-*<tag>\\"token'
    inputs[0].write_text("12 " + secret, encoding="utf-8")
    monkeypatch.setenv("ATTESTEXTRACT_API_KEY", secret)
    calls = configured_backend(monkeypatch, quote="12 " + secret)
    output, report = tmp_path / "extraction.json", tmp_path / "review.md"
    assert cli.main(arguments(inputs, output, report)) == 0
    text = report.read_text(encoding="utf-8")
    assert "synthetic" not in text and "tag" not in text and "token" not in text
    assert "REDACTED" in text
    assert json.loads(output.read_text(encoding="utf-8"))["candidates"][0]["fields"]["amount"]["evidence"]["quote"] == "12 " + secret
    assert len(calls) == 2
