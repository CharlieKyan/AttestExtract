"""Source-checked evidence and failure boundaries for the optional two-pass strategy."""

import json

import pytest

from attestextract.entity_first import run_entity_first
from attestextract.models import BackendResponse


class Backend:
    name = "unit-test"

    def __init__(self, reply):
        self.reply = reply
        self.sent = []

    def generate(self, request):
        packet = json.loads(request.messages[1]["content"])
        self.sent.append(packet["stage"])
        reply = self.reply(packet) if callable(self.reply) else self.reply
        return BackendResponse(text=reply if isinstance(reply, str) else json.dumps(reply),
                               usage={"total_tokens": 7})


def cite(window, quote, occurrence=None):
    return {"window": window["id"], "quote": quote, "occurrence": occurrence}


def card(w, name="A", scope=None, proof=None):
    return {"label": name, "key": name, "mention": cite(w, name, 0),
            "scope": scope, "qualification": cite(w, proof or w["text"].strip())}


def fact(w, owner, name="A", line="A load 80.", value="80"):
    return {"field": "load", "owner": owner, "value": value, "value_source": cite(w, value, 0),
            "property_source": cite(w, "load", 0), "owner_source": cite(w, name, 0),
            "link_source": cite(w, line), "fit_source": cite(w, line),
            "purpose": "measurement", "direct": True, "fit": "supported", "reason": "Measured load."}


def run(source, backend, **options):
    return run_entity_first(source, {"load": "Effective applied load"}, "individual units", backend, **options)


def test_qualifier_cannot_be_borrowed_from_other_assertion():
    def reply(p):
        w = p["windows"][0]
        if p["stage"] == "inventory":
            return {"objects": [card(w)]}
        assignment = fact(w, p["objects"][0]["object_id"])
        assignment["fit_source"] = cite(w, "B effective load 4.")
        return {"facts": [assignment], "nominations": []}

    report = run("A load 80. B effective load 4.", Backend(reply))
    (result,) = report["observations"]
    assert result["owner_supported"] and not result["qualified"]
    assert result["reason"] == "field_or_qualifier_scope_unconfirmed"


def test_nonnumeric_unit_substring_does_not_create_a_second_value():
    def reply(p):
        w = p["windows"][0]
        if p["stage"] == "inventory":
            return {"objects": [card(w)]}
        return {"facts": [fact(w, p["objects"][0]["object_id"], line=w["text"], value="8")],
                "nominations": []}

    report = run("A load 80.", Backend(reply))
    assert report["observations"] == []
    assert report["issues"][0]["reason"] == "numeric_substring"
    assert report["issues"][0]["rejected_fact"]["value"] == "8"


def test_conflicting_same_source_assignments_do_not_overwrite_the_first():
    def reply(p):
        w = p["windows"][0]
        if p["stage"] == "inventory":
            return {"objects": [card(w)]}
        f = fact(w, p["objects"][0]["object_id"], line=w["text"])
        return {"facts": [f, {**f, "purpose": "model_input"}], "nominations": []}

    r = run("A load 80.", Backend(reply))
    assert len(r["observations"]) == 1
    assert r["observations"][0]["reason"] == "conflicting_assignments"
    assert not r["observations"][0]["qualified"]


@pytest.mark.parametrize("roster", [("A",), ("A", "B")])
def test_competing_clauses_cannot_supply_a_value_to_the_other_named_subject(roster):
    text = "A has mass 80 kg, B has mass 60 kg."
    def reply(p):
        w = p["windows"][0]
        if p["stage"] == "inventory":
            return {"objects": [card(w, name=name) for name in roster]}
        ids = {c["label"]: c["object_id"] for c in p["objects"]}
        wrong = {**fact(w, ids["A"], line=text, value="60"),
                 "property_source": cite(w, "mass", 1)}
        correct = {**fact(w, ids["A"], line="A has mass 80 kg"), "property_source": cite(w, "mass", 0)}
        return {"facts": [wrong, correct], "nominations": []}
    r = run(text, Backend(reply))
    assert [(v["value"], v["qualified"]) for v in r["observations"]] == [("60", False), ("80", True)]


