import json

import pytest

from attestextract import assemble_from_files, cli, extract
from attestextract.errors import ConfigurationError
from attestextract.models import BackendResponse, ExecutionOptions
from attestextract.registry import BackendRegistry


TEXT = "Specimen A has mass 40 g."
TASK = {"prompt_description": "Recover the described mass", "field_list": {
    "specimen": "Name of individual specimen", "mass": "Measured mass"}}
OPTIONS = {"subject_label_field": "specimen", "field_policies": {"mass": {"allowed_units": ["g"]}}}


class Backend:
    name = "synthetic"
    mode = "replay"

    def __init__(self):
        self.stages = []

    def generate(self, request):
        packet = json.loads(request.messages[-1]["content"])
        self.stages.append(stage := packet["stage"])
        window = packet["windows"][0]["id"]

        def cite(quote):
            return {"window": window, "quote": quote}

        if stage == "inventory":
            data = {"objects": [{"label": "Specimen A", "key": "a", "mention": cite("Specimen A"),
                                 "qualification": cite(TEXT)}]}
        elif stage == "attributes":
            owner, = packet["objects"]
            data = {"nominations": [], "facts": [{"field": "mass", "value": "40", "value_source": cite("40"),
                    "unit": "g", "property_source": cite("mass"), "owner": owner["object_id"],
                    "owner_source": cite("Specimen A"), "link_source": cite(TEXT), "fit_source": cite(TEXT),
                    "fit": "supported", "purpose": "measurement", "direct": True, "reason": "Measured mass."}]}
        else:
            pytest.fail(f"unexpected stage {stage}")
        return BackendResponse(text=json.dumps(data), usage={"total_tokens": 5}, model="synthetic")


def arguments(tmp_path, flags=()):
    source = tmp_path / "source.md"
    source.write_bytes(TEXT.encode())
    task = tmp_path / "task.json"
    task.write_text(json.dumps(TASK))
    options = tmp_path / "options.json"
    options.write_text(json.dumps(OPTIONS))
    return ["extract", "--input", str(source), "--task", str(task), "--table-subject", "individual specimens",
            "--dataset-config", str(options), "--subject-strategy", "entity-first", "--format-retries", "1",
            "--max-analysis-calls", "48", "--output-dir", str(tmp_path / "run"), "--progress", "quiet", *flags]


def test_cli_entity_first_output_and_offline_rebuild(tmp_path):
    registry = BackendRegistry()
    backend = Backend()
    registry.register("synthetic", lambda **_: backend)
    args = arguments(tmp_path, ["--base-url", "http://localhost:1444/v1", "--model", "demo",
                                "--adapter", "synthetic", "--no-entity-rescue"])
    assert cli.main(args, registry=registry) == 0
    assert backend.stages == ["inventory", "attributes"]
    run = tmp_path / "run"
    report = json.loads((run / "entity-first.json").read_bytes())
    assert report["rescue"] is False and report["budget"]["limit"] == 48
    data = json.loads((run / "dataset/dataset.json").read_bytes())
    assert data["rows"] == [{"specimen": "Specimen A", "mass": "40"}]
    assert assemble_from_files(tmp_path / "source.md", run) == data
    assert (run / "dataset/lineage.json").is_file()
    assert json.loads((run / "result.json").read_bytes())["entity_first"] == report
    assert not (run / "analysis").exists()


def test_check_needs_no_secret_and_prioritizes_explicit_total(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    args = arguments(tmp_path, ["--model-id", "deepseek:deepseek-flash", "--max-model-calls", "4", "--check"])
    assert cli.main(args) == 0
    plan = json.loads(capsys.readouterr().out)
    assert (plan["subject_strategy"], plan["max_model_calls"], plan["network_calls"]) == ("entity-first", 4, 0)


def test_api_partial_provenance_and_preflight(tmp_path):
    backend = Backend()
    with pytest.raises(ConfigurationError, match="table_subject"):
        extract(TEXT, **TASK, backend=backend, subject_strategy="entity-first")
    assert not backend.stages
    result = extract(TEXT, **TASK, backend=backend, subject_strategy="entity-first", table_subject="specimens",
                     execution=ExecutionOptions(max_model_calls=1), output_dir=tmp_path / "run", progress="quiet")
    assert result.entity_first["status"] == "partial"
    assert result.entity_first["budget"]["calls_used"] == 1
    assert json.loads((tmp_path / "run/diagnostics/manifest.json").read_bytes())["status"] == "entity_first_incomplete"


def test_cli_reports_partial_as_nonzero(tmp_path):
    registry = BackendRegistry()
    registry.register("synthetic", lambda **_: Backend())
    args = arguments(tmp_path, ["--adapter", "synthetic", "--base-url", "http://localhost:1444/v1",
                                "--model", "demo", "--max-model-calls", "1"])
    assert cli.main(args, registry=registry) == 2

def test_separate_blocks_do_not_join_attributes_for_the_same_owner():
    from copy import deepcopy
    from attestextract.entity_first_output import dataset_view

    result = extract(TEXT, **TASK, backend=Backend(), table_subject="specimens", subject_strategy="entity-first",
                     dataset_options=OPTIONS)
    report = deepcopy(result.entity_first)
    task = result.task.model_copy(deep=True)
    task.input_materials.field_list["temperature"] = "Measured temperature"
    fact = deepcopy(report["observations"][0])
    fact.update(field="temperature", value="20", unit="K", block_id="other",
                observation_id="second", reason="Independent run")
    report["observations"].append(fact)
    rows = dataset_view(report, task, result.source, OPTIONS)["rows"]
    assert [(row["mass"], row["temperature"]) for row in rows] == [("40", None), (None, "20")]


@pytest.mark.parametrize("target", ["source", "report", "candidate"])
def test_frozen_projection_detects_drift(tmp_path, target):
    run = tmp_path / "run"
    source = tmp_path / "source.md"
    source.write_bytes(TEXT.encode())
    extract(TEXT, **TASK, backend=Backend(), table_subject="specimens", subject_strategy="entity-first",
            dataset_options=OPTIONS, output_dir=run, progress="quiet")
    payload = json.loads((run / "result.json").read_bytes())
    if target == "candidate":
        payload["candidates"][0]["fields"]["value"]["value"] = "4"
    elif target == "report":
        payload["entity_first"]["observations"][0]["qualified"] = False
    else:
        source.write_bytes((TEXT + " ").encode())
    (run / "result.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="differs"):
        assemble_from_files(source, run)
