"""Public source-level regressions for scoped shorthand and ambiguous ownership."""

import pytest

from niceextract.dataset import query_dataset
from niceextract.dataset_resolution import resolve_observations


def _span(source, quote):
    start = source.index(quote)
    return {"quote": quote, "start": start, "end": start + len(quote)}


def _entry(source, field, value, entity, bound, quote, references):
    ref = _span(source, quote)
    return {"observation_id": (entity or "") + field, "source_kind": "prose", "source_record": "/candidates/0",
            "record_type": field, "property_name": field, "entity": entity, "value": value,
            "source_evidence": [{"fields": ["value"], "quote": quote, "span": ref}],
            "subject_id": bound, "target_match": "supported", "binding_status": "supported" if bound else "unknown",
            "binding_evidence_ids": references, "blockers": []}


def _resolve(source, records, objects, evidence):
    analysis = {"observations": records, "objects": objects, "evidence": evidence, "relations": [],
                "table_subject": "named batches", "task": {"fields": {"name": "name", "yield": "yield"}}}
    return resolve_observations(source, {}, analysis, identity_fields=["name"], row_subject="named batches")


@pytest.mark.parametrize(("prefix", "before", "between", "supported"), [
    ("研究者将青山批分为早期和晚期", "", "", True),
    ("记录了青山批早期和晚期", "", "", False),
    ("研究者将青山批分为早期和晚期", "以下两项属于另一批青海批，", "", False),
    ("研究者将青山批分为早期和晚期", "", "以下介绍青海批，", False),
])
def test_scoped_division_supplies_shorthand_and_keeps_components_distinct(prefix, before, between, supported):
    source = prefix + "，" + before + "青早期表现均匀；" + between + "青晚期表现松散。"
    evidence = [{"evidence_id": "division", **_span(source, prefix)}]
    objects, records = [], []
    for name, value in [("青早期", "表现均匀"), ("青晚期", "表现松散")]:
        quote = name + value
        evidence.append({"evidence_id": name, **_span(source, quote)})
        objects.append({"object_id": name, "name": name + "（青山批" + name[1:] + "）",
                        "scope": "chapter", "is_table_subject": True, "mention_evidence_ids": [name]})
        records.append(_entry(source, "name", name, name, name, quote, ["division"]))
        records.append(_entry(source, "yield", value, name, name, quote, [name]))
    resolved = _resolve(source, records, objects, evidence)
    for name in ["青早期", "青晚期"]:
        direct = resolved[name + "yield"]["direct"]
        if not supported:
            assert direct["status"] == "supported" and direct["label"] == name
            assert direct["id"] != name
            assert not direct.get("alias_names")
            continue
        assert direct["id"] == name and direct["status"] == "supported"
        assert direct["alias_names"] == ["青山批" + name[1:]]
        parent = resolved[name + "yield"]["related"][0]
        assert (parent["label"], parent["reason"], parent["source_id"]) == ("青山批", "part_of", direct["id"])
        assert parent["target_id"] != direct["id"]
        assert {e["quote"] for e in direct["identity_evidence"]} == {prefix, "青早期", "青晚期"}
        assert all(source[e["start"]:e["end"]] == e["quote"] for e in direct["identity_evidence"])
        dataset = {"observations": [{"row_ids": ["row"], "direct": direct, "entity": name}]}
        assert query_dataset(dataset, name) == query_dataset(dataset, "青山批" + name[1:])
        assert query_dataset(dataset, "青山批") == []


@pytest.mark.parametrize(("claim", "expected"), [
    ("Either A or B yield = 30.", {"a", "b"}),
    ("A and B combined yield = 30.", set()),
    ("Either A or B was used, C yield = 30.", set()),
    ("Neither A nor B yield = 30.", set()),
])
def test_only_explicit_value_local_alternatives(claim, expected):
    source = "A.\nB.\n" + claim
    evidence = [{"evidence_id": x, **_span(source, x + ".")} for x in ("A", "B")]
    objects = [{"object_id": x.lower(), "name": x, "scope": "section", "mention_evidence_ids": [x],
                "is_table_subject": True} for x in ("A", "B")]
    records = [_entry(source, "yield", "30", None, None, "30", [])]
    result = _resolve(source, records, objects, evidence)["yield"]
    assert {c["id"] for c in result["candidates"] if c["basis"] == "source_alternatives"} == expected
    assert result["direct"]["status"] == "unknown"