def test_names_in_different_sections_do_not_cross_bind_or_confirm():
    text = "# East\n\nEast A load 80.\n\n# West\n\nWest A load 60."

    def reply(p):
        w = p["windows"][0]
        if p["stage"] == "inventory":
            return {"objects": [card(w, scope="East", proof="East A load 80.")]}
        eastern_id = next(o["object_id"] for o in p["objects"] if o["scope"] == "East")
        if p["stage"] == "confirm":
            return {"decisions": [{"key": "A", "status": "existing_object", "existing_id": eastern_id,
                                   "evidence": cite(w, "West A load 60."), "reason": "Identical label."}]}
        if "West A" in w["text"]:
            return {"nominations": [card(w, scope="West", proof="West A load 60.")],
                    "facts": [fact(w, "A", value="60", line="West A load 60.")]}
        return {"facts": [fact(w, eastern_id, line="East A load 80.")], "nominations": []}

    r = run(text, Backend(reply), max_chars=32)
    assert [(v["value"], v["qualified"]) for v in r["observations"]] == [("80", True), ("60", False)]
    assert r["nominations"][0]["reason"] == "unproven_existing_identity"
    assert len(r["objects"]) == 1


def test_late_nomination_revisits_only_previous_local_unassigned_measurement():
    def reply(p):
        stage = p["stage"]
        w = p["windows"][0]
        if stage == "inventory":
            return {"objects": []}
        if stage == "confirm":
            return {"decisions": [{"key": "B", "status": "new_object", "evidence": cite(w, "Unit B"),
                                   "reason": "Named study unit."}]}
        if "installed" in w["text"]:
            return {"nominations": [card(w, name="B")], "facts": []}
        owner = p["objects"][0]["object_id"] if stage == "revisit" else None
        return {"nominations": [], "facts": [fact(w, owner, name="B", value="60", line="B load 60.")]}

    b = Backend(reply)
    r = run("B load 60.\n\nUnit B was installed.", b, max_chars=32)
    assert b.sent == ["inventory", "attributes", "attributes", "confirm", "revisit"]
    assert len(r["observations"]) == 1 and r["observations"][0]["qualified"]
    assert r["observations"][0]["source_record"] == "/candidates/0"


def test_no_single_budget_is_spent_on_roster_before_reading_source():
    b = Backend({"facts": [], "nominations": []})
    r = run("A load 80.", b, max_calls=1)
    assert b.sent == ["attributes"]
    assert r["blocks"][0]["inventory"] == "budget_exhausted" and r["blocks"][0]["attributes"] == "read"
    assert r["budget"]["calls_used"] == r["budget"]["http_attempts"] == 1


def test_duplicate_json_is_rejected_and_retry_counts_against_existing_cap():
    b = Backend('{"objects":[],"objects":[]}')
    r = run("A load 80.", b, max_calls=4, format_retries=1)
    assert r["status"] == "partial" and r["objects"] == r["observations"] == []
    assert b.sent == ["inventory", "inventory", "attributes", "attributes"]
    assert r["budget"]["usage"] == {"total_tokens": 28}


def test_unreadable_source_or_transport_error_preserves_auditable_coverage():
    def broken(p):
        raise RuntimeError("unsafe provider detail")
    b = Backend(broken)
    r = run("A load 80.", b)
    assert r["blocks"][0]["inventory"] == "backend_failure"
    assert r["blocks"][0]["attributes"] == "backend_dispatch_halted"
    assert "unsafe provider detail" not in json.dumps(r)
    assert r["budget"]["calls_used"] == 1
    b = Backend({})
    r = run("A load 80.", b, input_budget=10)
    assert not b.sent and r["blocks"][0]["attributes"] == "input_budget_exceeded"


