import json

import pytest

from niceextract import extract, load_task
from niceextract.cli import main
from niceextract.errors import ConfigurationError, TaskValidationError
from niceextract.field_planning import prepare_types
from niceextract.models import BackendResponse, ExecutionOptions
from niceextract.task_input import resolve_task


TASK = {"prompt_description": "Collect a grade and the concentration of each sample.",
        "field_list": {"grade": "Material grade as an identifier", "amount": "Sample concentration"},
        "field_types": {"grade": {"kind": "text"}}}


class PlanningBackend:
    name = "field-planning-test"
    mode = "live"
    output_protocol = "flat-v1"

    def __init__(self, plan=None):
        self.plan = {"amount": {"kind": "numeric", "shapes": ["scalar", "range", "bound"]}} if plan is None else plan
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        answer = {"field_types": self.plan} if request.chunk_id == "task-field-types" else {"records": []}
        return BackendResponse(text=json.dumps(answer), finish_reason="stop")


def test_inline_plan_and_resume_use_one_reservation_and_no_source_in_planning(tmp_path):
    backend = PlanningBackend()
    first, next_run = tmp_path / "first", tmp_path / "second"
    options = dict(task=TASK, backend=backend, backend_identity="test", prepare_field_types=True,
                   execution=ExecutionOptions(max_model_calls=2), progress="quiet")
    document = "Sample Q had a nominal concentration of 5 ppm."
    run = extract(document, output_dir=first, **options)
    assert [r.chunk_id for r in backend.requests] == ["task-field-types", "chunk-0001"]
    assert document not in str(backend.requests[0].messages)
    assert json.loads(backend.requests[0].messages[-1]["content"])["fields"] == {"amount": "Sample concentration"}
    assert run.task.field_types["grade"].source == "user" and run.task.field_types["amount"].source == "model"
    assert json.loads(backend.requests[-1].messages[-1]["content"])["task"]["field_types"] == run.task.model_dump()["field_types"]
    extract(document, output_dir=next_run, resume_from=first, **options)
    assert len(backend.requests) == 2
    for run_dir in [first, next_run]:
        diagnostics = run_dir / "diagnostics"
        assert json.loads((diagnostics / "execution/summary.json").read_bytes())["max_model_calls"] == 1
        assert json.loads((diagnostics / "field-preparation.json").read_bytes())["calls_reserved"] == 1
    with pytest.raises(ConfigurationError, match="total call budget"):
        extract(document, task=TASK, backend=backend, prepare_field_types=True, execution=ExecutionOptions(max_model_calls=1))
    assert len(backend.requests) == 2


@pytest.mark.parametrize("answer", [{}, {"amount": {"kind": "bogus"}}, {"amount": {"kind": "numeric"},
                                                                       "grade": {"kind": "numeric"}}])
def test_bad_plan_and_user_reclassification_are_rejected(answer):
    with pytest.raises(TaskValidationError, match="invalid or incomplete"):
        prepare_types(resolve_task(TASK), PlanningBackend(answer))


def test_prepare_cli_reuses_plan_with_unknown_and_can_run_fully_offline(tmp_path, monkeypatch):
    backend = PlanningBackend({"amount": {"kind": "unknown"}})

    class StubRegistry:
        def create(self, *args, **kwargs):
            return backend

    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    source = tmp_path / "input.json"
    source.write_text(json.dumps(TASK), encoding="utf-8")
    out = tmp_path / "plan"
    assert main(["prepare-task", "--task", str(source), "--model-id", "deepseek:deepseek-flash",
                 "--output-dir", str(out)], registry=StubRegistry()) == 0
    saved = load_task(out / "task.json")
    assert saved.field_types["amount"].kind == "unknown"
    assert saved.field_types["grade"].source == "user"
    assert (out/"diagnostics/responses/task-field-types.txt").is_file()
    second = tmp_path / "reused"
    assert main(["prepare-task", "--task", str(out/"task.json"), "--output-dir", str(second)]) == 0
    assert load_task(second / "task.json") == saved
    assert json.loads((second / "preparation.json").read_bytes())["calls_reserved"] == 0


def test_prepare_cli_masks_current_and_legacy_secrets_in_diagnostics_and_errors(tmp_path, monkeypatch, capsys):
    from niceextract.errors import BackendError

    current, legacy = "synthetic-current-field", "synthetic-legacy-field"
    monkeypatch.setenv("NICEEXTRACT_API_KEY", current)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-provider-field")
    monkeypatch.delenv("ATTESTEXTRACT_API_KEY", raising=False)
    dotenv = tmp_path / "config.env"
    dotenv.write_text(f"ATTESTEXTRACT_API_KEY={legacy}\n", encoding="utf-8")
    source = tmp_path / "task.json"
    source.write_text(json.dumps(TASK), encoding="utf-8")

    class FailingRegistry:
        def create(self, *args, **kwargs):
            raise BackendError(f"synthetic diagnostic: {current}, {legacy}")

    out = tmp_path / "plan"
    status = main(["prepare-task", "--task", str(source), "--model-id", "deepseek:deepseek-flash",
                   "--env-file", str(dotenv), "--output-dir", str(out)], registry=FailingRegistry())
    assert status == 1
    captures = capsys.readouterr()
    error = (out / "diagnostics/error.json").read_text(encoding="utf-8")
    assert "[REDACTED]" in error and "[REDACTED]" in captures.err
    transcript = captures.out + captures.err + "".join(p.read_text(encoding="utf-8") for p in out.rglob("*") if p.is_file())
    assert current not in transcript and legacy not in transcript
