import json
import csv

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
                    "unit": "g", "property_source": cite("mass"), "target_id": owner["object_id"],
                    "source_subject": "Specimen A", "subject_source": cite("Specimen A"),
                    "link_source": cite(TEXT), "fit_source": cite(TEXT),
                    "fit": "supported", "purpose": "measurement", "reason": "Measured mass."}]}
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
    assert data["protocol_version"] == "entity-first-v3" and "strict_rows" not in data["admission"]
    assert data["proposals"][0]["disposition"] == "in_main"
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


def test_two_pass_files_deliver_only_source_backed_attribute_rows(tmp_path):
    from test_entity_first_engine import Backend as Replies, card, cite, fact

    text = ("Line A was listed with Line C. Line A load 80. Line B load 60. "
            "Line A and Line B combined load 140.")
    run = tmp_path / "run"
    path = tmp_path / "source.md"
    path.write_text(text, encoding="utf-8")

    def reply(packet):
        w = packet["windows"][0]
        if packet["stage"] == "inventory":
            return {"objects": [card(w, n) for n in ("Line A", "Line C")]}
        ids = {c["label"]: c["object_id"] for c in packet["objects"]}
        def reading(name, value, sentence, index, selected=None, **updates):
            f = fact(w, selected, name, sentence, value)
            f.update(subject_source=cite(w, name), property_source=cite(w, "load", index), **updates)
            return f
        return {"nominations": [card(w, "Line B", proof="Line B load 60.")], "facts": [
            reading("Line A", "80", "Line A load 80.", 0, ids["Line A"]),
            reading("Line B", "60", "Line B load 60.", 1, nomination_key="Line B"),
            reading("Line A and Line B", "140", "Line A and Line B combined load 140.", 2,
                    members=["Line A", "Line B"], purpose="aggregate"),
            reading("Line A", "8", "Line A load 80.", 0, ids["Line A"])]}

    result = extract(text, prompt_description="Individual line load.", field_list={"name": "Line name", "load": "Individual load"},
                     table_subject="individual production lines", subject_strategy="entity-first",
                     dataset_options={"subject_label_field": "name"}, backend=Replies(reply), output_dir=run, progress="quiet")
    assert result.run.output_protocol == "entity-first-v3"
    assert result.dataset["rows"] == [{"name": "Line A", "load": "80"}, {"name": "Line B", "load": "60"}]
    with (run / "dataset/roster.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert [(r["label"], r["main_rows"], r["stage"]) for r in csv.DictReader(stream)] == [
            ("Line A", "1", "inventory"), ("Line C", "0", "inventory"), ("Line B", "1", "rescue")]
    held = json.loads((run / "dataset/annotation.json").read_bytes())["review_proposals"]
    assert [(h["source_subject"], h["value"]) for h in held] == [("Line A and Line B", "140"), ("Line A", "8")]
    assert result.candidates[-1].fields["value"].evidence.status == "missing"
    assert assemble_from_files(path, run, audit_admission=True) == result.dataset


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


def test_lookup_by_roster_name_and_source_alias_returns_same_reading():
    from attestextract.dataset import query_dataset
    from test_entity_first_engine import Backend as Replies, card, cite, fact

    definition = "Station Delta (D1) is a monitoring station."
    source = definition + " D1 load 40."
    def reply(packet):
        w = packet["windows"][0]
        if packet["stage"] == "inventory":
            obj = card(w, "Station Delta", proof=definition)
            return {"objects": [{**obj, "aliases": [cite(w, definition)], "alias_names": ["D1"]}]}
        owner, = packet["objects"]
        f = fact(w, owner["object_id"], "D1", "D1 load 40.", "40")
        return {"nominations": [], "facts": [{**f, "subject_source": cite(w, "D1", 1)}]}

    result = extract(source, prompt_description="Measured station load.", field_list={"load": "Station load"},
                     backend=Replies(reply), table_subject="named stations", subject_strategy="entity-first")
    for name in ("Station Delta", "D1"):
        assert [o["value"] for o in query_dataset(result.dataset, name)] == ["40"]


def test_cli_reports_partial_as_nonzero(tmp_path):
    registry = BackendRegistry()
    registry.register("synthetic", lambda **_: Backend())
    args = arguments(tmp_path, ["--adapter", "synthetic", "--base-url", "http://localhost:1444/v1",
                                "--model", "demo", "--max-model-calls", "1"])
    assert cli.main(args, registry=registry) == 2

def test_separate_blocks_do_not_join_attributes_for_the_same_owner():
    second = "Specimen A has temperature 20 K."

    class TwoReadings(Backend):
        def generate(self, request):
            packet = json.loads(request.messages[-1]["content"])
            window = packet["windows"][0]
            if packet["stage"] == "inventory" or second not in window["text"]:
                return super().generate(request)
            owner, = packet["objects"]
            def cite(quote):
                return {"window": window["id"], "quote": quote}
            data = {"nominations": [], "facts": [{
                "field": "temperature", "value": "20", "unit": "K", "value_source": cite("20"),
                "property_source": cite("temperature"), "target_id": owner["object_id"],
                "source_subject": "Specimen A", "subject_source": cite("Specimen A"),
                "link_source": cite(second), "fit_source": cite(second), "fit": "supported",
                "purpose": "measurement", "reason": "Independent temperature measurement."}]}
            return BackendResponse(text=json.dumps(data), model="synthetic")

    result = extract("# First measurement\n" + TEXT + "\n\n# Second measurement\n" + second,
                     prompt_description=TASK["prompt_description"],
                     field_list={**TASK["field_list"], "temperature": "Measured temperature"},
                     backend=TwoReadings(), table_subject="specimens", subject_strategy="entity-first",
                     dataset_options=OPTIONS)
    rows = result.dataset["rows"]
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
