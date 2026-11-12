"""Public simple-task workflows with synthetic evidence and no paid calls."""

from copy import deepcopy
import json
import warnings

import httpx
import pytest

from attestextract import cli, extract, ExecutionOptions, load_task, RunTrace
from attestextract.backends import ReplayBackend, OpenAICompatibleBackend
from attestextract.errors import ConfigurationError, TaskValidationError
from attestextract.models import BackendResponse
from attestextract.registry import BackendRegistry
from attestextract.reporting import render_markdown_report
from attestextract.task_input import resolve_task, TaskInputWarning


SOURCE = "Sample A has mass 17 g."
TASK = {
    "prompt_description": "Extract explicitly reported sample properties with conditions.",
    "field_list": {"mass": "Reported sample mass", "length": "Reported sample length"},
    "examples": [{"text": "Sample B has mass 2 g.", "extractions": [
        {"field": "mass", "entity": "Sample B", "value": "2", "unit": "g",
         "quote": "Sample B has mass 2 g."}]}],
}
RESPONSE = {"records": [{"record_type": "mass",
    "values": {"entity": "Sample A", "value": "17", "unit": "g", "conditions": None},
    "quotes": {"entity": SOURCE, "value": SOURCE, "unit": SOURCE, "conditions": ""}}]}


@pytest.fixture(autouse=True)
def no_network_or_ambient_model(monkeypatch, tmp_path):
    original_send = httpx.Client.send

    def mock_only(client, *args, **kwargs):
        if not isinstance(client._transport, httpx.MockTransport):
            pytest.fail("unexpected real network")
        return original_send(client, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", mock_only)
    for key in ("ATTESTEXTRACT_MODEL", "ATTESTEXTRACT_BASE_URL", "ATTESTEXTRACT_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ATTESTEXTRACT_MODEL_CACHE_DIR", str(tmp_path / "model-cache"))


def test_python_keywords_and_task_file_agree(tmp_path):
    task_file = tmp_path / "task.json"
    task_file.write_text(json.dumps(TASK), encoding="utf-8")
    result = extract(SOURCE, backend=ReplayBackend([RESPONSE]), **TASK)
    from_file = extract(SOURCE, load_task(task_file), backend=ReplayBackend([RESPONSE]))
    assert result.task == from_file.task
    assert list(result.task.record_types) == ["mass", "length"]
    assert [(c.record_type, c.fields["value"].value) for c in result.candidates] == [("mass", "17")]
    assert result.candidates == from_file.candidates
    assert result.task.input_materials.examples[0].extractions[0].value == "2"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_response_value_retained_but_not_counted_as_valued_observation(value):
    response = deepcopy(RESPONSE)
    response["records"][0]["values"]["value"] = value
    response["records"][0]["quotes"]["value"] = ""
    result = extract(SOURCE, backend=ReplayBackend([response]), **TASK)
    candidate = result.candidates[0]
    assert candidate.fields["value"].value == value
    assert candidate.field_status["value"] == "required_missing"
    assert candidate.needs_review
    report = render_markdown_report(result)
    assert "| mass | Reported sample mass | 0 | 1 |" in report
    assert "| length | Reported sample length | 0 | 0 |" in report
    assert "零条候选也不能证明原文没有报告" in report


def test_bad_examples_are_rejected_before_backend_selection(monkeypatch):
    import attestextract.pipeline as pipeline
    monkeypatch.setattr(pipeline, "_selected_backend", lambda *a, **k: pytest.fail("backend configured"))
    bad = deepcopy(TASK)
    bad["examples"][0]["extractions"][0]["quote"] = "Not in the example."
    with pytest.raises(TaskValidationError, match=r"examples\[0\].extractions\[0\].quote"):
        extract(SOURCE, model="unregistered", base_url="https://synthetic.invalid/v1", **bad)
    with pytest.raises(TaskValidationError, match="not both"):
        extract(SOURCE, resolve_task(TASK), model="unregistered", **TASK)


def cli_setup(tmp_path, *, task=TASK, source=SOURCE):
    path = tmp_path / "inputs"
    path.mkdir()
    (path / "source.md").write_text(source, encoding="utf-8")
    (tmp_path / "task.json").write_text(json.dumps(task), encoding="utf-8")
    calls = []
    registry = BackendRegistry()

    class Backend:
        name = "synthetic-simple"
        mode = "replay"
        output_protocol = "flat-v1"

        def generate(self, request):
            calls.append(request)
            return BackendResponse(text=json.dumps(RESPONSE), model="synthetic")

    registry.register("synthetic", lambda **_: Backend())
    args = ["extract", "--input", str(path / "source.md"), "--task", str(tmp_path / "task.json"),
            "--base-url", "https://synthetic.invalid/v1", "--model", "synthetic", "--adapter", "synthetic"]
    return args, registry, calls


def test_cli_minimal_task_outputs_scope_snapshot_and_report(tmp_path, capsys):
    args, registry, calls = cli_setup(tmp_path)
    assert cli.main(args, registry=registry) == 0
    run = next((tmp_path / "outputs").iterdir())
    task = json.loads((run / "diagnostics/task.json").read_text(encoding="utf-8"))
    assert task["input_materials"]["field_list"] == TASK["field_list"]
    assert task["input_materials"]["examples"][0]["text"] == TASK["examples"][0]["text"]
    assert len(calls) == 1
    assert "| length | Reported sample length | 0 | 0 |" in (run / "report.md").read_text(encoding="utf-8")
    terminal = capsys.readouterr().err
    assert terminal.count("Task: 2 targets; 1 examples") == 1
    assert "Sample B" not in terminal


def test_cli_check_is_optional_network_free_and_keeps_all_targets(tmp_path, capsys):
    args, registry, calls = cli_setup(tmp_path)
    assert cli.main([*args, "--check"], registry=registry) == 0
    captured = capsys.readouterr()
    plan = json.loads(captured.out)
    assert plan["task"]["record_types"] == ["mass", "length"]
    assert plan["task"]["example_count"] == plan["task"]["example_extraction_count"] == 1
    assert plan["network_calls"] == 0 and calls == []
    assert not list((tmp_path / "outputs").glob("*/result.json"))


def test_cli_invalid_task_precedes_hidden_key_prompt_and_configuration(tmp_path, monkeypatch, capsys):
    bad = deepcopy(TASK)
    bad["field_list"]["mass"] = ""
    args, _, _ = cli_setup(tmp_path, task=bad)
    monkeypatch.setattr(cli.getpass, "getpass", lambda *a: pytest.fail("asked for credentials"))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "build_automatic_backend", lambda *a, **k: pytest.fail("backend prepared"))
    assert cli.main(args[:-2]) == 1  # raw model/address automatic entry
    assert "field_list.mass" in capsys.readouterr().err


