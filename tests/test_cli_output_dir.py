"""Ordinary CLI output directories preserve evidence and never overwrite a run."""

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket

import pytest

from attestextract import cli
from attestextract.models import BackendResponse
from attestextract.registry import BackendRegistry


SOURCE = "Sample A has mass 17 g."
TASK = {"prompt_description": "Extract reported sample mass.",
        "field_list": {"mass": "Reported mass, retaining units and conditions."}}
RESPONSE = {"records": [{"record_type": "mass",
    "values": {"entity": "Sample A", "value": "17", "unit": "g", "conditions": None},
    "quotes": {"entity": SOURCE, "value": SOURCE, "unit": SOURCE, "conditions": ""}}]}


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Network access is forbidden in output-directory tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    for key in ("ATTESTEXTRACT_BASE_URL", "ATTESTEXTRACT_MODEL", "ATTESTEXTRACT_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def setup_cli(tmp_path, *, source=SOURCE, response=None):
    source_path, task_path = tmp_path / "paper.md", tmp_path / "task.json"
    source_path.write_bytes(source.encode("utf-8"))
    task_path.write_text(json.dumps(TASK), encoding="utf-8")
    calls = []
    content = json.dumps(RESPONSE) if response is None else response

    class Backend:
        name = "synthetic-output-dir"
        mode = "replay"
        output_protocol = "flat-v1"

        def generate(self, request):
            calls.append(request)
            return BackendResponse(text=content, model="synthetic-output-model")

    registry = BackendRegistry()
    registry.register("synthetic", lambda **_: Backend())
    args = ["extract", "--input", str(source_path), "--task", str(task_path),
            "--base-url", "https://synthetic.invalid/v1", "--model", "synthetic-output-model",
            "--adapter", "synthetic", "--progress", "quiet"]
    return args, registry, calls


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def hashes(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


@pytest.mark.parametrize("existing_empty", [False, True])
def test_output_dir_saves_complete_result_report_and_checkpoint(tmp_path, existing_empty):
    args, registry, calls = setup_cli(tmp_path)
    output = tmp_path / "chosen" / "run"
    if existing_empty:
        output.mkdir(parents=True)
    assert cli.main([*args, "--output-dir", str(output)], registry=registry) == 0
    assert len(calls) == 1
    result = read(output / "result.json")
    assert result["candidates"][0]["fields"]["value"]["value"] == "17"
    assert "17" in (output / "report.md").read_text(encoding="utf-8")
    assert read(output / "diagnostics/manifest.json")["status"] == "succeeded"
    assert read(output / "diagnostics/execution/checkpoint.json")["state"]["calls_used"] == 1
    assert read(output / "diagnostics/task.json")["input_materials"]["field_list"] == TASK["field_list"]
    assert (output / "diagnostics/responses/chunk-0001.txt").read_text(encoding="utf-8") == json.dumps(RESPONSE)
    assert not (tmp_path / "outputs").exists()


@pytest.mark.parametrize("flag", ["--output", "--report", "--log-dir"])
def test_output_dir_rejects_advanced_path_mix_before_credentials(tmp_path, monkeypatch, capsys, flag):
    args, registry, calls = setup_cli(tmp_path)
    monkeypatch.setattr(cli, "load_env_file", lambda *a: pytest.fail("credentials read"))
    output = tmp_path / "run"
    assert cli.main([*args, "--env-file", "not-read.env", "--output-dir", str(output),
                     flag, str(tmp_path / "other")], registry=registry) == 1
    assert "cannot be combined" in capsys.readouterr().err
    assert not output.exists() and calls == []


@pytest.mark.parametrize("existing_file", [False, True])
def test_output_dir_does_not_touch_existing_content(tmp_path, monkeypatch, existing_file):
    args, registry, calls = setup_cli(tmp_path)
    output = tmp_path / "keep"
    if existing_file:
        output.write_bytes(b"original file")
    else:
        output.mkdir()
        (output / "notes.txt").write_bytes(b"original notes")
    before = output.read_bytes() if existing_file else hashes(output)
    monkeypatch.setattr(cli, "load_env_file", lambda *a: pytest.fail("credentials read"))
    assert cli.main([*args, "--env-file", "not-read.env", "--output-dir", str(output)], registry=registry) == 1
    assert (output.read_bytes() if existing_file else hashes(output)) == before
    assert calls == []


def test_output_dir_cannot_replace_the_source_file(tmp_path, capsys):
    args, registry, calls = setup_cli(tmp_path)
    output = tmp_path / "run"
    output.mkdir()
    source = output / "result.json"
    source.write_text(SOURCE, encoding="utf-8")
    args[args.index("--input") + 1] = str(source)
    assert cli.main([*args, "--output-dir", str(output)], registry=registry) == 1
    assert "must differ from the input" in capsys.readouterr().err
    assert list(output.iterdir()) == [source] and source.read_text(encoding="utf-8") == SOURCE
    assert calls == []


def test_output_dir_check_is_offline_and_does_not_claim_extraction(tmp_path, capsys):
    args, registry, calls = setup_cli(tmp_path)
    output = tmp_path / "preview"
    assert cli.main([*args, "--output-dir", str(output), "--check"], registry=registry) == 0
    plan = json.loads(capsys.readouterr().out)
    assert calls == [] and plan["network_calls"] == 0
    assert Path(plan["output"]) == output / "result.json"
    assert read(output / "diagnostics/manifest.json")["status"] == "checked"
    assert (output / "diagnostics/check.json").is_file()
    assert not (output / "result.json").exists() and not (output / "report.md").exists()


def test_failure_keeps_diagnostics_without_a_success_result(tmp_path):
    args, registry, calls = setup_cli(tmp_path, response="invalid JSON")
    output = tmp_path / "failed"
    assert cli.main([*args, "--output-dir", str(output)], registry=registry) == 1
    assert len(calls) == 1
    assert read(output / "diagnostics/manifest.json")["status"] == "failed"
    assert read(output / "diagnostics/execution/checkpoint.json")["state"]["calls_used"] == 1
    assert (output / "diagnostics/responses/chunk-0001.txt").read_text() == "invalid JSON"
    assert (output / "diagnostics/error.json").is_file()
    assert not (output / "result.json").exists() and not (output / "report.md").exists()


def test_partial_table_result_is_saved_and_returns_status_two(tmp_path):
    table = "<table><tr><th>Mass</th></tr><tr><td>17</td></tr></table>"
    valid = {"record_id": "mass-1", "record_type": "mass", "fields": {
        "entity": {"value": None, "sources": [], "relation": None},
        "value": {"value": "17", "sources": [{"ref": "table-0001-r0001-c0000", "quote": "17", "offset": 0}], "relation": "anchor"},
        "unit": {"value": None, "sources": [], "relation": None},
        "conditions": {"value": None, "sources": [], "relation": None}},
        "anchors": [{"ref": "table-0001-r0001-c0000", "quote": "17", "offset": 0}],
        "scope": "individual", "scope_sources": []}
    args, registry, calls = setup_cli(tmp_path, source=table,
        response=json.dumps({"records": [valid, {"record_id": "invalid"}]}))
    output = tmp_path / "partial"
    assert cli.main([*args, "--output-dir", str(output)], registry=registry) == 2
    assert len(calls) == 1
    result = read(output / "result.json")
    assert result["format_status"] == "partial_format_error"
    assert len(result["table_results"][0]["candidates"]) == 1
    assert (output / "report.md").is_file()
    assert read(output / "diagnostics/manifest.json")["status"] == "partial_format_error"


def test_resume_command_selects_new_sibling_and_reuses_completed_work(tmp_path):
    args, registry, calls = setup_cli(tmp_path)
    old = tmp_path / "selected"
    original_args = [*args, "--output-dir", str(old)]
    assert cli.main(original_args, registry=registry) == 0
    before = hashes(old)
    checkpoint = old / "diagnostics/execution/checkpoint.json"
    command = cli._resume_command(cli._parser().parse_args(original_args), checkpoint)
    tokens = ([value.replace("''", "'") for value in re.findall(r"'((?:[^']|'')*)'", command)]
              if os.name == "nt" else shlex.split(command))
    output = Path(tokens[tokens.index("--output-dir") + 1])
    assert output != old and output.parent == old.parent and not output.exists()
    assert "--output" not in tokens and "--report" not in tokens and "--log-dir" not in tokens
    assert cli.main(tokens[tokens.index("extract"):], registry=registry) == 0
    assert len(calls) == 1 and hashes(old) == before
    assert read(output / "result.json")["candidates"] == read(old / "result.json")["candidates"]


def test_resume_cannot_create_a_directory_inside_the_old_run(tmp_path):
    args, registry, calls = setup_cli(tmp_path)
    old = tmp_path / "first"
    assert cli.main([*args, "--output-dir", str(old)], registry=registry) == 0
    before = hashes(old)
    destination = old / "nested"
    assert cli.main([*args, "--output-dir", str(destination), "--resume", str(old)], registry=registry) == 1
    assert not destination.exists() and hashes(old) == before and len(calls) == 1
