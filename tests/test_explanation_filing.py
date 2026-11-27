import json
import csv
import pytest

from niceextract import BackendResponse, extract, query_dataset
from niceextract.backends import ReplayBackend
from niceextract.cli import main
from niceextract.dataset_io import save_dataset
from test_source_statement_protocol import FixtureBackend


MEASUREMENT = "Bearing B7 in motor M4 reached 60 °C."
FIELDS = {"name": "The row object's name", "temperature": "Temperature of a bearing on this machine"}


def reading(entity="Bearing B7", quote=MEASUREMENT, relation=None):
    return {"record_type": "temperature", "values": {"entity": entity, "value": "60", "unit": "°C", "conditions": None},
            "quotes": {"entity": quote, "value": quote, "unit": quote, "conditions": ""},
            "statement_basis": "reported", "reported_use": "measured", "source_property": "temperature",
            "name_handling": "related" if relation else "as_reported", "row_relation": relation}


def filing(target="motor M4", quote=MEASUREMENT, application="included", contains=True):
    return {"target": target, "explanation": f"The named bearing is installed in {target}.",
            "field_applicability": application, "target_basis": "reported",
            "target_contains_direct": contains, "evidence": [{"quote": quote, "ref": None}]}


def view(records, text=MEASUREMENT, row_subject="Named motors", fields=FIELDS):
    if not row_subject:
        records = [{k: v for k, v in r.items() if k not in {"name_handling", "row_relation"}} for r in records]
    options = {"subject_label_field": "name"}
    if row_subject:
        options["row_subject"] = row_subject
    return extract(text, prompt_description="Keep readings at the measured object's scope.", field_list=fields,
                   backend=ReplayBackend([{"records": records}]), observation_protocol="source-v2",
                   dataset_options=options, table_mode="text").dataset


def test_bearing_itself_without_a_requested_parent():
    dataset = view([reading()], row_subject=None)
    obs, = query_dataset(dataset, "Bearing B7")
    assert (obs["query_scope"], obs["target"]["label"], obs["value"]) == ("self", "Bearing B7", "60")


def test_bearing_under_motor_retains_direct_object_and_explanation(tmp_path, capsys):
    dataset = view([reading(relation=filing())])
    child, = query_dataset(dataset, "motor M4")
    direct, = query_dataset(dataset, "Bearing B7")
    assert (child["query_scope"], direct["query_scope"]) == ("descendant", "self")
    assert child["observation_id"] == direct["observation_id"]
    assert child["query_path"][0]["evidence"][0]["quote"] == MEASUREMENT
    assert not query_dataset(dataset, "motor M4", self_only=True)
    path = save_dataset(dataset, tmp_path / "data")
    assert main(["query-dataset", "--dataset", str(path), "--subject", "motor M4"]) == 0
    assert "下属 | Bearing B7" in capsys.readouterr().out
    assert main(["query-dataset", "--dataset", str(path), "--subject", "motor M4", "--self-only"]) == 0
    assert not capsys.readouterr().out
    assert "The named bearing is installed in motor M4." in (path / "main.csv").read_text("utf-8-sig")


def test_bearing_temperature_cannot_fill_whole_machine_mean():
    dataset = view([reading(relation=filing(application="excluded"))],
                   fields={**FIELDS, "temperature": "Mean temperature of the entire motor"})
    obs, = dataset["observations"]
    assert (obs["value"], obs["entity"], obs["field_excluded"]) == ("60", "Bearing B7", True)
    assert all(row["temperature"] is None for row in dataset["rows"])
    assert not query_dataset(dataset, "motor M4")
    assert query_dataset(dataset, "Bearing B7", exploratory=True)[0]["observation_id"] == obs["observation_id"]


def test_competing_motors_do_not_become_ancestors():
    text = "Bearing B7 reached 60 °C. Records list motor M4 and motor M9 as possible owners."
    relation = {"alternatives": [filing(name, text) for name in ("motor M4", "motor M9")]}
    r = {**reading(quote=text, relation=relation), "name_handling": "uncertain"}
    dataset = view([r], text)
    obs, = query_dataset(dataset, "motor M4")
    assert obs["query_scope"] == "pending" and len(obs["filing_subjects"]) == 2
    assert not obs["target"]["id"]


