import json
from copy import deepcopy
from hashlib import sha256

import pytest

from attestextract import cli, extract
from attestextract.dataset_supplement import run_supplement
from attestextract.dataset_supplement_packs import review_packs
from attestextract.models import BackendResponse


SOURCE = ("East registers Line 2. West registers Line 2.\r\n\r\n" + "Background. " * 80
          + "\r\n\r\nWest daily bulletin.\r\n\r\nLine 2 packed 50 boxes.")


def span(text):
    pos = SOURCE.rindex(text)
    return {"start": pos, "end": pos + len(text), "quote": text}


def fixture():
    objects = [{"object_id": plant, "label": "Line 2", "qualifier": plant, "scope": plant,
                "evidence": [span(f"{plant} registers Line 2.")]} for plant in ("East", "West")]
    obs = {"observation_id": "obs", "block_id": "block", "value_source": span("50"), "entity": "Line 2",
           "field": "packed", "value": "50", "unit": "boxes", "purpose": "measurement",
           "local_mention": span("Line 2"), "evidence": [span("Line 2 packed 50 boxes.")], "row_ids": [],
           "direct": {"status": "supported", "id": None, "label": "Line 2", "evidence": [span("Line 2")]},
           "fit": {"status": "supported"}, "target": {"status": "unknown"},
           "candidates": [{"id": o["object_id"], "label": o["label"]} for o in objects]}
    return {"subject_strategy": "entity-first", "protocol_version": "entity-first-v3",
            "source_sha256": sha256(SOURCE.encode()).hexdigest(), "document_id": "doc",
            "field_definitions": {"packed": "Packed boxes at one line"}, "coverage_gaps": [],
            "row_subject": "named line within one plant", "observations": [obs], "lineage": {"objects": objects}}


class Backend:
    name = "synthetic"

    def __init__(self, with_title=True):
        self.calls = 0
        self.with_title = with_title

    def generate(self, request):
        self.calls += 1
        packet = json.loads(request.messages[-1]["content"])
        item, = packet["items"]
        assert packet["protocol_version"] == "entity-first-v3" and item["axes"] == ["target"]
        assert item["target_ids"] == ["East", "West"]
        assert item["prior"]["direct"]["label"] == "Line 2"
        assert any("West daily bulletin." in w["quote"] for w in packet["windows"])
        original = "Line 2 packed 50 boxes."
        context = "West daily bulletin.\r\n\r\n" + original if self.with_title else original
        response = {"item_id": "obs", "axis": "target", "status": "supported", "reason": "West heading",
                    "endpoint_id": "West", "predicate": "same_as",
                    "applicability": {"description": "West daily bulletin", "source": span(context)},
                    "evidence": [span(context), span("West registers Line 2.")]}
        return BackendResponse(text=json.dumps({"decisions": [response]}), usage={"total_tokens": 18})


def test_continued_block_reread_only_when_requested_and_retains_frozen_view(tmp_path):
    data = fixture()
    before = deepcopy(data)
    ledger = tmp_path / "audit.sqlite"
    backend = Backend()
    pending = run_supplement(data, SOURCE, ledger_path=ledger, max_calls=0)
    assert pending["attempts_used"] == 0 and len(pending["pending"]) == 1
    opts = {"ledger_path": ledger, "max_calls": 1, "backend": backend}
    review = run_supplement(data, SOURCE, **opts)
    assert review["pending"] == [] and review["failures"] == []
    decision, = review["decisions"]
    assert decision["endpoint_id"] == "West" and decision["observation_value"] == "50"
    assert decision["source_subject"] == "Line 2" and decision["candidate_ids"] == ["East", "West"]
    assert run_supplement(data, SOURCE, **opts) == review and backend.calls == 1
    assert data == before