def test_normalized_example_warns_once_across_load_and_extract(tmp_path):
    task = deepcopy(TASK)
    task["examples"][0]["extractions"][0]["quote"] = "Sample  B has mass 2 g."
    path = tmp_path / "task.json"
    path.write_text(json.dumps(task), encoding="utf-8")
    with warnings.catch_warnings(record=True) as notices:
        warnings.simplefilter("always")
        compiled = load_task(path)
        extract(SOURCE, compiled, backend=ReplayBackend([RESPONSE]))
    assert len([n for n in notices if issubclass(n.category, TaskInputWarning)]) == 1


def initial_run(tmp_path):
    trace = RunTrace(tmp_path / "first")
    result = extract(SOURCE, backend=ReplayBackend([RESPONSE]), trace=trace,
                     execution=ExecutionOptions(max_model_calls=1), backend_identity="synthetic-replay-v1", **TASK)
    trace.finish(result.model_dump(mode="json"))
    return trace


def test_resume_reuses_complete_simple_task_without_new_generation(tmp_path):
    first = initial_run(tmp_path)
    result = extract(SOURCE, backend=ReplayBackend([]), trace=RunTrace(tmp_path / "second"),
                     resume_from=first.directory, execution=ExecutionOptions(max_model_calls=1),
                     backend_identity="synthetic-replay-v1", **TASK)
    assert result.candidates[0].fields["value"].value == "17"
    checkpoint = json.loads((tmp_path / "second/execution/checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["state"]["calls_used"] == 1


@pytest.mark.parametrize("change", ["definition", "example", "description"])
def test_changed_materials_reject_resume_before_generation(tmp_path, change):
    first = initial_run(tmp_path)
    changed = deepcopy(TASK)
    if change == "definition":
        changed["field_list"]["mass"] += " at room temperature"
    elif change == "description":
        changed["prompt_description"] += " Include model parameters."
    else:
        changed["examples"][0]["extractions"][0]["entity"] = "B"
    with pytest.raises(ConfigurationError, match="identity mismatch"):
        extract(SOURCE, backend=ReplayBackend([]), trace=RunTrace(tmp_path / "changed"),
                resume_from=first.directory, execution=ExecutionOptions(max_model_calls=1),
                backend_identity="synthetic-replay-v1", **changed)


def test_transport_change_reuses_simple_task_cache_with_preset(tmp_path, monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(RESPONSE)},
                                                     "finish_reason": "stop"}]})

    monkeypatch.setattr("attestextract.backends.OpenAICompatibleBackend", lambda **kw:
                        OpenAICompatibleBackend(**kw, transport=httpx.MockTransport(respond)))
    options = {"model": "deepseek:deepseek-flash", "api_key": "synthetic-key",
               "execution": ExecutionOptions(max_model_calls=1)}
    trace = RunTrace(tmp_path / "first")
    first = extract(SOURCE, trace=trace, model_options={"read_timeout": 60}, **options, **TASK)
    trace.finish(first.model_dump(mode="json"))
    resumed = extract(SOURCE, trace=RunTrace(tmp_path / "second"), resume_from=trace.directory,
                      model_options={"read_timeout": 240}, **options, **TASK)
    assert len(calls) == 1 and resumed.candidates == first.candidates


