import json

import pytest

from niceextract import extract
from niceextract.evidence import ground_quote
from niceextract.models import BackendResponse, DocumentChunk, SourceSpan
from niceextract.analysis import _route_extracted_names


def test_reported_owner_reaches_csv_without_a_verification_request(tmp_path):
    import csv

    text = "During the shift, Line K delivered 43 boxes."

    class Logbook:
        name, mode, output_protocol, max_attempts = "logbook", "replay", "flat-v1", 1

        def generate(self, request):
            assert not request.chunk_id.startswith("verify-")
            body = json.loads(request.messages[-1]["content"])
            assert body["task"]["row_subject"] == "production lines"
            return BackendResponse(model=self.name, text=json.dumps({"records": [{
                "record_type": "quantity", "name_handling": "as_reported",
                "values": {"entity": "Line K", "value": "43", "unit": "boxes", "conditions": None},
                "quotes": {"entity": text, "value": text, "unit": text, "conditions": ""},
            }]}))

    result = extract(text, prompt_description="List output by production line.",
                     field_list={"line": "Line name", "quantity": "Delivered boxes"},
                     backend=Logbook(), backend_identity="synthetic-logbook",
                     table_subject="production lines", dataset_options={"subject_label_field": "line"},
                     output_dir=tmp_path / "run", progress="quiet")
    assert result.analysis["budget"]["reserved"] == 0 and result.analysis["objects"] == []
    with (tmp_path / "run/dataset/main.csv").open(encoding="utf-8-sig", newline="") as f:
        row, = csv.DictReader(f)
    assert (row["line"], row["quantity"], row["直接对象"]) == ("Line K", "43", "Line K")

    # The first-pass file is sufficient if a later verification call was interrupted.
    from niceextract.dataset_io import assemble_from_files
    source = tmp_path / "input.md"
    source.write_text(text, encoding="utf-8")
    replay = assemble_from_files(source, tmp_path / "run", raw_only=True, subject_label_field="line")
    assert replay["row_subject"] == "production lines"
    assert replay["rows"] == result.dataset["rows"]
    changed = assemble_from_files(source, tmp_path / "run", raw_only=True, subject_label_field="line",
                                  row_subject="individual operators")
    assert not changed["rows"]


def test_compound_name_from_a_different_record_is_only_a_comparison_candidate():
    def observed(typ, name, val):
        return {"record_type": typ, "entity": name, "value": val, "name_handling": "as_reported",
                "source_evidence": [{"fields": ["value", "entity"], "quote": f"{name} {val}"}]}

    parent = observed("machine", "R1", "R1")
    part = observed("length", "R1 lining", "15")
    other = observed("length", "R10", "15")
    _route_extracted_names([parent, part, other], "machine")
    assert part["name_review"]["candidate_names"] == ["R1"] and part["entity"] == "R1 lining"
    assert "name_review" not in other and "name_review" not in parent


def test_markdown_range_span_points_to_both_original_characters():
    source = r"Sample Q ranged from 10%\~20%."
    chunk = DocumentChunk(chunk_id="c", text=source, segments=[SourceSpan(start=0, end=len(source))])
    ref = ground_quote(source, chunk, "10%~20%", normalize=True)
    assert ref.status == "normalized"
    assert source[ref.start:ref.end] == r"10%\~20%"
    assert ref.source_matches[0].rules == ["markdown_range"]


def test_competing_columns_for_one_original_value_are_sent_to_verification():
    def proposal(oid, field, ref, start):
        return {"observation_id": oid, "record_type": field, "entity": "Q", "value": "4",
                "source_kind": "table", "name_handling": "as_reported",
                "source_evidence": [{"fields": ["value"], "ref": ref, "quote": "4",
                                     "span": {"start": start, "end": start + 1}}]}

    mass = proposal("a", "mass", "table-r2-c3", 20)
    code = proposal("b", "category_code", "table-r2-c3", 20)
    other = proposal("c", "mass", "table-r4-c3", 45)
    _route_extracted_names([mass, code, other], None)
    assert mass["name_review"]["candidate_fields"] == ["category_code"]
    assert code["name_review"]["candidate_fields"] == ["mass"]
    assert "name_review" not in other


@pytest.mark.parametrize("definition,quote,expected", [
    ("Usable length. Exclude total length.", "Line X length, 60 mm.", "unknown"),
    ("Usable length. Exclude total length.", "Line X usable length, 60 mm.", "supported"),
    ("该件的可用宽度。只有总宽度的表述时，不改称可用宽度。", "指定边的宽度，为60毫米。", "unknown"),
    ("该件的可用宽度。只有总宽度的表述时，不改称可用宽度。", "指定边的可用宽度，为60毫米。", "supported"),
])
def test_reported_dimension_preserves_a_task_qualification(definition, quote, expected):
    from niceextract.dataset_field_fit import checked_field_fit

    item = {"value": "60", "conditions": quote.split(",")[0], "name_handling": "as_reported",
            "source_kind": "prose", "target_match": "supported"}
    fit = checked_field_fit(definition, item, {"status": "supported"}, [{"quote": quote}])
    assert fit["status"] == expected
