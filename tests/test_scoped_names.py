"""Independent source claims for name decisions, their scope and their limitations."""

import json

import pytest

from niceextract.dataset import query_dataset
from niceextract.dataset_resolution import resolve_observations
from niceextract.scoped_names import validate_name_receipt
from niceextract.table_routes import build_table_input
from niceextract.verification_protocol import apply_decision, parse_step, parse_unified_step
from niceextract.verification_sources import SourceIndex


def scenario(*, relation, source, statement, alias, target, definition, scope=(), rivals=(), later=(),
             mention_position=None, second_definition=None, as_extracted=False, display=None,
             other_member_labels=(), unified=False):
    index = SourceIndex(source)
    def quote(text, position=None):
        at = source.index(text) if position is None else position
        p = next(p for p in index.passages if p["start"] <= at and at + len(text) <= p["end"])
        return {"passage_id": p["passage_id"], "quote": text,
                "occurrence": p["text"][:at - p["start"]].count(text)}

    def observation(oid, s):
        pos = source.index(s)
        value = next(w for w in s.split() if w.rstrip(".").isdigit()).rstrip(".")
        return {"observation_id": oid, "value": value, "entity": alias, "unit": None, "record_type": "yield",
                "source_kind": "prose", "source_evidence": [{"quote": s, "fields": ["entity", "value"],
                "span": {"start": pos, "end": pos + len(s)}}]}

    def obj(key, name, citation, row):
        return {"object_id": key, "name": name, "kind": "instance", "scope": "", "is_table_subject": row,
                "mentions": [citation]}

    observations = [observation("origin", statement), *(observation(key, s) for key, s in later)]
    use = quote(alias, source.index(statement) + statement.index(alias) if mention_position is None else mention_position)
    ref = quote(target)
    source_id = "owner" if relation in {"related", "different"} else "target"
    objects = [obj("target", display or target, ref, True)]
    for n, label in enumerate(other_member_labels):
        objects.append(obj(f"member-{n}", label, ref, True))
    if source_id == "owner":
        objects.append(obj("owner", alias, use, False))
    for key in rivals:
        objects.append(obj(key, key, quote(key), True))
    descriptor = {"relation": relation, "mention": use, "target_id": "target", "target_mention": ref,
                  "evidence": [quote(definition), quote(statement)], "scope_evidence": list(map(quote, scope)),
                  "competitor_ids": list(rivals), "reason": "Names interpreted from the cited source."}
    bindings = [{"observation_id": obs["observation_id"], "subject_id": source_id, "status": "supported",
                 "target_match": "supported", "property_name": "yield", "reason": "Reported here.",
                 "evidence": [quote(obs["source_evidence"][0]["quote"])],
                 "name_resolution": descriptor if obs["observation_id"] == "origin" else None}
                for obs in observations]
    if second_definition:
        name, defining, heading = second_definition
        occurrence = quote(alias, source.index(later[0][1]) + later[0][1].index(alias))
        objects.append(obj("second-target", name, quote(name), True))
        bindings[1].update(subject_id="second-target", name_resolution={
            **descriptor, "target_id": "second-target", "target_mention": quote(name), "mention": occurrence,
            "evidence": [quote(defining), quote(later[0][1])], "scope_evidence": [quote(heading)]})
    payload = {"action": "decide", "question": "", "objects": objects, "bindings": bindings}
    if unified:
        payload["relations"] = []
        for binding in bindings:
            name = binding.pop("name_resolution")
            if name is not None:
                predicate = name.pop("relation")
                payload["relations"].append({**name, "source_kind": "mention", "source_id": binding["observation_id"],
                                             "predicate": "different_from" if predicate == "different" else predicate})
        step = parse_unified_step(json.dumps(payload))
    else:
        step = parse_step(json.dumps(payload))
    objects, relations, updates, entries = apply_decision(
        step, source_text=source, source_hash="synthetic", batch=observations, objects=[], relations=[],
        entries={}, visible_passages=index.passages, source_index=index)
    for obs in observations:
        obs.update(updates[obs["observation_id"]])
        if as_extracted and obs["observation_id"] != "origin":
            obs.update(subject_id=None, binding_status="as_extracted", name_handling="as_reported",
                       binding_evidence_ids=[])
    analysis = {"name_resolution_protocol": "scoped-name-v1", "table_subject": "line",
                "task": {"fields": {"yield": "yield"}}, "objects": objects, "relations": relations,
                "evidence": list(entries.values()), "observations": observations}
    return resolve_observations(source, {}, analysis, identity_fields=[], row_subject="line")


