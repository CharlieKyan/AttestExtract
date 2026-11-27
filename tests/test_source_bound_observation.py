"""A saved model binding must identify the same source occurrence to carry a reading."""

import pytest

from niceextract import assemble_dataset, extract, query_dataset
from niceextract.analysis import _input_observations
from niceextract.backends import ReplayBackend
from niceextract.dataset_extraction_owner import ExtractionOwners


def build_snapshot(second, *, repeated=False, relation="This assembly contains the coupon."):
    parent = "Unit R"
    first = "coupon mass was 3 g, "
    source = f"{parent} was examined. {first}{second} {relation}"
    fields = {"n": "Named unit", "w": "Width of the unit or associated coupons."}
    values = {"entity": "coupon", "value": "4", "unit": "mm", "conditions": None}
    record = {"record_type": "w", "values": values,
              "quotes": {"entity": second, "value": second, "unit": second, "conditions": ""}}
    result = extract(source, prompt_description="Read the tested device.", field_list=fields,
                     backend=ReplayBackend([{"records": [record]}], output_protocol="flat-v1"))
    evidence = {}
    readings, quarantined = _input_observations(source, result, evidence)
    def cite(key, quote):
        start = source.index(quote)
        evidence[key] = {"evidence_id": key, "quote": quote, "start": start, "end": start + len(quote)}
        return [key]
    def obj(key, label, quote, row):
        return {"object_id": key, "name": label, "kind": "instance", "scope": "current study",
                "is_table_subject": row, "mention_evidence_ids": cite("mention-" + key, quote),
                "semantic_status": "model_proposed"}
    objects = [obj("device", parent, parent, True), obj("coupon", parent + " coupon", first + second, False)]
    if repeated:
        objects.append(obj("another", "Unit S coupon", first + second, False))
    readings[0].update(subject_id="coupon", binding_status="supported", property_name="width",
                       target_match="supported", binding_evidence_ids=cite("binding", second))
    edge = {"source_id": "coupon", "target_id": "device", "predicate": "part_of", "relation_id": "r",
            "semantic_status": "model_proposed", "evidence_ids": cite("membership", relation)}
    snapshot = {"analysis_version": "0.1", "status": "complete", "source_format_status": "complete",
                "semantic_status": "model_proposed", "table_subject": "named units", "objects": objects,
                "source": result.source.model_dump(mode="json"), "relations": [edge], "observations": readings,
                "task": {"name": result.task.name, "description": result.task.description, "fields": fields},
                "evidence": list(evidence.values()), "quarantined": quarantined}
    return assemble_dataset(source, result.model_dump(mode="json"), snapshot, subject_label_field="n")


@pytest.mark.parametrize("reading,ambiguous,accepted", [
    ("width was 4 mm.", False, True),
    ("Plate B width was 4 mm.", False, False),
    ("width was 4 mm.", True, False),
])
def test_only_one_source_attributed_owner_can_supply_a_shared_subject(reading, ambiguous, accepted):
    data = build_snapshot(reading, repeated=ambiguous)
    obs, = data["observations"]
    assert obs["value"] == "4"
    if accepted:
        assert (obs["direct"]["id"], obs["target"]["id"]) == ("coupon", "device")
        assert query_dataset(data, "Unit R") == [obs]
        assert query_dataset(data, "coupon") == [obs]
    else:
        assert not query_dataset(data, "Unit R")
        assert obs["delivery"] == "detail"


@pytest.mark.parametrize("relation", [
    "This assembly is part of the coupon.",
    "This assembly is outside the coupon.",
    "This assembly has the same color as the coupon.",
    "This assembly contains a reference to the coupon.",
    "This assembly does not contain the coupon.",
])
def test_an_unverified_parent_relation_cannot_take_ownership_of_a_reading(relation):
    data = build_snapshot("width was 4 mm.", relation=relation)
    obs, = data["observations"]
    assert obs["direct"]["id"] == "coupon" and obs["value"] == "4"
    assert obs["target"]["status"] != "supported"
    assert not query_dataset(data, "Unit R")


@pytest.mark.parametrize(("quote", "predicate", "expected"), [
    ("该装置包含试样", "part_of", True),
    ("该装置不包含试样", "part_of", False),
    ("试样采自该装置", "sample_of", True),
    ("该装置包含试样", "sample_of", False),
])
def test_membership_checks_direction_and_relation_kind(quote, predicate, expected):
    from niceextract.dataset_extraction_owner import _contextual_membership
    assert _contextual_membership(quote, "试样", predicate) == expected


@pytest.mark.parametrize("cohort,origin,expected", [
    ("North Site samples of outlet water were analyzed.", "samples", "North Site"),
    ("North Site samples of outlet water were analyzed.", "samples from an unregistered site", None),
    ("North Site samples and South Site samples of outlet water were analyzed.", "samples", None),
    ("North Site was installed. South Site samples of outlet water were analyzed.", "samples", None),
    ("North Site samples of outlet water were analyzed.\n\n## A different scenario", "samples", None),
])
def test_generic_owner_uses_declared_study_target_only_with_local_cohort(cohort, origin, expected):
    setup = "The study target is North Site. South Site was recorded separately."
    reading = f"Based on data from {origin}, outlet water conductivity was 7."
    source = f"{setup}\n\n## Measurements\n\n{cohort}\n\n{reading}"
    def entry(text):
        left = source.index(text)
        return {"quote": text, "span": {"start": left, "end": left + len(text)}, "fields": ["entity", "value"]}
    def identity(name, sentence):
        return {"record_type": "site", "entity": name, "value": name, "observation_id": name,
                "target_match": "supported", "source_evidence": [entry(sentence)]}
    rows = [identity("North Site", "The study target is North Site"),
            identity("South Site", "South Site was recorded separately")]
    owners = ExtractionOwners(source, {"table_subject": "named sites", "objects": [], "evidence": [],
                                       "relations": [], "observations": rows}, True, "site")
    obs = {"source_kind": "prose", "name_handling": "as_reported", "target_match": "supported",
           "fit": {"status": "supported"}, "source_evidence": [entry(reading)]}
    direct = {"label": "outlet water", "evidence": [entry(reading)["span"]]}
    target = owners._study_context(obs, direct, "Conductivity of the site or associated samples.")
    assert (target["label"] if target else None) == expected