def test_cross_object_without_named_row_is_answered_by_targeted_document_lookup():
    source = "Bearing B7 reached 60 °C.\n\nService register: Bearing B7 is installed in motor M4."
    r = {**reading(quote="Bearing B7 reached 60 °C."), "name_handling": "related"}

    class Backend(FixtureBackend):
        def generate(self, request):
            if not request.chunk_id.startswith("verify-"):
                return super().generate(request)
            self.requests.append(request)
            payload = json.loads(request.messages[-1]["content"])
            obs, = payload["observations"]
            assert payload["filing_question"]
            if not payload["lookup_history"]:
                return BackendResponse(text=json.dumps({"action": "search", "query": "Service register", "question": "Which motor?"}))
            found = payload["lookup_history"][0]["result"]["matches"][0]
            quote = {"passage_id": found["passage_id"], "quote": "Bearing B7 is installed in motor M4."}
            return BackendResponse(text=json.dumps({"action": "decide", "question": "Which motor?",
                "objects": [{"object_id": "motor", "name": "motor M4", "kind": "instance", "scope": "",
                             "is_table_subject": True, "mentions": [quote]}],
                "bindings": [{"observation_id": obs["observation_id"], "subject_id": None, "status": "unknown",
                              "evidence": [], "reason": "The direct name is in the reading.", "target_match": "supported"}],
                "relations": [{"source_kind": "observation", "source_id": obs["observation_id"], "target_id": "motor",
                               "explanation": "The service register identifies its motor.", "target_basis": "reported",
                               "field_applicability": "included", "target_contains_direct": True, "evidence": [quote]}]}))

    backend = Backend({"records": [r]})
    result = extract(source, prompt_description="Report measured temperatures.", field_list=FIELDS,
                     backend=backend, backend_identity="targeted-filing-test", observation_protocol="source-v2",
                     table_subject="Named motors", dataset_options={"subject_label_field": "name"},
                     table_mode="text", max_analysis_calls=2)
    assert result.analysis["budget"]["reserved"] == 2
    obs, = query_dataset(result.dataset, "motor M4")
    assert (obs["entity"], obs["value"], obs["query_scope"]) == ("Bearing B7", "60", "descendant")


def test_hierarchy_traverses_originally_anchored_containment_only():
    first = "Bearing B7 belongs to motor M4 in plant P21."
    second = "Meter L2 monitored motor M4 and recorded 60 °C."
    text = first + " " + second
    readings = [reading(quote=text, relation=filing("motor M4", first)),
                reading("motor M4", text, filing("plant P21", first)),
                reading("Meter L2", text, {**filing("motor M4", second, contains=False),
                                          "explanation": "Meter L2 is used to monitor motor M4."})]
    dataset = view(readings, text)
    found = {o["entity"]: o for o in query_dataset(dataset, "plant P21")}
    assert set(found) == {"Bearing B7", "motor M4"}
    assert [step["parent"] for step in found["Bearing B7"]["query_path"]] == ["plant P21", "motor M4"]
    assert {o["entity"]: o["query_scope"] for o in query_dataset(dataset, "motor M4")} == {
        "Bearing B7": "descendant", "motor M4": "self", "Meter L2": "associated"}
    assert not any(p["subject_label"] == "plant P21" and len(p["observations"]) == 0
                   for p in dataset["lineage"]["rows"])


def test_unmapped_row_subject_has_its_own_csv_column_and_decisive_link_needs_no_call(tmp_path):
    backend = FixtureBackend({"records": [reading(relation=filing())]})
    result = extract(MEASUREMENT, prompt_description="Keep a motor's bearing readings.",
                     field_list={"temperature": "Temperature of this motor's bearing"}, backend=backend,
                     backend_identity="decisive-reading", table_subject="Named motors", observation_protocol="source-v2")
    assert result.analysis["budget"]["reserved"] == 0 and len(backend.requests) == 1
    path = save_dataset(result.dataset, tmp_path / "delivery")
    with (path / "main.csv").open(encoding="utf-8-sig", newline="") as stream:
        row, = csv.DictReader(stream)
    assert (row["主表主体"], row["直接对象"], row["temperature"]) == ("motor M4", "Bearing B7", "60")


def test_context_only_values_are_suppressed_but_missing_citations_remain_visible():
    from niceextract.dataset_filing import FilingContext
    from copy import deepcopy

    unresolved = view([reading(quote="This note does not mention a reading.")], text="Bearing B7 was inspected.")
    obs, = unresolved["observations"]
    assert obs["citation_status"] == "unverified" and obs["row_ids"]
    snapshot = {"table_subject": None, "objects": [], "evidence": [], "relations": [], "observations": [obs]}
    context = FilingContext(MEASUREMENT, snapshot, [0, len(MEASUREMENT)], statement_protocol="source-v2")
    other = deepcopy(obs)
    other["blockers"].append({"code": "value_only_in_context"})
    context.apply(other, True, {})
    assert other["filing_suppressed"] and not other["eligible"]


