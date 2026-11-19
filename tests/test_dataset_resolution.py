"""Independent, source-level expectations for local ownership and field use."""

import pytest

from attestextract.dataset_resolution import resolve_observations
from attestextract.table_routes import build_table_input


def _span(source, quote, start=0):
    offset = source.index(quote, start)
    return {"start": offset, "end": offset + len(quote), "quote": quote}


def _observation(source, key, quote, entity, value, *, field="mass", **extra):
    return {"observation_id": key, "source_record": "/candidates/0", "record_type": field,
            "source_kind": "prose", "entity": entity, "value": value, "property_name": field,
            "target_match": "supported", "source_evidence": [
                {"fields": ["value"], "quote": quote, "span": _span(source, quote)}], **extra}


def _object(key, label, mention, target=True):
    return {"object_id": key, "name": label, "scope": "local", "mention_evidence_ids": [mention],
            "is_table_subject": target}


def _run(source, obs, objects=(), evidence=(), relations=(), result=None, **options):
    analysis = {"observations": obs, "task": {"fields": {o["record_type"]: o["record_type"] for o in obs}},
                "table_subject": "batch", "objects": objects, "evidence": evidence, "relations": relations}
    return resolve_observations(source, result or {}, analysis, identity_fields=options.get("identity_fields", ["name"]),
                                row_subject=options.get("row_subject", "batch"))


def test_forward_alias_is_scoped_and_preserves_separate_measurements():
    source = "# East\nP2 mass = 12.\nEast unit (hereafter P2).\nP2 mass = 14.\n# West\nP2 mass = 18."
    rows = [_observation(source, str(n), f"P2 mass = {n}.", "P2", n) for n in [12, 14, 18]]
    rows.append(_observation(source, "identity", "East unit", "East unit", "East unit", field="name"))
    result = _run(source, rows)
    first = result["12"]["direct"]
    assert first["label"] == "East unit" and first["id"] == result["14"]["direct"]["id"]
    assert result["12"]["target"]["status"] == "supported"
    assert result["18"]["direct"]["label"] == "P2" and result["18"]["direct"]["id"] != first["id"]
    assert all(source[e["start"]:e["end"]] == e["quote"] for e in first["evidence"])


def test_redefinition_and_negation_suspend_only_disputed_facts():
    source = "North unit (hereafter P2).\nSouth unit (hereafter P2).\nP2 mass = 12.\nP2 mass is not 14."
    rows = [_observation(source, "one", "P2 mass = 12.", "P2", 12),
            _observation(source, "two", "14", "P2", 14)]
    rows += [_observation(source, name, name, name, name, field="name") for name in ["North unit", "South unit"]]
    result = _run(source, rows)
    assert result["one"]["direct"]["reason"] == "conflicting_scoped_definitions"
    assert result["two"]["direct"]["status"] == result["two"]["fit"]["status"] == "unknown"


def test_source_relation_identifies_target_without_promoting_component_measurement():
    source = "Pad mass = 12.\nPad is part of Mixer."
    evidence = [dict(evidence_id="value", **_span(source, "Pad mass = 12.")),
                dict(evidence_id="rel", **_span(source, "Pad is part of Mixer."))]
    row = _observation(source, "x", "12", "Pad", 12, subject_id="pad",
                       binding_status="supported", binding_evidence_ids=["value"])
    edge = {"source_id": "pad", "target_id": "mixer", "relation_id": "r", "predicate": "part_of", "evidence_ids": ["rel"]}
    result = _run(source, [row], [_object("pad", "Pad", "value", False), _object("mixer", "Mixer", "rel")],
                  evidence, [edge])["x"]
    assert (result["direct"]["id"], result["target"]["id"]) == ("pad", "mixer")
    assert result["related"][0]["reason"] == "part_of" and result["target"]["basis"] == "relation"


