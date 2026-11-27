"""Offline CLI acceptance for explicit, bounded format-failure recovery advice."""

import json
import os
import re
import shlex
from pathlib import Path

import httpx
import pytest

from niceextract import cli
from niceextract.backends import OpenAICompatibleBackend
from niceextract.execution import digest


TASK = {"name": "Synthetic power", "record_types": {"power": {
    "fields": {"value": {"type": "integer", "required": True}, "unit": {"type": "string"}},
}}}
VALID = '{"records":[{"record_type":"power","values":{"value":8,"unit":"W"},"quotes":{"value":"8","unit":"W"}}]}'
INVALID = '{"records":[{"record_type":"power","values":{"value":8,{"unit":"W"}}}]}'


def response(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})


@pytest.fixture
def setup_cli(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(("NICEEXTRACT_", "ATTESTEXTRACT_", "DEEPSEEK_", "QWEN_", "DASHSCOPE_", "VLM_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-format-guidance-key")
    source = tmp_path / "quoted' source.md"
    task = tmp_path / "task.json"
    source.write_text("Lamp A uses 8 W.\n\nLamp B uses 8 W.", encoding="utf-8")
    task.write_text(json.dumps(TASK), encoding="utf-8")
    args = ["extract", "--input", str(source), "--task", str(task),
            "--model-id", "deepseek:deepseek-flash", "--chunking", "fixed",
            "--max-chunk-chars", "20",
            "--output-dir", str(tmp_path / "selected' run")]
    calls = []

    def intercept(handler):
        def handle(request):
            calls.append(request)
            return handler(request)
        monkeypatch.setattr("niceextract.backends.OpenAICompatibleBackend", lambda **kwargs:
                            OpenAICompatibleBackend(**kwargs, transport=httpx.MockTransport(handle)))

    return args, calls, intercept


def command_tokens(terminal):
    line = next(line for line in terminal.splitlines() if "--resume" in line and "niceextract" in line)
    return ([value.replace("''", "'") for value in re.findall(r"'((?:[^']|'')*)'", line)]
            if os.name == "nt" else shlex.split(line))


def checkpoint_for(args):
    return Path(args[args.index("--output-dir") + 1]) / "diagnostics/execution/checkpoint.json"


def test_disabled_retry_offers_explicit_opt_in_and_preserves_total_budget(setup_cli, capsys):
    args, calls, intercept = setup_cli
    intercept(lambda _: response(INVALID if len(calls) == 1 else VALID))
    assert cli.main(args) == 1
    terminal = capsys.readouterr().err
    checkpoint = checkpoint_for(args)
    old_checkpoint = checkpoint.read_bytes()
    saved = json.loads(old_checkpoint)["state"]
    assert saved["format_retries"] == 0 and saved["calls_used"] == len(calls) == 1
    assert "Ordinary resume cannot advance" in terminal
    assert "per chunk" in terminal and "also processes pending chunks" in terminal
    assert "synthetic-format-guidance-key" not in terminal
    tokens = command_tokens(terminal)
    assert tokens[tokens.index("--format-retries") + 1] == "1"
    assert tokens[tokens.index("--max-model-calls") + 1] == "6"
    assert tokens[tokens.index("--input") + 1] == str(Path(args[args.index("--input") + 1]).resolve())
    old_output = checkpoint.parent.parent.parent
    new_output = Path(tokens[tokens.index("--output-dir") + 1])
    assert new_output.parent == old_output.parent and new_output != old_output
    assert not new_output.exists()

    # The displayed opt-in actually progresses the failed chunk and the remaining chunk.
    assert cli.main(tokens[tokens.index("extract"):]) == 0
    assert len(calls) == 3 and checkpoint.read_bytes() == old_checkpoint
    resumed = json.loads((new_output / "diagnostics/execution/checkpoint.json").read_bytes())["state"]
    assert resumed["max_model_calls"] == 6 and resumed["calls_used"] == 3
    assert resumed["nodes"]["chunk-0001"]["format_retries_reserved"] == 1


def test_exhausted_retry_does_not_offer_ineffective_repeat(setup_cli, capsys):
    args, calls, intercept = setup_cli
    intercept(lambda _: response(INVALID))
    assert cli.main(args + ["--format-retries", "1"]) == 1
    terminal = capsys.readouterr().err
    assert len(calls) == 2
    assert "single format regeneration has already been reserved" in terminal
    assert "increasing the total budget does not reset it" in terminal
    assert "Resume checkpoint:" in terminal and "--resume" not in terminal


def test_unchanged_resume_after_disabled_retry_offers_real_opt_in(setup_cli, capsys):
    args, calls, intercept = setup_cli
    intercept(lambda _: response(INVALID))
    assert cli.main(args) == 1
    capsys.readouterr()
    original = checkpoint_for(args)
    resume_args = list(args)
    resume_args[resume_args.index("--output-dir") + 1] += "-resume"
    assert cli.main(resume_args + ["--resume", str(original)]) == 1
    terminal = capsys.readouterr().err
    assert len(calls) == 1  # Exhaustion was checked before any new model call.
    assert "--format-retries" in terminal and "Ordinary resume cannot advance" in terminal


@pytest.mark.parametrize("failure", ["read_timeout", "truncated_response"])
def test_other_failures_keep_existing_resume_behavior(setup_cli, capsys, failure):
    args, calls, intercept = setup_cli
    def fail(request):
        if failure == "read_timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]})
    intercept(fail)
    assert cli.main(args + ["--max-split-depth", "0"]) == 1
    terminal = capsys.readouterr().err
    assert len(calls) == 1 and "Resume in a new run" in terminal
    tokens = command_tokens(terminal)
    assert "--format-retries" not in tokens


def test_format_failure_with_no_total_budget_does_not_offer_nonfunctional_command(setup_cli, capsys):
    args, calls, intercept = setup_cli
    intercept(lambda _: response(INVALID))
    assert cli.main(args + ["--max-model-calls", "1"]) == 1
    terminal = capsys.readouterr().err
    assert len(calls) == 1 and "saved total call budget is exhausted" in terminal
    assert "--resume" not in terminal


@pytest.mark.parametrize("damage", ["invalid_json", "wrong_checksum", "unknown_version", "missing_retry_metadata"])
def test_unverifiable_checkpoint_does_not_claim_unused_retry(setup_cli, capsys, monkeypatch, damage):
    args, calls, intercept = setup_cli
    intercept(lambda _: response(INVALID))
    original = cli._show_resume

    def corrupt_before_guidance(parsed, trace, exc=None):
        checkpoint = trace.directory / "execution/checkpoint.json"
        envelope = json.loads(checkpoint.read_bytes())
        if damage == "invalid_json":
            checkpoint.write_text("{", encoding="utf-8")
        else:
            if damage == "wrong_checksum":
                envelope["sha256"] = "wrong"
            elif damage == "unknown_version":
                envelope["state"]["checkpoint_version"] = "unknown"
                envelope["sha256"] = digest(envelope["state"])
            else:
                del envelope["state"]["nodes"]["chunk-0001"]["format_retries_reserved"]
                envelope["sha256"] = digest(envelope["state"])
            checkpoint.write_text(json.dumps(envelope), encoding="utf-8")
        original(parsed, trace, exc)

    monkeypatch.setattr(cli, "_show_resume", corrupt_before_guidance)
    assert cli.main(args) == 1
    terminal = capsys.readouterr().err
    assert len(calls) == 1 and "Could not verify an unused" in terminal
    assert "--resume" not in terminal and "--format-retries" not in terminal