def test_table_free_text_filing_preserves_direct_cell_and_row():
    from niceextract.table_routes import build_table_input

    source = "| Machine | Component | Temperature / °C |\n| --- | --- | --- |\n| motor M4 | Bearing B7 | 60 |"
    cells = {e["quote"].strip(): {"ref": e["ref"], "quote": e["quote"]} for e in build_table_input(source)["entries"]}
    item = {"record_id": "r1", "record_type": "temperature", "scope": "individual", "scope_sources": [],
            "anchors": [cells["60"]], "statement_basis": "reported", "reported_use": "measured",
            "source_property": "Temperature", "name_handling": "related",
            "row_relation": {**filing(), "evidence": [cells["motor M4"], cells["Bearing B7"]]},
            "fields": {"entity": {"value": "Bearing B7", "relation": "row", "sources": [cells["Bearing B7"]]},
                       "value": {"value": "60", "relation": "anchor", "sources": [cells["60"]]},
                       "unit": {"value": "°C", "relation": "column", "sources": [cells["Temperature / °C"]]}}}
    result = extract(source, prompt_description="Keep the temperature measured on the component.",
                     field_list={"temperature": "The machine's component temperature."},
                     backend=ReplayBackend([{"records": [item]}]), observation_protocol="source-v2",
                     dataset_options={"row_subject": "Named machines"})
    obs, = query_dataset(result.dataset, "motor M4")
    assert obs["entity"] == "Bearing B7" and obs["source_kind"] == "table"
    assert obs["filing_subjects"][0]["relation_evidence"][0]["ref"] == cells["motor M4"]["ref"]


def test_bilingual_equality_needs_an_explicit_shared_observation_decision():
    from copy import deepcopy
    from niceextract.dataset_repeat import collect_repeats

    cn = "轴承 B7 温度为 60 °C。"
    en = "Bearing B7 reached 60 °C."
    dataset = view([reading("轴承 B7", cn), reading("Bearing B7", en)], cn + "\n\n" + en, row_subject=None)
    assert len(query_dataset(dataset)) == 2
    from niceextract.dataset_repeat import _consistent
    fact = dataset["observations"][0]
    for a, b in [("-60", "60"), ("<60", ">60"), ("1×10^-3", "1×10^3")]:
        assert not _consistent({**fact, "value": a}, {**fact, "value": b})
    obs = deepcopy(dataset["observations"])
    primary, other = obs
    citations = {e["evidence_id"]: e for e in dataset["lineage"]["evidence"]}
    other["reviewed_repeats"] = [{"target_id": primary["observation_id"], "explanation": "Translated abstract.",
                                  "evidence_ids": [primary["evidence_ids"][0], other["evidence_ids"][0]]}]
    collect_repeats(obs, citations)
    assert other["corroborates"] == primary["observation_id"]
    assert primary["corroborating_observations"] == [other["observation_id"]]


@pytest.mark.parametrize("quote", ["60 °C", None, MEASUREMENT])
def test_a_number_or_an_invented_name_cannot_confirm_the_filing_or_hierarchy(quote):
    target = "never-named motor ZZ"
    relation = filing(target, quote)
    if quote is None:
        relation["evidence"] = []
    data = view([reading(relation=relation)])
    obs, = query_dataset(data, target)
    assert obs["citation_status"] == "located" and obs["value"] == "60"
    assert (obs["query_scope"], obs["delivery"], obs["target"]["status"]) == ("pending", "provisional", "unknown")
    assert obs["target"]["filing_citation_status"] == "unverified"
    assert not query_dataset(data, target, include_pending=False)
    assert "归属待定" in data["lineage"]["rows"][0]["status"]
    assert obs["filing_review_reason"]


def test_cited_target_pronoun_and_reported_source_alias_are_supported():
    intro = "The service register identifies the assembly as Main Drive."
    wording = "Bearing B7 is part of that assembly."
    value = "Bearing B7 reached 60 °C."
    rel = {**filing("主驱动", intro), "target_source_name": "Main Drive",
           "evidence": [{"quote": intro, "ref": None}, {"quote": wording, "ref": None}]}
    result = view([reading(quote=value, relation=rel)], " ".join((intro, wording, value)))
    obs, = query_dataset(result, "主驱动")
    assert obs["query_scope"] == "descendant" and obs["target"]["status"] == "supported"


