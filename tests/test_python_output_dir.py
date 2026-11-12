"""Ordinary Python runs save usable artifacts without caller lifecycle code."""

from copy import deepcopy
import json

import httpx
import pytest

from attestextract import cli, extract, RunTrace
from attestextract.backends import OpenAICompatibleBackend
from attestextract.errors import ConfigurationError, InvalidResponseError
from attestextract.models import BackendResponse
from attestextract.progress import ConsoleProgress


SOURCE = "Sample A has mass 17 g."
TASK = {"prompt_description": "Extract reported sample properties.",
        "field_list": {"mass": "Reported sample mass", "length": "Reported sample length"}}
RESPONSE = {"records": [{"record_type": "mass",
    "values": {"entity": "Sample A", "value": "17", "unit": "g", "conditions": None},
    "quotes": {"entity": SOURCE, "value": SOURCE, "unit": SOURCE, "conditions": ""}}]}


class SyntheticBackend:
    name = "synthetic-output-run"
    mode = "replay"
    output_protocol = "flat-v1"

    def __init__(self, response=None, failure=None):
        self.response = RESPONSE if response is None else response
        self.failure = failure
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        if self.failure is not None:
            raise self.failure
        return BackendResponse(text=json.dumps(self.response), model="synthetic-fixed-model")