def test_unregistered_automatic_service_uses_simple_task_and_bound_resume(tmp_path, monkeypatch):
    calls = []
    original_client = httpx.Client

    def respond(request):
        if request.method == "GET":
            assert "Authorization" not in request.headers
            return httpx.Response(401)
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer synthetic-only"
        payload = json.loads(request.content)
        assert payload["model"] == "unregistered-synthetic-model"
        assert payload["response_format"]["type"] == "json_schema"
        assert set(json.loads(payload["messages"][-1]["content"])["task"]["record_types"]) == {"mass", "length"}
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(RESPONSE)},
                                                     "finish_reason": "stop"}]})

    def client(**kwargs):
        kwargs["transport"] = httpx.MockTransport(respond)
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    options = {"model": "unregistered-synthetic-model", "base_url": "https://unknown.synthetic.invalid/v1",
               "api_key": "synthetic-only", "execution": ExecutionOptions(max_model_calls=1)}
    trace = RunTrace(tmp_path / "first")
    result = extract(SOURCE, trace=trace, **TASK, **options)
    trace.finish(result.model_dump(mode="json"))
    assert result.schema_version == "0.5" and len(result.run.generation_attempts) == 1
    reused = extract(SOURCE, trace=RunTrace(tmp_path / "second"), resume_from=trace.directory, **TASK, **options)
    assert reused.candidates == result.candidates and len(calls) == 1
    changed = deepcopy(TASK)
    changed["field_list"]["mass"] = "Dry sample mass"
    with pytest.raises(ConfigurationError, match="identity mismatch"):
        extract(SOURCE, trace=RunTrace(tmp_path / "third"), resume_from=trace.directory, **changed, **options)
    assert len(calls) == 1