def test_identity_field_cannot_name_component_as_parent():
    source = "Mixer contains Pad."
    row = _observation(source, "x", "Mixer contains Pad.", "Mixer", "Pad", field="name", binding_status="supported",
                       subject_id="mixer", binding_evidence_ids=["e"])
    result = _run(source, [row], [_object("mixer", "Mixer", "e")],
                  [dict(evidence_id="e", **_span(source, source))])["x"]
    assert result["fit"]["status"] == "unknown"


@pytest.mark.parametrize("conflict", [False, True])
def test_equivalence_component_conflict_is_checked_before_merge(conflict):
    source = "AA mass = 1.\nCC mass = 2.\nAA is the same entity as BB.\nBB is the same entity as CC."
    quotes = source.splitlines()
    evidence = [dict(evidence_id=str(i), **_span(source, line)) for i, line in enumerate(quotes)]
    objects = [_object(x, x, mention) for x, mention in [("AA", "0"), ("BB", "2"), ("CC", "1")]]
    rows = [_observation(source, x, quotes[i], x, i+1, binding_status="supported", subject_id=x,
                         binding_evidence_ids=[str(i)]) for i, x in enumerate(["AA", "CC"])]
    edges = [{"relation_id": str(i), "source_id": left, "target_id": right, "predicate": "same_as",
              "evidence_ids": [str(i)]} for i, left, right in [(2, "AA", "BB"), (3, "BB", "CC")]]
    if conflict:
        edges.append({"relation_id": "conflict", "source_id": "AA", "target_id": "CC",
                      "predicate": "different_from", "evidence_ids": ["0"]})
    result = _run(source, rows, objects, evidence, edges)
    assert (result["AA"]["direct"]["id"] == result["CC"]["direct"]["id"]) == (not conflict)


def _table_fixture(transposed=False, threshold=False):
    title = "Classification thresholds" if threshold else "Mean"
    matrix = [["Batch", title], ["A17", "12"]]
    if transposed:
        matrix = list(zip(*matrix))
    source = "<table>" + "".join("<tr>" + "".join("<td>" + v + "</td>" for v in row) + "</tr>"
                                 for row in matrix) + "</table>"
    cells = {e["quote"]: {**e, "selected_start": e["start"], "selected_end": e["end"], "selected_quote": e["quote"]}
             for e in build_table_input(source)["entries"] if e["kind"] == "cell"}
    rows, records = [], []
    for i, (label, field) in enumerate([("12", "mass"), ("A17", "name")]):
        item = _observation(source, field, label, "A17", label, field=field, source_kind="table",
                            source_record=f"/table_results/0/candidates/{i}")
        rows.append(item)
        records.append({"scope": "individual", "resolved_fields": {"entity": [cells["A17"]], "value": [cells[label]]}})
    raw = {"table_results": [{"table_id": "table-0001", "candidates": records}]}
    return source, rows, raw


@pytest.mark.parametrize("transposed", [False, True])
def test_same_fact_on_both_axes_purpose_change_affects_fit(transposed):
    for threshold in [False, True]:
        source, rows, raw = _table_fixture(transposed, threshold)
        result = _run(source, rows, result=raw)["mass"]
        assert result["direct"]["label"] == result["target"]["label"] == "A17"
        assert result["fit"]["status"] == ("unknown" if threshold else "supported")
        assert result["purpose"] == ("classification_threshold" if threshold else "reported_mean")


def test_original_binding_and_its_possible_parent_remain_searchable():
    source = "Pad mass is not 12.\nUnit X is located in Area Y."
    row = _observation(source, "x", "12", "Pad", 12, subject_id="x", binding_status="supported",
                       binding_evidence_ids=["v"])
    ev = [dict(evidence_id=k, **_span(source, q)) for k, q in [("v", "12"), ("r", "Unit X is located in Area Y.")]]
    objects = [_object("x", "Unit X", "r"), _object("y", "Area Y", "r")]
    edges = [{"source_id": "x", "target_id": "y", "predicate": "located_in", "relation_id": "r", "evidence_ids": ["r"]}]
    result = _run(source, [row], objects, ev, edges)["x"]
    assert result["direct"]["status"] == "unknown"
    assert {(c["id"], c["reason"]) for c in result["candidates"]} >= {("x", "original_binding"), ("y", "located_in")}