@pytest.fixture(autouse=True)
def only_mock_http(monkeypatch, tmp_path):
    original = httpx.Client.send

    def send(client, *args, **kwargs):
        assert isinstance(client._transport, httpx.MockTransport), "Real network is forbidden"
        return original(client, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setenv("ATTESTEXTRACT_MODEL_CACHE_DIR", str(tmp_path / "cache"))


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def run(directory, backend=None, **kwargs):
    return extract(SOURCE, backend=backend or SyntheticBackend(), output_dir=directory,
                   backend_identity="synthetic-model-config-v1", **TASK, **kwargs)


def test_output_directory_saves_result_report_raw_response_and_checkpoint(tmp_path, capsys):
    directory = tmp_path / "run"
    result = run(directory)
    assert read(directory / "result.json") == result.model_dump(mode="json")
    assert read(directory / "diagnostics/result.json") == read(directory / "result.json")
    assert "17" in (directory / "report.md").read_text(encoding="utf-8")
    assert "length" in (directory / "report.md").read_text(encoding="utf-8")
    assert read(directory / "diagnostics/task.json")["input_materials"]["field_list"] == TASK["field_list"]
    assert read(directory / "diagnostics/responses/chunk-0001.txt") == RESPONSE
    assert read(directory / "diagnostics/manifest.json")["status"] == "succeeded"
    state = read(directory / "diagnostics/execution/checkpoint.json")["state"]
    assert state["calls_used"] == 1 and state["max_model_calls"] == 3
    stderr = capsys.readouterr().err
    assert "Task: 2 targets" in stderr and "Resume checkpoint:" in stderr


@pytest.mark.parametrize("existing", ["empty", "populated", "file"])
def test_exact_output_directory_accepts_empty_and_never_overwrites(tmp_path, existing):
    directory = tmp_path / "destination"
    if existing == "file":
        directory.write_text("keep", encoding="utf-8")
    else:
        directory.mkdir()
        if existing == "populated":
            (directory / "keep.txt").write_text("original", encoding="utf-8")
    backend = SyntheticBackend()
    if existing == "empty":
        run(directory, backend, progress="quiet")
        assert (directory / "result.json").is_file()
    else:
        with pytest.raises(ConfigurationError, match="empty"):
            run(directory, backend)
        assert backend.calls == []
        preserved = directory if existing == "file" else directory / "keep.txt"
        assert preserved.read_text(encoding="utf-8") in ("keep", "original")


@pytest.mark.parametrize("kwargs", [{"progress": "invalid"}, {"output_dir": 42}])
def test_bad_output_options_fail_before_backend_selection(tmp_path, monkeypatch, kwargs):
    def forbidden(*args, **kwargs):
        pytest.fail("must validate output options before backend or credentials")

    monkeypatch.setattr("attestextract.pipeline._selected_backend", forbidden)
    with pytest.raises(ConfigurationError):
        extract(SOURCE, model="deepseek:deepseek-flash", **TASK, **kwargs)


@pytest.mark.parametrize("kwargs", [{"progress": "normal"}, {"output_dir": "other"}])
def test_owned_output_options_reject_caller_trace(tmp_path, kwargs):
    backend = SyntheticBackend()
    with pytest.raises(ConfigurationError, match="caller-owned trace"):
        extract(SOURCE, backend=backend, trace=RunTrace(tmp_path / "caller"), **TASK, **kwargs)
    assert not backend.calls


def test_legacy_injected_backend_retains_memory_only_lifecycle(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    result = extract(SOURCE, backend=SyntheticBackend(), **TASK)
    assert len(result.candidates) == 1 and list(tmp_path.iterdir()) == []
    assert capsys.readouterr().err == ""
    trace = RunTrace(tmp_path / "caller")
    extract(SOURCE, backend=SyntheticBackend(), trace=trace, **TASK)
    assert not (trace.directory / "result.json").exists()
    assert read(trace.directory / "manifest.json")["status"] == "running"


@pytest.mark.parametrize("failure", [RuntimeError("synthetic failure"), KeyboardInterrupt()])
def test_failure_and_interrupt_keep_reserved_checkpoint_and_close_progress(tmp_path, monkeypatch, failure):
    closed = []
    original = ConsoleProgress.close

    def close(console):
        closed.append(console)
        original(console)

    monkeypatch.setattr(ConsoleProgress, "close", close)
    directory = tmp_path / "failed"
    with pytest.raises(type(failure)):
        run(directory, SyntheticBackend(failure=failure))
    assert len(closed) == 1 and closed[0]._stop.is_set()
    assert not (directory / "result.json").exists()
    assert read(directory / "diagnostics/manifest.json")["status"] == "failed"
    assert read(directory / "diagnostics/error.json")["error_type"] == type(failure).__name__
    assert read(directory / "diagnostics/execution/checkpoint.json")["state"]["calls_used"] == 1


def test_invalid_response_is_saved_and_not_presented_as_success(tmp_path):
    directory = tmp_path / "invalid"
    with pytest.raises(InvalidResponseError):
        run(directory, SyntheticBackend(response={"records": "not a list"}), progress="quiet")
    assert not (directory / "result.json").exists()
    assert read(directory / "diagnostics/responses/chunk-0001.txt") == {"records": "not a list"}
    assert read(directory / "diagnostics/manifest.json")["status"] == "failed"


def test_output_write_error_marks_trace_failed(tmp_path, monkeypatch):
    from attestextract import run_output

    def forbidden(*args, **kwargs):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(run_output, "render_markdown_report", forbidden)
    directory = tmp_path / "disk-failure"
    with pytest.raises(OSError):
        run(directory, progress="quiet")
    assert read(directory / "diagnostics/manifest.json")["status"] == "failed"
    assert (directory / "diagnostics/execution/checkpoint.json").is_file()


def test_midrun_user_report_is_preserved_and_never_marked_complete(tmp_path):
    directory = tmp_path / "collision"

    class Backend(SyntheticBackend):
        def generate(self, request):
            (directory / "report.md").write_text("User's report", encoding="utf-8")
            return super().generate(request)

    with pytest.raises(FileExistsError):
        run(directory, Backend(), progress="quiet")
    assert (directory / "report.md").read_text(encoding="utf-8") == "User's report"
    assert read(directory / "result.json")["candidates"]
    assert read(directory / "diagnostics/manifest.json")["status"] == "failed"
    assert read(directory / "diagnostics/error.json")["stage"] == "output"
    assert read(directory / "diagnostics/responses/chunk-0001.txt") == RESPONSE
    assert not (directory / "diagnostics/result.json").exists()


def test_completed_resume_reuses_cached_result_and_preserves_old_run(tmp_path):
    first = tmp_path / "first"
    original = run(first, progress="quiet")
    before = {str(p.relative_to(first)): p.read_bytes() for p in first.rglob("*") if p.is_file()}
    backend = SyntheticBackend(failure=AssertionError("completed source must not call model"))
    resumed = run(tmp_path / "second", backend, resume_from=first, progress="quiet")
    assert resumed.candidates == original.candidates and not backend.calls
    assert read(tmp_path / "second/diagnostics/execution/checkpoint.json")["state"]["calls_used"] == 1
    with pytest.raises(ConfigurationError):
        run(first / "nested", backend, resume_from=first, progress="quiet")
    assert not (first / "nested").exists()
    assert before == {str(p.relative_to(first)): p.read_bytes() for p in first.rglob("*") if p.is_file()}


def test_preset_prose_defaults_match_cli_without_execution_options(tmp_path, monkeypatch):
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(RESPONSE)},
                                                     "finish_reason": "stop"}]})

    monkeypatch.setattr("attestextract.backends.OpenAICompatibleBackend", lambda **kw:
                        OpenAICompatibleBackend(**kw, transport=httpx.MockTransport(respond)))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-only")
    directory = tmp_path / "python"
    extract(SOURCE, model="deepseek:deepseek-flash", output_dir=directory, progress="quiet", **TASK)
    paper = tmp_path / "paper.md"
    paper.write_text(SOURCE, encoding="utf-8")
    task = tmp_path / "task.json"
    task.write_text(json.dumps(TASK), encoding="utf-8")
    cli_dir = tmp_path / "cli"
    cli_dir.mkdir()
    assert cli.main(["extract", "--input", str(paper), "--task", str(task),
                     "--model-id", "deepseek:deepseek-flash", "--output", str(cli_dir / "result.json"),
                     "--report", str(cli_dir / "report.md"), "--log-dir", str(cli_dir / "diagnostics"),
                     "--progress", "quiet"]) == 0
    py_state = read(directory / "diagnostics/execution/checkpoint.json")["state"]
    cli_state = read(cli_dir / "diagnostics/execution/checkpoint.json")["state"]
    assert py_state["max_model_calls"] == cli_state["max_model_calls"] == 3
    assert py_state["calls_used"] == cli_state["calls_used"] == 1
    assert calls[0]["messages"] == calls[1]["messages"]
    # No output_dir is needed to enable the ordinary preset's ledger.
    trace = RunTrace(tmp_path / "caller-trace")
    extract(SOURCE, model="deepseek:deepseek-flash", trace=trace, **TASK)
    assert (trace.directory / "execution/checkpoint.json").is_file()


