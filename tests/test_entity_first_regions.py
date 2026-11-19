"""A mixed table may contain independent identities, target values and material held for inspection."""
import json

import pytest

from attestextract import extract
from attestextract.models import BackendResponse


class TableAnswers:
    name = "synthetic"
    mode = "replay"

    def __init__(self, case):
        self.case = case
        self.stages = []

    def generate(self, request):
        packet = json.loads(request.messages[1]["content"])
        window = packet["windows"][0]
        self.stages.append(packet["stage"])
        assert window["cells"]["r1c0"] == "Rack A"

        def cell(ref):
            return {"window": window["id"] + "@" + ref}

        identity = {"key": "rack", "label": "Rack A", "kind": "instance",
                    "mention": cell("r1c0"), "qualification": cell("r1c0")}
        if packet["stage"] == "inventory":
            answer = {"objects": []}
        elif packet["stage"] == "confirm":
            answer = {"decisions": [{"key": "rack", "status": "new_object", "evidence": cell("r1c0"),
                                     "reason": "Named rack in the source."}]}
        else:
            answer = {"facts": [], "nominations": [], "review_regions": [cell("r1c1")]}
            if self.case != "empty":
                answer["nominations"].append(identity)
            if self.case == "mixed":
                answer["facts"].append({"field": "count", "value": "7", "owner": "rack", "value_source": cell("r1c2"),
                                        "owner_source": cell("r1c0"), "property_source": cell("r0c2"),
                                        "link_source": cell("r1c0"), "fit_source": cell("r0c2"),
                                        "direct": True, "fit": "supported", "purpose": "measurement",
                                        "reason": "Count reported for this rack."})
            if self.case == "invalid":
                answer["review_regions"].append({"window": "block-09999"})
        return BackendResponse(text=json.dumps(answer), usage={"total_tokens": 10})


@pytest.mark.parametrize("case", ["mixed", "identity", "empty", "invalid"])
def test_same_local_request_delivers_whichever_outputs_it_contains(case):
    source = "| Rack | Notes | Count | More notes |\n| --- | --- | --- | --- |\n| Rack A | 7 | 7 | stored |"
    backend = TableAnswers(case)
    result = extract(source, field_list={"rack": "Rack name", "count": "Count"}, prompt_description="Collect counts",
                     backend=backend, subject_strategy="entity-first", table_subject="individual racks",
                     dataset_options={"subject_label_field": "rack"})
    report = result.entity_first
    block, = report["blocks"]
    assert backend.stages == (["inventory", "attributes"] if case == "empty" else ["inventory", "attributes", "confirm"])
    assert report["status"] == ("partial" if case == "invalid" else "complete")
    left, right = source.index("7"), source.rindex("7")
    assert block["review_regions"] == [{"start": left, "end": left + 1}]
    assert result.dataset["lineage"]["review_regions"] == [{"block_id": block["block_id"], "start": left, "end": left + 1}]
    assert [o["label"] for o in report["objects"]] == ([] if case == "empty" else ["Rack A"])
    if case == "mixed":
        assert result.dataset["rows"] == [{"rack": "Rack A", "count": "7"}]
        assert report["observations"][0]["value_source"]["start"] == right
        assert block["attribute_outcome"] == "facts_reported"
    else:
        assert result.dataset["rows"] == []
        assert block["attribute_outcome"] == "no_target_facts_reported"
    assert report["summary"]["main_rows_with_attributes"] == int(case == "mixed")
    assert report["summary"]["empty_roster_dispatches"] == 1
