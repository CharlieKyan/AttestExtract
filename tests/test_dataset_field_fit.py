from attestextract.dataset_field_fit import checked_field_fit


def _assess(definition, property_name, quote, **claim):
    item = {"value": "100", "target_match": "supported", "property_name": property_name, **claim}
    return checked_field_fit(definition, item, {"status": "supported", "reason": "legacy"},
                             [{"start": 0, "end": len(quote), "quote": quote}])


def test_qualification_in_task_requires_evidence_for_that_measurement():
    definition = "该试样的可用长度，保留单位与口径。只有毛坯长度或余料长度的表述时，不改称可用长度。"
    pending = _assess(definition, "长度", "平均长度100 m")
    assert (pending["status"], pending["requested_property"]) == ("unknown", "可用长度")
    assert _assess(definition, "长度", "平均可用长度100 m")["status"] == "supported"
    assert _assess(definition, "长度", "毛坯长度为100 m")["status"] == "contradicted"
    assert _assess(definition, "长度", "毛坯长度20 m，可用长度为100 m")["status"] == "supported"
    assert _assess(definition, "长度", "毛坯长度20 m，平均长度100 m")["status"] == "unknown"


def test_english_qualified_property_and_explicit_exclusion():
    definition = "Usable length of the specimen. Exclude blank length."
    assert _assess(definition, "mean length", "mean length was 100 mm")["status"] == "unknown"
    assert _assess(definition, "length", "Usable length was 100 mm")["status"] == "supported"
    assert _assess(definition, "length", "blank length was 100 mm")["status"] == "contradicted"
    assert _assess(definition, "length", "blank length 5 mm and length 100 mm")["status"] == "unknown"


def test_explicit_denial_applies_to_the_same_subject_and_property():
    definition = "该仪器的表面温度，保留单位"
    a = _assess(definition, "室温", "仪器 A 环境温度为100 °C，表面温度未测。")
    assert a["status"] == "contradicted"
    b = _assess(definition, "表面温度", "A 表面温度为100 °C，B 表面温度未测。")
    assert b["status"] == "supported"


def test_inclusive_description_does_not_require_an_extra_qualification():
    assert _assess("试样的各类长度，包括各个方向。", "长度", "长度100 m")["status"] == "supported"
    assert _assess("Reported mass of the row subject", "mass", "mass 100 g")["status"] == "supported"


def test_second_measurement_does_not_certify_or_override_the_first():
    definition = "Usable length of the specimen. Exclude blank length."
    assert _assess(definition, "blank length", "blank length = 100 mm and usable length = 50 mm")["status"] == "contradicted"
    assert _assess(definition, "mean length", "mean length = 100 mm and usable length = 50 mm")["status"] == "unknown"
    assert _assess(definition, "length", "blank length = 50 mm and usable length = 100 mm")["status"] == "supported"