@pytest.mark.parametrize("as_extracted", [False, True])
@pytest.mark.parametrize("relation", ["reusable_alias", "local_identity"])
@pytest.mark.parametrize("unified", [False, True], ids=["archived", "single-entry"])
def test_site_shorthand_reuse_requires_explicit_scope(relation, as_extracted, unified):
    source = ("# Names\nIn the East section the team put the North station under the tag Q.\n# East\n"
              "Q yield = 3.\nQ yield = 4.\n# West\nQ yield = 5.")
    rows = scenario(relation=relation, source=source, alias="Q", target="North station",
                    definition="In the East section the team put the North station under the tag Q.", statement="Q yield = 3.",
                    scope=["# East"], later=[("second", "Q yield = 4."), ("west", "Q yield = 5.")],
                    as_extracted=as_extracted, unified=unified)
    assert rows["origin"]["direct"]["label"] == "North station"
    assert rows["origin"]["direct"]["local_mention"]["quote"] == "Q"
    if relation == "reusable_alias":
        assert rows["second"]["direct"]["id"] == rows["origin"]["direct"]["id"]
    else:
        assert rows["second"]["direct"]["id"] != rows["origin"]["direct"]["id"]
    assert rows["west"]["direct"]["id"] != rows["origin"]["direct"]["id"]


@pytest.mark.parametrize("relation", ["related", "different"])
@pytest.mark.parametrize("unified", [False, True], ids=["archived", "single-entry"])
def test_material_association_never_identifies_the_specimen_with_the_batch(relation, unified):
    source = "Batch Violet supplied Pin X.\nPin X had a yield of 9."
    rows = scenario(relation=relation, source=source, alias="Pin X", target="Batch Violet",
                    definition="Batch Violet supplied Pin X.", statement="Pin X had a yield of 9.", unified=unified)
    row = rows["origin"]
    assert row["direct"]["label"] == "Pin X"
    assert row["related"][0]["reason"] == relation
    assert row["target"]["label"] == ("Batch Violet" if relation == "related" else None)


def test_denying_specimen_provenance_only_withholds_the_related_target():
    definition = "Batch Violet never supplied Pin X."
    statement = "Pin X had a yield of 9."
    row = scenario(relation="related", source=definition + "\n" + statement, alias="Pin X",
                   target="Batch Violet", definition=definition, statement=statement)["origin"]
    assert row["direct"]["label"] == "Pin X" and row["direct"]["status"] == "supported"
    assert row["fit"]["status"] == "supported" and not row["related"]
    assert row["target"]["status"] == "unknown"


def test_unresolved_rival_keeps_source_observation_unmerged():
    source = "Mixer and Cutter used badge R on different shifts.\nR yield = 7."
    row = scenario(relation="local_identity", source=source, alias="R", target="Mixer",
                   definition="Mixer and Cutter used badge R on different shifts.", statement="R yield = 7.",
                   rivals=["Cutter"])["origin"]
    assert row["direct"]["label"] == "R" and row["direct"]["status"] == "supported"
    assert row["target"]["status"] == "unknown"
    assert {c["label"] for c in row["candidates"] if c["basis"] == "scoped_name"} == {"Mixer", "Cutter"}
    assert row["direct"]["local_mention"]["quote"] == "R"