def test_homonyms_keep_competing_ids_for_a_bare_statement():
    source = "Line A in East.\nLine A in West.\nLine A yield = 30."
    evidence = [{"evidence_id": loc, **_span(source, "Line A in " + loc)} for loc in ("East", "West")]
    objects = [{"object_id": loc, "name": "Line A", "scope": loc, "mention_evidence_ids": [loc],
                "is_table_subject": True} for loc in ("East", "West")]
    record = _entry(source, "yield", "30", "Line A", "East", "30", [])
    result = _resolve(source, [record], objects, evidence)["Line Ayield"]
    assert result["direct"]["status"] == result["target"]["status"] == "unknown"
    assert {c["id"] for c in result["candidates"] if c["basis"] == "source_scope_candidates"} == {"East", "West"}


def test_section_resolves_an_otherwise_ambiguous_registered_owner():
    source = "## East\nLine A in East.\nLine A yield = 30.\n## West\nLine A in West."
    evidence = [{"evidence_id": loc, **_span(source, "Line A in " + loc)} for loc in ("East", "West")]
    evidence.append({"evidence_id": "measurement", **_span(source, "Line A yield = 30.")})
    objects = [{"object_id": loc, "name": "Line A", "scope": loc, "mention_evidence_ids": [loc],
                "is_table_subject": True} for loc in ("East", "West")]
    record = _entry(source, "yield", "30", "Line A", "East", "30", ["measurement"])
    result = _resolve(source, [record], objects, evidence)["Line Ayield"]
    assert (result["direct"]["status"], result["direct"]["id"]) == ("supported", "East")
    assert not any(c["basis"] == "source_scope_candidates" for c in result["candidates"])


def test_source_identity_fields_supply_alternatives_without_model_ids():
    source = "甲线。\n乙线。\n甲线和乙线均已开工，其中一线的yield为30。"
    identities = [_entry(source, "name", name, name, None, name, []) for name in ("甲线", "乙线")]
    item = _entry(source, "yield", "30", None, None, "30", [])
    result = _resolve(source, [*identities, item], [], [])["yield"]
    options = [c for c in result["candidates"] if c["basis"] == "source_alternatives"]
    assert {c["label"] for c in options} == {"甲线", "乙线"}
    assert len({c["id"] for c in options}) == 2
    assert not _resolve(source, [item], [], [])["yield"]["candidates"]


@pytest.mark.parametrize(("clause", "expected"), [
    ("Aster yield = 12", "supported"),
    ("Aster insert yield = 12", "unknown"),
    ("Aster yielded no samples; controller yield = 12", "unknown"),
])
def test_verbatim_identity_survives_an_unresolved_display_name(clause, expected):
    source = clause + "."
    model_label = "Aster (unit built by Delta)"
    obj = {"object_id": "unit", "name": model_label, "scope": "test", "is_table_subject": True,
           "mention_evidence_ids": ["subject"]}
    evidence = [{"evidence_id": "subject", **_span(source, "Aster")},
                {"evidence_id": "property", **_span(source, clause)}]
    records = [_entry(source, "name", "Aster", "Aster", "unit", "Aster", ["subject"]),
               _entry(source, "yield", "12", "Aster", "unit", "12", ["property"])]
    result = _resolve(source, records, [obj], evidence)["Asteryield"]
    assert result["direct"]["status"] == expected
    if expected == "supported":
        assert result["target"]["status"] == "supported" and result["target"]["label"] == "Aster"
        assert result["target"]["id"] != "unit" and result["direct"]["alias_names"] == []