def test_explicit_attribute_list_recovers_occurrence_from_original_entity_quote():
    source = "试样甲面积 $2 . 0$ ，平均高度12 mm。"
    entity_quote = source.rstrip("。")
    row = _observation(source, "x", "平均高度12 mm", "试样甲", "12", conditions="平均高度", unit="mm")
    row["source_evidence"].append({"fields": ["entity"], "quote": entity_quote, "span": _span(source, entity_quote)})
    result = _run(source, [row])["x"]
    assert result["direct"]["label"] == "试样甲"
    assert result["direct"]["evidence"] == [_span(source, entity_quote)]
    assert result["target"]["status"] == "unknown"


def test_shared_cell_can_recover_owner_with_empty_entity_using_opposite_property_axis():
    source, rows, raw = _table_fixture()
    rows[0].update(entity=None, property_name="Mean")
    raw["table_results"][0]["candidates"][0]["resolved_fields"]["entity"] = []
    assert _run(source, rows, result=raw)["mass"]["target"]["label"] == "A17"
    rows[0]["property_name"] = "another property"
    assert _run(source, rows, result=raw)["mass"]["direct"]["status"] == "unknown"


def test_split_bindings_same_cell_preserve_local_membership_and_explanation():
    source, rows, raw = _table_fixture()
    first = rows[0]
    second = {**first, "observation_id": "second", "source_record": "/table_results/0/candidates/1"}
    rows = [first, second]
    records = raw["table_results"][0]["candidates"]
    records[1] = records[0]
    for i, row in enumerate(rows):
        row.update(subject_id=str(i), binding_status="supported", binding_evidence_ids=["e"])
    evidence = [dict(evidence_id="e", **_span(source, "<td>A17</td><td>12</td>"))]
    objects = [_object("0", "A17", "e"), _object("1", "unit A17", "e")]
    # A configured display-name role requires evidence of that role before projection.
    typed = _run(source, rows, objects, evidence, result=raw)
    assert typed["mass"]["target"]["reason"] == "target_identity_role_unconfirmed"
    result = _run(source, rows, objects, evidence, result=raw, identity_fields=[])
    for row in result.values():
        assert row["target"]["membership_sources"] == ["0", "1"]
        assert row["target"]["label"] == "A17"
        assert row["target"]["evidence"][-1]["evidence_id"] == "e"
    assert len({row["direct"]["id"] for row in result.values()}) == 1
    # One explicit disagreement changes consensus.
    objects[1]["is_table_subject"] = False
    assert _run(source, rows, objects, evidence, result=raw, identity_fields=[])["mass"]["target"]["status"] == "unknown"