def test_ambiguous_review_charges_credit_and_requires_additional_budget_to_retry(tmp_path):
    data, ledger, backend = fixture(), tmp_path / "audit.sqlite", Backend(with_title=False)
    args = {"ledger_path": ledger, "backend": backend}
    first = run_supplement(data, SOURCE, max_calls=1, **args)
    assert first["failures"][0]["reason"] == "target_scope_still_ambiguous" and not first["decisions"]
    backend.with_title = True
    assert run_supplement(data, SOURCE, max_calls=1, **args)["pending"] and backend.calls == 1
    final = run_supplement(data, SOURCE, max_calls=2, **args)
    assert final["attempts_used"] == 2 and final["usage"] == {"total_tokens": 36}
    assert final["decisions"][0]["endpoint_id"] == "West"


def test_complete_or_inapplicable_facts_do_not_consume_review_budget(tmp_path):
    data = fixture()
    complete = data["observations"][0]
    blocked = deepcopy(complete)
    complete["row_ids"] = ["row"]
    blocked["fit"]["status"] = "contradicted"
    data["observations"].append(blocked)
    backend = Backend()
    report = run_supplement(data, SOURCE, ledger_path=tmp_path / "review.sqlite", max_calls=1, backend=backend)
    assert report["pending"] == [] and report["attempts_used"] == backend.calls == 0
    del data["protocol_version"]
    with pytest.raises(ValueError, match="two-pass"):
        run_supplement(data, SOURCE, ledger_path=tmp_path / "old.sqlite", max_calls=0)


def test_cli_reread_finds_alias_first_registered_in_a_later_block(tmp_path, capsys):
    observation = "B1 load 60."
    definition = "Unit Blue is called B1."
    text = observation + "\n\n" + definition
    source = tmp_path / "source.md"
    source.write_bytes(text.encode())
    run = tmp_path / "run"
    stages = []

    class Replies:
        name = "synthetic"
        mode = "replay"

        def generate(self, request):
            packet = json.loads(request.messages[-1]["content"])
            stages.append(packet["stage"])
            window = packet["windows"][0]
            def cite(quote):
                return {"window": window["id"], "quote": quote}
            if packet["stage"] == "inventory":
                reply = {"objects": []}
            elif observation in window["text"]:
                reply = {"nominations": [], "facts": [{
                    "field": "load", "value": "60", "value_source": cite("60"),
                    "property_source": cite("load"), "source_subject": "B1", "subject_source": cite("B1"),
                    "target_id": None, "link_source": cite(observation), "fit_source": cite(observation),
                    "fit": "supported", "purpose": "measurement", "reason": "Reported load."}]}
            else:
                reply = {"nominations": [{"key": "b", "label": "Unit Blue", "mention": cite("Unit Blue"),
                          "qualification": cite(definition), "aliases": [cite(definition)], "alias_names": ["B1"]}],
                         "facts": []}
            return BackendResponse(text=json.dumps(reply), model=self.name)

    extract(text, prompt_description="Read unit loads", field_list={"unit": "Unit name", "load": "Load"},
            table_subject="named units", subject_strategy="entity-first", backend=Replies(), output_dir=run,
            dataset_options={"subject_label_field": "unit"}, max_chunk_chars=32,
            progress="quiet")
    assert set(stages) == {"inventory", "attributes"}
    data = json.loads((run / "dataset/dataset.json").read_bytes())
    obj, = data["lineage"]["objects"]
    obs, = data["observations"]
    assert not obs["row_ids"] and not obs["candidates"] and obs["direct"]["status"] == "supported"
    packet, = review_packs(data, text)
    item, = packet["items"]
    assert item["target_ids"] == [obj["object_id"]] and item["axes"] == ["target"]
    assert definition in [s["quote"] for s in item["endpoints"][obj["object_id"]]["mentions"]]
    capsys.readouterr()
    assert cli.main(["supplement-dataset", "--input", str(source), "--run-dir", str(run),
                     "--ledger", str(tmp_path / "review.sqlite"), "--max-calls", "0"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["pending"]) == 1 and report["attempts_used"] == 0