def test_explicit_progress_without_output_stays_in_memory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    extract(SOURCE, backend=SyntheticBackend(), progress="normal", **TASK)
    assert not list(tmp_path.iterdir())
    assert "Completed:" in capsys.readouterr().err


def test_model_key_is_redacted_from_saved_output_report_and_console(tmp_path, monkeypatch, capsys):
    key = "synthetic-key-only"
    source = SOURCE + " " + key
    response = deepcopy(RESPONSE)
    response["records"][0]["quotes"] = {name: source for name in ("entity", "value", "unit")}
    response["records"][0]["quotes"]["conditions"] = ""

    def respond(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)},
                                                     "finish_reason": "stop"}]})

    monkeypatch.setattr("attestextract.backends.OpenAICompatibleBackend", lambda **kw:
                        OpenAICompatibleBackend(**kw, transport=httpx.MockTransport(respond)))
    directory = tmp_path / key
    extract(source, model="deepseek:deepseek-flash", api_key=key, output_dir=directory, **TASK)
    for path in directory.rglob("*"):
        if path.is_file():
            saved = path.read_text(encoding="utf-8")
            assert key not in saved and key.replace("-", r"\-") not in saved
    assert key not in capsys.readouterr().err


def test_partial_table_result_is_saved_with_partial_manifest(tmp_path):
    table = '<table><tr><th>Sample</th><th>Mass</th></tr><tr><td>A</td><td>17</td></tr></table>'
    anchor = {"ref": "table-0001-r0001-c0001", "quote": "17", "offset": 0}
    record = {"record_id": "good", "record_type": "mass", "fields": {
        "entity": {"value": "A", "sources": [{"ref": "table-0001-r0001-c0000", "quote": "A", "offset": 0}], "relation": "row"},
        "value": {"value": "17", "sources": [anchor], "relation": "anchor"},
        "unit": {"value": None, "sources": [], "relation": None},
        "conditions": {"value": None, "sources": [], "relation": None},
    }, "anchors": [anchor], "scope": "individual", "scope_sources": []}
    broken = deepcopy(record)
    broken["record_id"] = "bad"
    broken["fields"]["value"]["value"] = {"wrong": "type"}
    result = extract(table, backend=SyntheticBackend({"records": [record, broken]}),
                     backend_identity="synthetic-table-v1", output_dir=tmp_path / "table", progress="quiet", **TASK)
    assert result.format_status == "partial_format_error"
    assert read(tmp_path / "table/result.json")["format_status"] == "partial_format_error"
    assert read(tmp_path / "table/diagnostics/manifest.json")["status"] == "partial_format_error"
    assert (tmp_path / "table/report.md").is_file()
