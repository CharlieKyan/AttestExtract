"""Assignments, local codes and source scope across a complete file-backed dataset."""

import pytest

from niceextract import assemble_dataset, extract, query_dataset
from niceextract.analysis import _input_observations
from niceextract.backends import ReplayBackend
from niceextract.dataset_extraction_owner import ExtractionOwners


def test_parenthetical_registry_identifier_preserves_attributes_and_scoped_lookup():
    site = "East Bay Kiln was selected"
    declaration = "Kiln (K2)"
    result = "Kiln reached 80 C"
    other = "West Bay Kiln reached 70 C"
    source = f"{site}, then Unit B was inspected.\n\nAt East Bay, {declaration} ran overnight. {result}.\n\n{other}."
    fields = {"key": "Furnace name or code", "location": "Facility of the furnace", "temp": "Temperature"}
    proposals = []
    for field, owner, value, quote in [
        ("key", "Kiln", "Kiln", site), ("location", "Kiln", "East Bay", site),
        ("key", "Kiln", "K2", declaration), ("temp", "Kiln", "80", result),
        ("temp", "Kiln", "70", other), ("key", "Unit B", "Unit B", "Unit B was inspected"),
    ]:
        values = {"entity": owner, "value": value, "unit": None, "conditions": None}
        proposals.append({"record_type": field, "name_handling": "as_reported", "values": values,
                          "quotes": {k: quote if v is not None else "" for k, v in values.items()}})
    run = extract(source, prompt_description="Read furnace data.", field_list=fields,
                  table_subject="Named furnaces",
                  backend=ReplayBackend([{"records": proposals}], output_protocol="flat-v1"))
    evidence = {}
    items, rejected = _input_observations(source, run, evidence)
    identity = next(o for o in items if o["value"] == "K2")
    proof, = identity["evidence_ids"]
    for item in items:
        item.update(binding_status="as_extracted", subject_id=None, target_match="supported", binding_evidence_ids=[])
    identity.update(binding_status="supported", subject_id="east-02", binding_evidence_ids=[proof])
    registry = {"object_id": "east-02", "name": declaration, "scope": "East Bay Kiln",
                "kind": "instance", "is_table_subject": True, "mention_evidence_ids": [proof],
                "semantic_status": "model_proposed"}
    analysis = {**run.analysis, "objects": [registry], "observations": items,
                "jobs": [{"job_id": "id-job", "observation_ids": [identity["observation_id"]], "status": "complete"}],
                "quarantined": rejected, "evidence": list(evidence.values())}
    dataset = assemble_dataset(source, run.model_dump(mode="json"), analysis, row_subject="Named furnaces",
                               subject_label_field="key")
    for lookup in ("K2", "Kiln (K2)", "east-02"):
        hits = {(o["field"], o["value"]) for o in query_dataset(dataset, lookup)}
        assert hits == {("location", "East Bay"), ("temp", "80")}
    assert next(o for o in dataset["observations"] if o["value"] == "70")["direct"]["id"] != "east-02"


@pytest.mark.parametrize("text,owner,value,supported", [
    ("Line A produced 80 boxes, then Line B was inspected.", "Line A", "80", True),
    ("Line A produced 80 boxes, Line B produced 60 boxes.", "Line A", "60", False),
    ("Line A produced 80 boxes, Line B produced 60 boxes.", "Line B", "60", True),
    ("Line A, Line B jointly produced 80 boxes.", "Line B", "80", False),
    ("Line A, Line B produced 80 boxes.", "Line B", "80", False),
    ("Line A did not produce 80 boxes, Line B was inspected.", "Line A", "80", False),
])
def test_complete_cited_clause_keeps_only_its_own_subject(text, owner, value, supported):
    snapshot = {"table_subject": "Production lines", "evidence": [], "objects": [], "relations": [],
                "observations": [{"entity": s, "source_evidence": []} for s in ("Line A", "Line B")]}
    owners = ExtractionOwners(text, snapshot, True)
    entry = {"fields": ["entity", "value"], "span": {"start": 0, "end": len(text)}, "quote": text}
    obs = {"entity": owner, "value": value, "unit": "boxes", "source_evidence": [entry]}
    assert bool(owners._extracted_proof(obs, owner)) == supported


def test_each_attested_line_keeps_its_own_output_in_the_main_table():
    clauses = ("Line A produced 80 boxes", "Line B produced 60 boxes")
    source = ", ".join(clauses) + "."
    records = []
    for owner, count, clause in zip(("Line A", "Line B"), ("80", "60"), clauses, strict=True):
        for field, value, quote in [("n", owner, owner), ("quantity", count, clause)]:
            values = {"entity": owner, "value": value, "unit": None, "conditions": None}
            records.append({"record_type": field, "name_handling": "as_reported", "values": values,
                            "quotes": {k: quote if v is not None else "" for k, v in values.items()}})
    run = extract(source, prompt_description="Report box counts.", field_list={"n": "Line", "quantity": "Box count"},
                  table_subject="Named production lines", dataset_options={"subject_label_field": "n"},
                  backend=ReplayBackend([{"records": records}], output_protocol="flat-v1"))
    assert {(r["n"], r["quantity"]) for r in run.dataset["rows"]} == {("Line A", "80"), ("Line B", "60")}


@pytest.mark.parametrize("reported_code,match", [("M1", False), ("M2", True)])
def test_second_registry_declaration_can_compete_without_a_verified_identity_observation(reported_code, match):
    from niceextract.dataset_identity import RegisteredDesignations

    first = "Unit Alpha (M1)"
    second = f"Unit Beta ({reported_code})"
    result = "M1 mass was 10 mg"
    source = f"At East, {first} was fitted. At West, {second} was fitted. {result}."

    def span(quote):
        start = source.index(quote)
        return {"start": start, "end": start + len(quote), "quote": quote}

    objects = [
        {"object_id": "east", "name": first, "scope": "East", "is_table_subject": True, "mention_evidence_ids": ["e"]},
        {"object_id": "west", "name": "Unit Beta (M1)", "scope": "West", "is_table_subject": True,
         "mention_evidence_ids": ["w"]},
    ]
    identity = {"record_type": "device", "entity": "Unit Alpha", "value": "M1", "subject_id": "east",
                "binding_status": "supported", "target_match": "supported", "binding_evidence_ids": ["e"],
                "source_evidence": [{"span": span(first), "fields": ["entity", "value"]}]}
    registry = RegisteredDesignations(source, {"objects": objects, "observations": [identity],
                "evidence": [{"evidence_id": "e", **span(first)}, {"evidence_id": "w", **span(second)}]}, "device")
    endpoint = {"id": "mention-M1", "label": "M1", "status": "supported", "evidence": [span(result)]}
    obs = {"value": "10", "unit": "mg", "direct": dict(endpoint), "target": dict(endpoint)}
    registry.apply(obs, {})
    assert obs["target"]["id"] == ("east" if match else "mention-M1")
    assert obs["direct"]["id"] == obs["target"]["id"]