@pytest.mark.parametrize("definition,readings", [
    ("将蓝批分为早期和晚期。", ["蓝早期 yield = 3.", "蓝晚期 yield = 6."]),
    ("蓝批经历两个阶段。先述晚期，再述早期。", ["蓝晚期 yield = 6.", "蓝早期 yield = 3."]),
])
def test_division_components_survive_paraphrase_and_reordering(definition, readings):
    source = definition + "\n" + "\n".join(readings)
    for statement in readings:
        alias = statement.split()[0]
        member = alias[1:]
        canonical = "蓝批" + member
        assert canonical not in source
        row = scenario(relation="local_identity", source=source, alias=alias, target=member,
                       definition=definition, statement=statement, display=canonical)["origin"]
        assert row["direct"]["label"] == canonical and row["target"]["label"] == canonical
        assert alias in row["direct"]["alias_names"]
        assert [v["quote"] for v in row["direct"]["identity_evidence"]] == ["蓝批", member]
        view = {"observations": [{**row, "row_ids": ["source-row"]}]}
        assert query_dataset(view, alias) == query_dataset(view, canonical) == view["observations"]


@pytest.mark.parametrize("heading,resolved", [("# 蓝批", True), ("# 测量结果", False)])
def test_shared_member_qualifier_uses_original_heading_or_keeps_competing_parents(heading, resolved):
    definition = "蓝批与绿批各有早期和晚期。"
    statement = "末段 yield = 5."
    source = definition + "\n" + heading + "\n" + statement
    row = scenario(relation="reusable_alias", source=source, alias="末段", target="晚期",
                   definition=definition, statement=statement, display="蓝批晚期", scope=[heading],
                   other_member_labels=["绿批晚期"])["origin"]
    if resolved:
        assert row["target"]["label"] == "蓝批晚期"
    else:
        assert row["direct"]["label"] == "末段" and row["target"]["status"] == "unknown"
        assert {r["label"] for r in row["direct"]["name_candidates"]} == {"蓝批晚期", "绿批晚期"}


def test_an_extra_unsourced_qualifier_cannot_become_a_searchable_name():
    definition = "本批包含早期和晚期。"
    statement = "末段 yield = 5."
    row = scenario(relation="local_identity", source=definition + "\n" + statement, alias="末段", target="晚期",
                   display="蓝批晚期", definition=definition, statement=statement)["origin"]
    assert row["target"]["status"] == "unknown" and row["direct"]["label"] == "末段"


def test_valid_name_elsewhere_in_source_cannot_resolve_this_observation():
    source = "The North station uses badge R.\nR yield = 7."
    row = scenario(relation="local_identity", source=source, alias="R", target="North station",
                   definition="The North station uses badge R.", statement="R yield = 7.",
                   mention_position=source.index("R"))["origin"]
    assert row["direct"]["reason"] == "name_evidence_unresolved"


@pytest.mark.parametrize("overlap", [True, False])
def test_overlapping_alias_definitions_suspend_both_targets(overlap):
    next_heading = "" if overlap else "# Other\n"
    source = ("# Trial\nNorth works used Q.\nQ yield = 7.\n" + next_heading +
              "South works used Q.\nQ yield = 9.")
    rows = scenario(relation="reusable_alias", source=source, alias="Q", target="North works",
                    definition="North works used Q.", statement="Q yield = 7.", scope=["# Trial"],
                    later=[("second", "Q yield = 9.")],
                    second_definition=("South works", "South works used Q.", "# Trial" if overlap else "# Other"))
    if overlap:
        assert {r["direct"]["reason"] for r in rows.values()} == {"competing_name_interpretations"}
    else:
        assert [r["direct"]["label"] for r in rows.values()] == ["North works", "South works"]


