"""CLI smoke tests use synthetic replay or a local fake backend only."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from attestextract import cli
from attestextract.models import BackendResponse
from attestextract.errors import BackendError


def run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if not key.startswith("ATTESTEXTRACT_")}
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "attestextract", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def test_all_bundled_demos_run_offline_outside_the_source_directory(tmp_path):
    output = tmp_path / "examples.json"
    process = run_cli("demo", "--example", "all", "--output", str(output), cwd=tmp_path)

    assert process.returncode == 0, process.stderr
    assert "replay" in process.stderr.lower()
    assert "no model inference" in process.stderr.lower()
    examples = json.loads(output.read_text(encoding="utf-8"))["examples"]
    assert set(examples) == {"ccus", "procurement"}
    assert len(examples["ccus"]["candidates"]) == 2
    assert len(examples["procurement"]["candidates"]) == 3
    for result in examples.values():
        assert result["run"]["mode"] == "replay"
        assert not result["review"]
        assert all(not candidate["needs_review"] for candidate in result["candidates"])


def test_single_demo_prints_valid_json_to_stdout(tmp_path):
    process = run_cli("demo", "--example", "procurement", cwd=tmp_path)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["source"]["name"] == "procurement.md"
    assert result["run"]["mode"] == "replay"


def test_extract_without_model_configuration_fails_without_replay(tmp_path):
    source = tmp_path / "source.txt"
    task = tmp_path / "task.json"
    output = tmp_path / "result.json"
    source.write_text("A result.", encoding="utf-8")
    task.write_text("{}", encoding="utf-8")
    process = run_cli(
        "extract", "--input", str(source), "--task", str(task), "--output", str(output),
        cwd=tmp_path,
    )

    assert process.returncode != 0
    assert "--base-url" in process.stderr and "--model" in process.stderr
    assert "replay" not in process.stderr.lower()
    assert not output.exists()


@pytest.mark.parametrize("extension", ["txt", "md"])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])
def test_extract_reads_utf8_and_uses_the_configured_backend(extension, newline, monkeypatch, tmp_path):
    text = f"采购记录{newline}{newline}编号甲🙂金额 120 元。{newline}"
    source = tmp_path / f"中文输入.{extension}"
    task = tmp_path / "task.json"
    output = tmp_path / "结果.json"
    source.write_bytes(text.encode("utf-8"))
    task.write_text(json.dumps({
        "name": "UTF-8 extraction",
        "record_types": {"purchase": {"fields": {"amount": {"type": "number"}}}},
    }), encoding="utf-8")
    seen = []

    class FakeBackend:
        name = "configured-test-backend"

        def __init__(self, **kwargs):
            seen.append(kwargs)

        def generate(self, request):
            assert json.loads(request.messages[-1]["content"])["source"] == text
            return BackendResponse(text=json.dumps({
                "records": [{"record_type": "purchase", "fields": {"amount": {"value": 120, "quote": "120"}}}]
            }))

    monkeypatch.setattr("attestextract.backends.OpenAICompatibleBackend", FakeBackend)
    monkeypatch.setenv("ATTESTEXTRACT_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("ATTESTEXTRACT_MODEL", "synthetic-test-model")
    monkeypatch.delenv("ATTESTEXTRACT_API_KEY", raising=False)
    status = cli.main(["extract", "--input", str(source), "--task", str(task), "--output", str(output)])

    assert status == 0
    assert seen[0]["base_url"] == "https://example.invalid/v1"
    assert seen[0]["model"] == "synthetic-test-model"
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["run"]["backend"] == "configured-test-backend"
    assert result["run"]["mode"] == "live"
    assert result["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["source"]["character_count"] == len(text)
    evidence = result["candidates"][0]["fields"]["amount"]["evidence"]
    assert evidence["start"] == text.index("120")
    assert text[evidence["start"]:evidence["end"]] == "120"


def test_backend_failure_is_reported_and_does_not_create_success_output(monkeypatch, tmp_path, capsys):
    source = tmp_path / "source.txt"
    task = tmp_path / "task.json"
    output = tmp_path / "result.json"
    source.write_text("A result.", encoding="utf-8")
    task.write_text(json.dumps({
        "name": "Failure test",
        "record_types": {"result": {"fields": {"value": {"type": "string"}}}},
    }), encoding="utf-8")

    class FailingBackend:
        name = "failing-test-backend"

        def __init__(self, **kwargs):
            pass

        def generate(self, request):
            raise BackendError("Synthetic endpoint unavailable")

    monkeypatch.setattr("attestextract.backends.OpenAICompatibleBackend", FailingBackend)
    status = cli.main([
        "extract", "--input", str(source), "--task", str(task), "--output", str(output),
        "--base-url", "https://example.invalid/v1", "--model", "synthetic-test-model",
    ])

    assert status != 0
    assert not output.exists()
    captured = capsys.readouterr()
    assert "Synthetic endpoint unavailable" in captured.err
    assert not captured.out
