import pytest

from niceextract.dataset_prose_rows import prose_record_keys


def readings(text, values):
    evidence = {"start": 0, "end": len(text), "quote": text}
    obs = []
    for prop, value, condition in values:
        obs.append({
            "observation_id": prop, "eligible": True, "source_kind": "prose",
            "direct": {"id": "coupon", "label": "coupon", "evidence": [evidence]},
            "target": {"id": "batch"}, "source_evidence": [{**evidence, "fields": ["value"], "span": evidence}],
            "property_name": prop, "field": prop, "value": value, "unit": "mm",
            "conditions": condition, "statistic_kind": "value", "purpose": "reported",
        })
    return obs


def test_one_cited_specimen_statement_can_supply_two_columns():
    source = "The coupon length was 12 mm, and its width was 8 mm."
    obs = readings(source, [("length", "12", None), ("width", "8", None)])
    keys = prose_record_keys(source, [0, len(source)], obs)
    assert keys["length"] == keys["width"]


@pytest.mark.parametrize("transition", ["after cooling its", "in a second run its", "another coupon's"])
def test_explicit_new_condition_in_source_prevents_pairing_even_if_model_omitted_it(transition):
    source = f"The coupon length was 12 mm, and {transition} width was 8 mm."
    obs = readings(source, [("length", "12", None), ("width", "8", None)])
    assert prose_record_keys(source, [0, len(source)], obs) == {}


def test_repeated_field_and_disjoint_owner_citations_never_pair_by_list_order():
    source = "The coupon length was 12 mm, and its width was 8 mm."
    obs = readings(source, [("length", "12", None), ("width", "8", None)])
    assert not prose_record_keys(source, [0, len(source)], [*obs, obs[0]])
    obs[1]["direct"]["evidence"] = [{"start": 29, "end": len(source), "quote": source[29:]}]
    assert not prose_record_keys(source, [0, len(source)], obs)


def test_typical_bounds_at_different_operating_conditions_keep_separate_rows():
    definition = "Unit X (U1)"
    sentences = ["Unit X under load generally exceeds 10 kg", "Unit X at rest generally exceeds 3 mm"]
    source = ". ".join([definition, *sentences])
    obs = []
    for sentence, field, value, unit in zip(sentences, ["load", "travel"], ["10", "3"], ["kg", "mm"]):
        start = source.index(sentence)
        item, = readings(source, [(field, value, sentence.removeprefix("Unit X "))])
        item["direct"] = {"id": "U1", "label": "Unit X", "designation_span": {"start": 0, "end": len(definition)}}
        item["target"] = {"id": "U1"}
        item["source_evidence"] = [{"fields": ["entity", "value"], "span": {"start": start, "end": start + len(sentence)}}]
        item.update(unit=unit, statistic_kind="bound", statistic_parts={"qualifier": "generally"})
        obs.append(item)
    assert not prose_record_keys(source, [0, len(source)], obs)