def test_alias_judgment_cannot_turn_a_denial_into_an_assignment():
    source = "The North station uses badge R.\nR did not reach a yield of 7."
    row = scenario(relation="local_identity", source=source, alias="R", target="North station",
                   definition="The North station uses badge R.", statement="R did not reach a yield of 7.")["origin"]
    assert row["direct"]["status"] == row["fit"]["status"] == "unknown"


@pytest.mark.parametrize("negative", [False, True])
def test_definition_polarity_changes_name_resolution(negative):
    definition = f"The North station {'never used' if negative else 'used'} the tag Q."
    source = definition + "\nQ yield = 3."
    row = scenario(relation="local_identity", source=source, alias="Q", target="North station",
                   definition=definition, statement="Q yield = 3.")["origin"]
    assert (row["target"]["label"] == "North station") == (not negative)


@pytest.mark.parametrize("ambiguous", [False, True])
def test_ordinary_reuse_cannot_take_an_adjacent_owners_value(ambiguous):
    reading = "T yield = 3 and Q yield = 4." if ambiguous else "Q yield = 3."
    source = "# Trial\nNorth works used Q.\nQ yield = 6.\n" + reading
    row = scenario(relation="reusable_alias", source=source, alias="Q", target="North works",
                   definition="North works used Q.", statement="Q yield = 6.", scope=["# Trial"],
                   later=[("second", reading)], as_extracted=True)["second"]
    if ambiguous:
        assert row["target"]["status"] == "unknown"
    else:
        assert row["target"]["label"] == "North works"


def test_separately_cited_assignment_can_resolve_inside_a_multi_owner_sentence():
    source = "# Trial\nNorth works used Q.\nT yield = 3 and Q yield = 4."
    row = scenario(relation="local_identity", source=source, alias="Q", target="North works",
                   definition="North works used Q.", statement="Q yield = 4.")["origin"]
    assert row["target"]["label"] == "North works"


@pytest.mark.parametrize("text,citation,admit", [
    ("Q yield = 3 and purity = 6, respectively.", "Q yield = 3", True),
    ("Q yield = 3，纯度为 6，分别列示。", "Q yield = 3", True),
    ("Q yield and purity were 3 and 6, respectively.", "Q yield and purity were 3", False),
    ("P and Q yield = 3 and 6, respectively.", "Q yield = 3", False),
    ("Q yield = 3 and purity = 6, respectively, were not measured.", "Q yield = 3", False),
    ("Q yield ≠ 3 and purity = 6, respectively.", "Q yield ≠ 3", False),
])
def test_parallel_statements_need_an_independent_assignment(text, citation, admit):
    definition = "North works used code Q."
    row = scenario(relation="local_identity", source=definition + "\n" + text, alias="Q", target="North works",
                   definition=definition, statement=citation)["origin"]
    assert (row["target"]["label"] == "North works") == admit
    assert (row["fit"]["status"] == "supported") == admit


def test_hidden_endpoint_quarantines_the_name_decision_independently():
    source = "Delta station measured 7."
    index = SourceIndex(source)
    passage = index.passages[0]["passage_id"]
    quote = {"passage_id": passage, "quote": source}
    mention = {"passage_id": passage, "quote": "Delta station"}
    step = parse_step(json.dumps({"action": "decide", "question": "", "bindings": [
        {"observation_id": "v", "subject_id": "shown", "status": "supported", "evidence": [quote],
         "reason": "Measured by this station.", "target_match": "supported",
         "name_resolution": {"relation": "local_identity", "mention": mention, "target_mention": mention,
              "target_id": "hidden", "evidence": [quote], "scope_evidence": [], "competitor_ids": [],
              "reason": "Selected an object omitted from this request."}}]}))
    reviews = []
    _, _, updates, _ = apply_decision(step, source_text=source, source_hash="synthetic",
        batch=[{"observation_id": "v"}], objects=[{"object_id": "shown"}, {"object_id": "hidden"}],
        relations=[], entries={}, visible_passages=index.passages, source_index=index,
        shown_objects={"shown"}, review_items=reviews)
    assert updates["v"]["subject_id"] == "shown" and "name_resolution" not in updates["v"]
    assert [r["proposal_type"] for r in reviews] == ["name_resolution"]