@pytest.mark.parametrize("transposed", [False, True])
@pytest.mark.parametrize("role,expected", [("Station", "supported"), ("Material", "unknown")])
def test_target_membership_requires_the_source_role_of_the_configured_name_field(transposed, role, expected):
    source, observations, result = _table_fixture()
    source = source.replace("Batch", "Station")
    matrix = [[role, "Mass"], ["B18", "27"]]
    if transposed:
        matrix = list(zip(*matrix))
    source += "\n<table>" + "".join("<tr>" + "".join(f"<td>{v}</td>" for v in cells) + "</tr>"
                                    for cells in matrix) + "</table>"
    # Build all evidence on the final immutable source, including the earlier identity example.
    for ti, (owner, number) in enumerate([("A17", "12"), ("B18", "27")]):
        table_id = f"table-{ti+1:04d}"
        entries = build_table_input(source, table_id=table_id)["entries"]
        cells = {e["quote"]: {**e, "selected_quote": e["quote"], "selected_start": e["start"],
                              "selected_end": e["end"]} for e in entries if e["kind"] == "cell"}
        record = {"scope": "individual", "resolved_fields": {"entity": [cells[owner]], "value": [cells[number]]}}
        if ti == 0:
            observations = [_observation(source, "n", owner, owner, owner, field="name", source_kind="table",
                                         source_record="/table_results/0/candidates/0")]
            result = {"table_results": [{"table_id": table_id, "candidates": [
                {"scope": "individual", "resolved_fields": {"entity": [cells[owner]], "value": [cells[owner]]}}]}]}
        else:
            observations.append(_observation(source, "m", number, owner, number, source_kind="table",
                                             source_record="/table_results/1/candidates/0",
                                             binding_status="supported", subject_id="owner", binding_evidence_ids=["e"]))
            result["table_results"].append({"table_id": table_id, "candidates": [record]})
    evidence = [dict(evidence_id="e", **_span(source, "B18"))]
    measurement = _run(source, observations, [_object("owner", "B18", "e")], evidence, result=result)["m"]
    assert measurement["direct"]["label"] == "B18"
    assert measurement["target"]["status"] == expected
    assert role in [e["quote"] for e in measurement["target"]["evidence"]]


def test_prose_model_binding_cannot_join_unrelated_subject_and_value_citations():
    source = "Mixer R completed calibration. An unlabelled sample returned 12."
    row = _observation(source, "o", "12", None, "12", binding_status="supported", subject_id="m",
                       binding_evidence_ids=["name", "reading"])
    evidence = [dict(evidence_id=k, **_span(source, q)) for k, q in [("name", "Mixer R"), ("reading", "12")]]
    obs = _run(source, [row], [_object("m", "Mixer R", "name")], evidence)["o"]
    assert obs["direct"]["status"] == "unknown"
    assert any(c["id"] == "m" for c in obs["candidates"])


def test_relation_under_a_later_heading_remains_a_candidate():
    source = "# East\nPad mass = 12.\n# West\nPad is part of Mixer."
    evidence = [dict(evidence_id="v", **_span(source, "Pad mass = 12.")),
                dict(evidence_id="r", **_span(source, "Pad is part of Mixer."))]
    row = _observation(source, "x", "12", "Pad", 12, binding_status="supported", subject_id="p",
                       binding_evidence_ids=["v"])
    pad = {**_object("p", "Pad", "v", False), "mention_evidence_ids": ["v", "r"]}
    result = _run(source, [row], [pad, _object("m", "Mixer", "r")], evidence,
                  [{"source_id": "p", "target_id": "m", "predicate": "part_of", "evidence_ids": ["r"]}])["x"]
    assert result["target"]["status"] == "unknown" and not result["related"]
    assert any(c["id"] == "m" and c["reason"] == "part_of" for c in result["candidates"])


def test_wrong_unit_in_parallel_parameter_block():
    source = "<table><tr><td>Meter</td><td>Rate</td><td>Station</td><td>A17</td></tr>" + \
             "<tr><td>m</td><td>50</td><td>g</td><td>12</td></tr></table>"
    cells = {e["quote"]: {**e, "selected_start": e["start"], "selected_end": e["end"], "selected_quote": e["quote"]}
             for e in build_table_input(source)["entries"] if e["kind"] == "cell"}
    row = _observation(source, "x", "12", "A17", 12, source_kind="table", source_record="/table_results/0/candidates/0")
    record = {"scope": "individual", "resolved_fields": {"entity": [cells["A17"]], "value": [cells["12"]], "unit": [cells["m"]]}}
    raw = {"table_results": [{"table_id": "table-0001", "candidates": [record]}]}
    decision = _run(source, [row], result=raw)["x"]
    assert decision["direct"]["label"] == "A17"
    assert decision["fit"]["reason"] == "qualifier_crosses_intervening_label"
