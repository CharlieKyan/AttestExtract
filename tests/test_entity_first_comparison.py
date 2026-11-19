"""Source-backed fixtures: manually specified owners/attributes under controlled roster omissions."""

import json

import pytest

from attestextract.entity_first import run_entity_first
from attestextract.models import BackendResponse


class TwoObjectBackend:
    name = "two-object-fixture"
    mode = "replay"

    def __init__(self, labels, prop, field, missing=True):
        self.labels = labels
        self.prop = prop
        self.field = field
        self.missing = missing

    def generate(self, request):
        packet = json.loads(request.messages[1]["content"])
        window = packet["windows"][0]["id"]

        def citation(quote):
            return {"window": window, "quote": quote}

        def proposal(label, key):
            return {"label": label, "key": key, "mention": citation(label), "qualification": citation(label)}

        stage = packet["stage"]
        first, second = self.labels
        if stage == "inventory":
            objects = [proposal(first, "first")]
            if not self.missing:
                objects.append(proposal(second, "second"))
            response = {"objects": objects}
        else:
            ids = {card["label"]: card["object_id"] for card in packet["objects"]}
            nominations = []
            if second not in ids and packet["rescue"]:
                nominations.append(proposal(second, "new"))
                ids[second] = "new"
            facts = []
            for label, value in [(first, "80"), (second, "60"), ("Total", "140")]:
                owner = ids.get(label)
                facts.append({"field": self.field, "value": value,
                              "target_id": owner if owner != "new" else None,
                              "nomination_key": "new" if owner == "new" else None,
                              "value_source": citation(value), "source_subject": label, "subject_source": citation(label),
                              "property_source": citation(self.prop), "link_source": citation(label),
                              "fit_source": citation(self.prop), "fit": "supported" if owner else "unknown",
                              "purpose": "aggregate" if label == "Total" else "measurement",
                              "reason": "Local table assignment."})
            response = {"nominations": nominations, "facts": facts}
        return BackendResponse(text=json.dumps(response), model="synthetic", usage={"total_tokens": 10})


@pytest.mark.parametrize("subject,labels,prop,field", [
    ("production lines", ("Line A", "Line B"), "Count", "items"),
    ("specimens", ("Sample A", "Sample B"), "Load", "failure_load"),
    ("stations", ("Station A", "Station B"), "Hours", "duration"),
])
@pytest.mark.parametrize("transposed", [False, True])
def test_local_source_recovers_omitted_target_and_preserves_group_total(subject, labels, prop, field, transposed):
    first, second = labels
    if transposed:
        source = f"| Name | {first} | {second} | Total |\n| --- | --- | --- | --- |\n| {prop} | 80 | 60 | 140 |"
    else:
        source = f"| Name | {prop} |\n| --- | --- |\n| {first} | 80 |\n| {second} | 60 |\n| Total | 140 |"
    for missing, rescue, expected in [(False, False, {first: "80", second: "60"}),
                                      (True, False, {first: "80"}), (True, True, {first: "80", second: "60"})]:
        report = run_entity_first(source, {field: prop}, subject, TwoObjectBackend(labels, prop, field, missing),
                                  rescue=rescue, max_calls=4)
        names = {o["object_id"]: o["label"] for o in report["objects"]}
        assert {names[o["owner_id"]]: o["value"] for o in report["observations"] if o["qualified"]} == expected
        assert report["status"] == "complete"
        assert len(report["observations"]) == 3
        total = next(o for o in report["observations"] if o["value"] == "140")
        assert not total["qualified"] and total["owner_id"] is None
        assert report["budget"]["calls_used"] == 2
        assert report["budget"]["usage"]["total_tokens"] == 10 * report["budget"]["calls_used"]
