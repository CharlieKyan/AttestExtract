import json

import pytest

from attestextract import cli, save_dataset
from attestextract.jev_review import review_dataset
from test_dataset_admission import assemble


SOURCE = "S2: mean length = 30 cm."


class LocalScorer:
    identity = "synthetic-checkpoint"

    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def predict(self, request):
        self.calls += 1
        choices = request["questions"]["disposition"]["criteria"]
        if self.fail:
            raise ValueError("synthetic invalid request")
        p = {key: float(key == "review") for key in choices}
        return {"model": "Intern-Decision-4B", "answers": {"disposition": {
            "type": "choice", "choice": "review", "decision": "review",
            "confidence": 1.0, "probabilities": p}}}


def frozen(tmp_path):
    source = tmp_path / "source.md"
    source.write_bytes(SOURCE.encode())
    directory = tmp_path / "original"
    save_dataset(assemble(SOURCE, SOURCE, [("S2", "S2")], "S2",
                          definition="Usable length of the specimen.", property_name="mean length"), directory)
    return source, directory


def test_ordinary_cli_and_resume_use_original_dataset(tmp_path, monkeypatch):
    source, baseline = frozen(tmp_path)
    before = {p.name: p.read_bytes() for p in baseline.iterdir()}
    model = LocalScorer()
    monkeypatch.setattr("attestextract.jev_backend.LocalJev", lambda *a, **kw: model)
    ledger = tmp_path / "shared.sqlite"
    args = ["review-dataset", "--input", str(source), "--dataset", str(baseline), "--checkpoint", "synthetic",
            "--ledger", str(ledger), "--output-dir"]
    for name, cached in [("final", 0), ("second", 1)]:
        assert cli.main([*args, str(tmp_path / name)]) == 0
        report = json.loads((tmp_path / name / "dataset.json").read_bytes())["jev_review"]
        assert (report["scored"], report["errors"], report["cached"], model.calls) == (1, 0, cached, 1)
        assert report["items"][0]["decision"]["choice"] == "review"
        assert (tmp_path / name / "main.csv").is_file()
    assert before == {p.name: p.read_bytes() for p in baseline.iterdir()}
    assert cli.main([*args, str(baseline)]) != 0


def test_failed_item_has_a_receipt_and_can_resume_to_requested_destination(tmp_path):
    source, baseline = frozen(tmp_path)
    target = tmp_path / "final"
    partial, report = review_dataset(source, baseline, target, LocalScorer(fail=True))
    assert partial != target and not target.exists()
    assert report["errors"] == 1 and report["items"][0]["error"] == "synthetic invalid request"
    destination, report = review_dataset(source, baseline, target, LocalScorer())
    assert destination == target and report["scored"] == 1
    with pytest.raises(ValueError, match="outside"):
        review_dataset(source, baseline, baseline / "another", LocalScorer())
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="byte-identical"):
        review_dataset(source, baseline, tmp_path / "bad", LocalScorer())


def test_subject_selection_alone_cannot_admit_a_value(tmp_path, monkeypatch):
    from attestextract.jev_packets import make_packets
    source, baseline = frozen(tmp_path)
    data = json.loads((baseline / "dataset.json").read_bytes())
    [packet] = make_packets(data, SOURCE)
    packet["request"]["questions"]["disposition"]["criteria"]["bind_1"] = "S2"
    packet["options"]["bind_1"] = {"id": "o0", "label": "S2", "evidence": []}
    monkeypatch.setattr("attestextract.jev_review.make_packets", lambda *args: [packet])
    model = LocalScorer()
    def predict(request):
        model.calls += 1
        choices = request["questions"]["disposition"]["criteria"]
        selected = "bind_1" if "bind_1" in choices else "mismatch"
        return {"model": "Intern-Decision-4B", "answers": {"disposition": {"type": "choice",
                "choice": selected, "confidence": 1, "probabilities": {k: int(k == selected) for k in choices}}}}
    model.predict = predict
    for n in (1, 2):
        output, report = review_dataset(source, baseline, tmp_path / str(n), model, ledger_path=tmp_path / "both.sqlite")
        [item] = report["items"]
        assert item["subject_check"]["choice"] == "bind_1" and item["field_check"]["decision"]["choice"] == "mismatch"
        assert item["decision"]["choice"] == "review" and report["field_checks"] == 1
        [obs] = json.loads((output / "dataset.json").read_bytes())["observations"]
        assert obs["delivery"] == "provisional" and obs["jev_review"]["field_verdict"] == "mismatch"
        assert model.calls == 2