@pytest.mark.parametrize("changed", [{"observation_id": "another"}, {"target_id": "absent"},
                                      {"scope_evidence_ids": []}, {"mention_id": "missing"},
                                      {"competitor_ids": ["target"]}, {"semantic_status": "human_approved"}])
def test_damaged_archived_receipt_is_explicitly_rejected(changed):
    receipt = {"observation_id": "v", "source_id": "from", "target_id": "target", "mention_id": "m",
               "target_mention_id": "t", "evidence_ids": ["proof"], "scope_evidence_ids": ["scope"],
               "competitor_ids": [], "relation": "reusable_alias", "reason": "Named in this section.",
               "semantic_status": "model_proposed"}
    obs = {"observation_id": "v", "subject_id": "from", "name_resolution": receipt}
    validate_name_receipt(obs, {"from", "target"}, {"proof", "scope", "m", "t"})
    with pytest.raises(ValueError, match="name resolution"):
        validate_name_receipt({**obs, "name_resolution": {**receipt, **changed}},
                              {"from", "target"}, {"proof", "scope", "m", "t"})


@pytest.mark.parametrize("transposed", [False, True])
@pytest.mark.parametrize("use_header", [False, True])
def test_table_name_must_be_cited_from_value_owner_cell(transposed, use_header):
    grid = [["ID", "yield"], ["Y", "7"]]
    if transposed:
        grid = zip(*grid)
    definition = "The shop assigned code Y to River Works."
    source = definition + "\n<table>" + "".join(
        "<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in grid) + "</table>"
    cells = {e["quote"]: {**e, "selected_start": e["start"], "selected_end": e["end"], "selected_quote": e["quote"]}
             for e in build_table_input(source)["entries"] if e["kind"] == "cell"}
    def span(key, start, quote):
        return {"evidence_id": key, "start": start, "end": start + len(quote), "quote": quote}
    name_cell = cells["ID" if use_header else "Y"]
    evidence = [span("from", name_cell["start"], name_cell["quote"]),
                span("to", source.index("River Works"), "River Works"), span("defined", 0, definition)]
    obs = {"observation_id": "v", "entity": "Y", "subject_id": "river", "binding_status": "supported",
           "binding_evidence_ids": ["from"], "target_match": "supported", "record_type": "yield",
           "property_name": "yield", "value": "7", "source_kind": "table", "source_record": "/table_results/0/candidates/0",
           "source_evidence": [{"fields": ["value"], "quote": "7", "span": cells["7"]}],
           "name_resolution": {"observation_id": "v", "source_id": "river", "target_id": "river",
               "relation": "local_identity", "mention_id": "from", "target_mention_id": "to",
               "evidence_ids": ["from", "defined"], "scope_evidence_ids": [], "competitor_ids": []}}
    data = {"objects": [{"object_id": "river", "name": "River Works", "scope": "",
                         "is_table_subject": True, "mention_evidence_ids": ["to"]}],
            "observations": [obs], "evidence": evidence, "table_subject": "unit",
            "task": {"fields": {"yield": "yield"}}}
    raw = {"table_results": [{"table_id": "table-0001", "candidates": [
        {"scope": "individual", "resolved_fields": {"entity": [cells["Y"]], "value": [cells["7"]]}}]}]}
    row = resolve_observations(source, raw, data, identity_fields=["name"], row_subject="unit")["v"]
    if use_header:
        assert row["target"]["status"] == "unknown" and row["direct"]["label"] == "Y"
    else:
        assert row["target"]["status"] == "supported" and row["direct"]["label"] == "River Works"
