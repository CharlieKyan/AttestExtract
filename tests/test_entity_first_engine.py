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
    return {"field": "load", "target_id": owner, "value": value, "value_source": cite(w, value, 0),
            "property_source": cite(w, "load", 0), "source_subject": name, "subject_source": cite(w, name, 0),
            "link_source": cite(w, line), "fit_source": cite(w, line),
            "purpose": "measurement", "fit": "supported", "reason": "Measured load."}


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
    assert result["fit"]["status"] != "supported"


def test_nonnumeric_unit_substring_does_not_create_a_second_value():
    def reply(p):
        w = p["windows"][0]
        if p["stage"] == "inventory":
            return {"objects": [card(w)]}
        return {"facts": [fact(w, p["objects"][0]["object_id"], line=w["text"], value="8")],
                "nominations": []}

    report = run("A load 80.", Backend(reply))
    item, = report["observations"]
    assert item["value"] == "8" and not item["qualified"]
    assert item["value_source"] is None
    assert report["calls"][1]["fact_decisions"][0]["observation_id"] == item["observation_id"]


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


def test_homonyms_with_distinct_qualifiers_register_in_the_same_attribute_pass():
    text = "# East\n\nEast A load 80.\n\n# West\n\nWest A load 60."

    def reply(p):
        w = p["windows"][0]
        if p["stage"] == "inventory":
            return {"objects": [card(w, scope="East", proof="East A load 80.")]}
        eastern_id = next(o["object_id"] for o in p["objects"] if o["scope"] == "East")
        if "West A" in w["text"]:
            return {"nominations": [card(w, scope="West", proof="West A load 60.")],
                    "facts": [dict(fact(w, None, value="60", line="West A load 60."), nomination_key="A")]}
        return {"facts": [fact(w, eastern_id, line="East A load 80.")], "nominations": []}

    r = run(text, Backend(reply), max_chars=32)
    assert [(v["value"], v["qualified"]) for v in r["observations"]] == [("80", True), ("60", True)]
    assert r["nominations"][0]["status"] == "new_object"
    assert [o["qualifier"] for o in r["objects"]] == ["East", "West"]
    assert r["observations"][0]["owner_id"] != r["observations"][1]["owner_id"]


def test_late_nomination_keeps_previous_observation_for_separate_review():
    def reply(p):
        stage = p["stage"]
        w = p["windows"][0]
        if stage == "inventory":
            return {"objects": []}
        if "installed" in w["text"]:
            return {"nominations": [card(w, name="B")], "facts": []}
        return {"nominations": [], "facts": [fact(w, None, name="B", value="60", line="B load 60.")]}

    b = Backend(reply)
    r = run("B load 60.\n\nUnit B was installed.", b, max_chars=32)
    assert b.sent == ["inventory", "attributes", "attributes"]
    assert r["objects"][0]["label"] == "B"
    assert r["observations"][0]["entity"] == "B" and not r["observations"][0]["qualified"]
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

def test_small_roster_is_visible_in_later_blocks_even_with_zero_literal_hits():
    text = "# Intake\n\nUnit A is registered.\n\n# Result\n\nNo measurement."
    shown = []
    def reply(packet):
        w = packet["windows"][0]
        if packet["stage"] == "inventory":
            return {"objects": [card(w, name="Unit A")]}
        shown.append([entry["label"] for entry in packet["objects"]])
        return {"nominations": [], "facts": []}
    r = run(text, Backend(reply), max_chars=25)
    assert len(shown) >= 2 and all(labels == ["Unit A"] for labels in shown)
    assert not r["observations"]
    assert sum(u["total_tokens"] for u in r["budget"]["usage_by_stage"].values()) == r["budget"]["usage"]["total_tokens"]


def test_crowded_roster_does_not_drop_one_competitor_to_fit_the_model():
    from attestextract.entity_first import _attribute_packet

    class Capacity:
        def __init__(self, slots):
            self.slots = slots
        def messages(self, stage, packet):
            return packet
        def fits(self, packet, stage):
            return len(packet["objects"]) <= self.slots

    def record(oid, label, qualifier, aliases=()):
        return {"object_id": oid, "qualifier": qualifier, "label": label, "kind": "instance",
                "section": "other", "aliases": [{"name": name} for name in aliases]}

    roster = {r["object_id"]: r for r in (record("east", "East device", "East", ["Device 2"]),
                                       record("west", "West device", "West", ["Device 2"]),
                                       record("unrelated", "Device 99", None))}
    block = {"kind": "text", "scope": "current", "start": 0, "text": "Device 2 measured 80.",
             "context": [], "block_id": "current"}
    both = _attribute_packet(roster, block, Capacity(2), True)
    assert {v["object_id"] for v in both["objects"]} == {"east", "west"}
    assert not _attribute_packet(roster, block, Capacity(1), True)["objects"]