def test_adapter_reported_hidden_attempts_halt_further_dispatch():
    class Retrying:
        def generate(self, request):
            return BackendResponse(text='{"objects":[]}', attempts=2)
    r = run("A load 80.", Retrying())
    assert r["budget"]["calls_used"] == 1 and r["budget"]["http_attempts"] == 2
    assert r["blocks"][0]["inventory"] == "single_attempt_contract_violated"
    assert r["blocks"][0]["attributes"] == "backend_dispatch_halted"


def test_supplied_task_examples_are_context_and_cannot_ground_a_measurement():
    examples = [{"text": "Station Z has load 1.", "extractions": []}]
    def reply(packet):
        assert packet["task"]["examples"] == examples
        assert all("Station Z" not in w["text"] for w in packet["windows"])
        return {"objects": []} if packet["stage"] == "inventory" else {"nominations": [], "facts": []}
    report = run("A load 80.", Backend(reply), examples=examples)
    assert report["status"] == "complete"


@pytest.mark.parametrize("source", [
    "| Device | Count | Device | Count |\n|---|---|---|---|\n| A | 80 | B | 60 |",
    "| Device | A |\n|---|---|\n| Count | 80 |\n| Device | B |\n| Count | 60 |",
])
@pytest.mark.parametrize("owner,value,occurrence,expected", [
    ("A", "80", 0, True), ("B", "60", 1, True), ("A", "60", 1, False), ("B", "80", 0, False)
])
def test_parallel_parameter_blocks_have_local_owner_boundaries(source, owner, value, occurrence, expected):
    def reply(packet):
        w = packet["windows"][0]
        if packet["stage"] == "inventory":
            return {"objects": [card(w, name=n) for n in ("A", "B")]}
        ids = {e["label"]: e["object_id"] for e in packet["objects"]}
        f = fact(w, ids[owner], name=owner, value=value, line=owner)
        f.update(property_source=cite(w, "Count", occurrence), fit_source=cite(w, "Count", occurrence))
        return {"nominations": [], "facts": [f]}
    result = run(source, Backend(reply))
    assert len(result["observations"]) == 1
    assert result["observations"][0]["qualified"] is expected


def test_shared_subject_multilevel_statistic_headers_and_physical_separator():
    from attestextract.entity_first_protocol import table_aligned
    from attestextract.structure import build_structure

    def aligns(source, owner, value, property_name):
        table, = build_structure(source).tables
        def span(text):
            start = source.index(text)
            return {"start": start, "end": start + len(text), "quote": text}
        return table_aligned(table, source, span(owner), span(value), span(property_name))

    # Repeated property/statistic headers do not partition a single owner column.
    shared = ('<table><tr><th rowspan="2">Device</th><th colspan="2">Before</th><th colspan="2">After</th></tr>'
              '<tr><th>Maximum</th><th>Mean</th><th>Maximum</th><th>Mean</th></tr>'
              '<tr><td>Unit S</td><td>90</td><td>80</td><td>70</td><td>60</td></tr></table>')
    assert aligns(shared, "Unit S", "60", "After")
    split = "| Device | Count |   | Specimen | Rate |\n|---|---|---|---|---|\n| Unit S | 80 |   | Batch B | 60 |"
    assert aligns(split, "Unit S", "80", "Count")
    assert not aligns(split, "Unit S", "60", "Rate")


def test_request_source_and_responses_are_checked_for_secrets_before_storage():
    b = Backend({})
    r = run("A load 80. hidden-key", b, secrets=["hidden-key"])
    assert not b.sent and "hidden-key" not in json.dumps(r)
    b = Backend('{"objects":[],"diagnosis":"hidden-key"}')
    r = run("A load 80.", b, secrets=["hidden-key"])
    assert "hidden-key" not in json.dumps(r)
    assert r["blocks"][0]["inventory"] == "credential_in_response"