def test_conflicting_owner_is_retained_and_cannot_be_reported_as_its_original_direct_value():
    from niceextract.analysis import _prepare_extraction_assignments
    from niceextract.dataset_filing import FilingContext
    from copy import deepcopy

    data = view([reading()], row_subject=None)
    obs = deepcopy(data["observations"][0])
    obs["blockers"] = [{"code": "value_owner_conflict", "message": "The value citation names B8."}]
    obs.update(binding_status="pending", name_handling="as_reported")
    _prepare_extraction_assignments([obs], None, True, statement_protocol="source-v2", source=MEASUREMENT)
    assert obs["binding_status"] == "pending" and obs["filing_review_reason"]
    snapshot = {"table_subject": "Named motors", "objects": [], "evidence": [], "relations": [], "observations": [obs]}
    FilingContext(MEASUREMENT, snapshot, [0, len(MEASUREMENT)], statement_protocol="source-v2").apply(obs, True, {})
    assert obs["direct"]["status"] == "unknown" and not obs["filing_subjects"] and not obs["eligible"]
    assert obs["value"] == "60" and obs["entity"] == "Bearing B7"


@pytest.mark.parametrize("association", [
    "Vial A1 was sampled at site North.",
    "Vial B2 was stored elsewhere, while Vial A1 was sampled at site North.",
])
def test_relationship_about_a_different_sample_cannot_file_this_measurement(association):
    measurement = "Vial B2 reached 60 °C."
    data = view([reading("Vial B2", measurement, filing("site North", association))],
                measurement + " " + association, row_subject="Sampling sites")
    obs, = query_dataset(data, "site North")
    assert obs["query_scope"] == "pending" and obs["target"]["status"] == "unknown"
    assert obs["value"] == "60" and obs["filing_review_reason"] and not obs["eligible"]


def test_unrelated_remote_registry_mention_is_not_a_relationship():
    from niceextract.filing_evidence import filing_citation_problem
    first, second = "Vial B2 reached 60 °C.", "The regional inventory lists site South."
    source = first + "\n\n" + second
    licensed = {"start": 0, "end": len(first), "quote": first}
    outside = {"start": len(first)+2, "end": len(source), "quote": second}
    record = {"entity": "Vial B2", "value": "60", "unit": "°C"}
    for cited in ([licensed], [licensed, outside]):
        reason = filing_citation_problem(source, {"target": "site South", "status": "located", "evidence": cited},
                                         record, registered_mentions=[outside])
        assert reason


def test_uncertain_self_named_parent_prefers_pending_in_display():
    r = reading(relation=filing("Bearing B7", MEASUREMENT, "uncertain"))
    data = view([r])
    row, = data["lineage"]["rows"]
    assert row["subject_label"].startswith("归属待定") and "Bearing B7" in row["subject_label"]
    assert data["observations"][0]["entity"] == "Bearing B7"


def test_three_model_cited_passages_can_connect_pronouns_to_both_endpoints():
    intro = "Station Hythe was established in 1972."
    child = "The sample was inventoried as D9."
    link = "It was collected from that station."
    reading_text = "D9 reached 60 °C."
    rel = {**filing("Station Hythe", link), "evidence": [{"quote": q, "ref": None} for q in (intro, child, link)]}
    data = view([reading("D9", reading_text, rel)], "\n\n".join((intro, child, link, reading_text)))
    obs, = query_dataset(data, "Station Hythe")
    assert obs["target"]["status"] == "supported" and not obs.get("filing_review_reason")


def test_target_cell_from_another_sample_row_cannot_establish_filing():
    from niceextract.filing_evidence import filing_citation_problem
    from niceextract.table_routes import build_table_input
    source = "| Site | Vial | Mass / mg |\n| --- | --- | --- |\n| North | A1 | 11 |\n| South | B2 | 15 |"
    cells = {e["quote"].strip(): e for e in build_table_input(source)["entries"]}
    record = {"entity": "B2", "value": "15", "unit": "mg",
              "source_evidence": [{"fields": ["value"], "ref": cells["15"]["ref"]}]}
    correct = {"target": "South", "status": "located", "evidence": [cells["B2"], cells["South"]]}
    wrong = {"target": "North", "status": "located", "evidence": [cells["B2"], cells["North"]]}
    assert filing_citation_problem(source, correct, record) is None
    assert filing_citation_problem(source, wrong, record)