def test_nomination_reuses_unique_registered_identity_across_sections():
    text = "# Registration\n\nDevice A is a station.\n\n# Output\n\nDevice A (A17) load 80."
    def reply(packet):
        w = packet["windows"][0]
        if packet["stage"] == "inventory":
            return {"objects": [card(w, "Device A")]}
        if "A17" not in w["text"]:
            return {"nominations": [], "facts": []}
        proposal = card(w, "Device A", "A17", "Device A (A17) load 80.")
        return {"nominations": [proposal], "facts": [dict(fact(w, None, name="Device A",
                line="Device A (A17) load 80."), nomination_key="Device A")]}
    report = run(text, Backend(reply), max_chars=38)
    one, = report["objects"]
    assert report["nominations"][0]["object_id"] == one["object_id"]
    claim, = report["observations"]
    assert claim["qualified"] and claim["owner_id"] == one["object_id"]


def test_one_cell_claimed_as_two_fields_is_held_once_with_both_proposals():
    source = "| Asset | Load | Weight |\n|---|---|---|\n| Unit Q | 80 | 17 |"
    def reply(packet):
        w = packet["windows"][0]
        if packet["stage"] == "inventory":
            return {"objects": [card(w, name="Unit Q")]}
        base = fact(w, packet["objects"][0]["object_id"], name="Unit Q", line="Unit Q")
        base.update(property_source=cite(w, "Load"), fit_source=cite(w, "Load"))
        return {"nominations": [], "facts": [base, {**base, "field": "weight", "property_source": cite(w, "Weight")}]}
    report = run_entity_first(source, {"load": "Reported load", "weight": "Reported weight"}, "individual assets",
                              Backend(reply))
    obs, = report["observations"]
    assert not obs["qualified"] and obs["reason"] == "conflicting_assignments"
    first, second = report["calls"][-1]["fact_decisions"]
    assert first["observation_id"] == second["observation_id"] == obs["observation_id"]
    assert (first["action"], second["action"]) == ("retained", "conflicting")
    assert [d["assessment"]["property_source"]["quote"] for d in (first, second)] == ["Load", "Weight"]


@pytest.mark.parametrize("stage", ["inventory", "rescue"])
@pytest.mark.parametrize("transposed", [False, True])
def test_table_identity_uses_its_separate_role_header_on_either_axis(stage, transposed):
    rows = [("Specimen", "Load"), ("S7", "80")]
    if transposed:
        rows = list(zip(*rows))
    source = "<table>" + "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in rows) + "</table>"
    def reply(packet):
        w = packet["windows"][0]
        def cell(value):
            ref = next(k for k, v in w["cells"].items() if v == value)
            return {"window": f"{w['id']}@{ref}"}
        identity = {"key": "s", "label": "S7", "mention": cell("S7"), "qualification": cell("Specimen")}
        wrong = {"key": "wrong", "label": "S7", "mention": cell("S7"), "qualification": cell("Load")}
        if packet["stage"] == "inventory":
            return {"objects": [identity, wrong] if stage == "inventory" else []}
        return {"nominations": [identity, wrong] if stage == "rescue" else [], "facts": []}
    report = run(source, Backend(reply))
    obj, = report["objects"]
    assert obj["label"] == "S7" and obj["stage"] == stage
    assert [s["quote"] for s in obj["evidence"]] == ["S7", "Specimen"]
    rejected = report["issues"] if stage == "inventory" else [n for n in report["nominations"] if not n["object_id"]]
    assert [entry["reason"] for entry in rejected] == ["invalid_identity_evidence"]


def test_later_registered_homonym_invalidates_only_earlier_unqualified_selection():
    source = "R1 load 80.\n\nEast R1 load 70.\n\nWest R1 load 60."
    def reply(packet):
        w = packet["windows"][0]
        line = w["text"].strip()
        if packet["stage"] == "inventory":
            return {"objects": [card(w, name="R1")]}
        if line.startswith("R1"):
            oid = packet["objects"][0]["object_id"]
            return {"nominations": [], "facts": [fact(w, oid, name="R1", line=line)]}
        place, value = ("West", "60") if "West" in line else ("East", "70")
        known = card(w, name="R1", scope=place)
        claim = dict(fact(w, None, name="R1", line=line, value=value), nomination_key=known["key"])
        return {"nominations": [known], "facts": [claim]}
    report = run(source, Backend(reply), max_chars=20)
    early, east, west = report["observations"]
    assert (early["direct"]["label"], early["target"]["status"], early["qualified"]) == ("R1", "unknown", False)
    assert {c["qualifier"] for c in early["candidates"]} == {"East", "West"}
    assert east["qualified"] and west["qualified"] and east["owner_id"] != west["owner_id"]
    first = next(c for c in report["calls"] if c["stage"] == "attributes")
    assert first["fact_decisions"][0]["assessment"]["target"]["status"] == "supported"


def test_same_source_identity_with_conflicting_kind_is_not_overwritten():
    def reply(packet):
        w = packet["windows"][0]
        if packet["stage"] == "inventory":
            identity = card(w, name="Study 7")
            return {"objects": [{**identity, "kind": "instance"}, {**identity, "kind": "group"}]}
        return {"nominations": [], "facts": []}
    report = run("Study 7 was registered.", Backend(reply))
    obj, = report["objects"]
    assert obj["kind"] == "instance"
    assert report["issues"][0]["reason"] == "conflicting_identity_at_same_mention"
