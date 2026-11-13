"""Simple display files retain all values and distinguish display from verification."""
from copy import deepcopy
import json

import pytest

from attestextract import extract
from attestextract.annotation_view import build_annotation, render_annotation, save_annotation
from attestextract.backends import ReplayBackend
from attestextract.models import BackendResponse, CandidateLink, CheckResult


TEXT = "Sample A has mass 3~5 g, mean 4 g, and density 2 g/mL."
FIELDS = {"mass": "Reported sample mass", "density": "Reported density", "temperature": "Reported temperature"}


def record(field="mass", value="3~5", unit="g", entity="Sample A", conditions="range", quote=TEXT):
    values = dict(entity=entity, value=value, unit=unit, conditions=conditions)
    return {"record_type": field, "values": values,
            "quotes": {key: quote if item is not None else "" for key, item in values.items()}}


def result_for(*records, source=TEXT):
    return extract(source, prompt_description="Extract reported values without inference.",
                   field_list=FIELDS, backend=ReplayBackend([{"records": list(records)}], output_protocol="flat-v1"),
                   source_name="synthetic.md")


def test_same_object_keeps_different_conditions_and_fields_together():
    result = result_for(record(), record(value="4", conditions="mean"),
                        record("density", "2", "g/mL", conditions=None))
    before = result.model_dump(mode="json")
    data = build_annotation(result)
    assert len(data["records"]) == 1
    row = data["records"][0]
    assert row["entity"] == "Sample A"
    assert set(row["fields"]) == {"mass", "density"}
    assert [item["value"] for item in row["fields"]["mass"]] == ["3~5", "4"]
    assert [item["conditions"] for item in row["fields"]["mass"]] == ["range", "mean"]
    assert all("scope" not in item for item in row["fields"]["mass"])
    assert row["fields"]["mass"][0]["evidence"] == [TEXT]
    assert data["missing_fields"] == ["temperature"]
    assert "未人工核实" in data["verification"]
    assert result.model_dump(mode="json") == before
    markdown = render_annotation(data)
    rows = [line for line in markdown.splitlines() if line.startswith("| mass |")]
    assert len(rows) == 2
    assert all("Sample A has mass" in line and "未人工核实" in line for line in rows)
    assert "3\\~5" in rows[0] and "range" in rows[0]
    assert "4" in rows[1] and "mean" in rows[1]
    assert "program_checked" not in markdown and "candidate-" not in markdown


def test_missing_entities_remain_independent_and_equal_values_are_not_deduplicated():
    result = result_for(record(), record(entity="Sample B"), record(entity=None),
                        record(entity=None, value="4"), record(entity="Sample A "))
    # Existing extraction can fold identical same-call records; the display must
    # nevertheless retain separately identified candidates supplied by a result.
    result.candidates.append(result.candidates[0].model_copy(update={"candidate_id": "independent-observation"}))
    records = build_annotation(result)["records"]
    assert len(records) == 5
    assert len(records[0]["fields"]["mass"]) == 2
    assert records[1]["entity"] == "Sample B"
    assert records[2]["entity"] is None and records[3]["entity"] is None
    assert records[4]["entity"] == "Sample A "


def test_real_warnings_and_associations_remain_but_routine_info_is_hidden():
    result = result_for(record(conditions=None), record(value="4", conditions=None))
    result.candidates[0].checks.append(CheckResult(code="synthetic_check", passed=False, field="value"))
    result.links.append(CandidateLink(kind="insufficient_context",
        candidate_ids=[item.candidate_id for item in result.candidates],
        compared_fields=["value"], unresolved_fields=["conditions"]))
    data = build_annotation(result)
    items = data["records"][0]["fields"]["mass"]
    assert any("synthetic_check" in issue for issue in items[0]["issues"])
    assert all(any("关联依据不足" in issue for issue in item["issues"]) for item in items)
    assert not any("optional" in issue for item in items for issue in item["issues"])
    assert "待核对" in render_annotation(data)


def test_zero_false_and_original_generic_fields_are_preserved_without_inferred_object():
    task = {"name": "Generic events", "record_types": {"event": {"fields": {
        "name": {"type": "string"}, "count": {"type": "integer"}, "active": {"type": "boolean"}}}}}
    response = {"records": [{"record_type": "event", "fields": {
        "name": {"value": "event", "quote": "event"}, "count": {"value": 0, "quote": "0"},
        "active": {"value": False, "quote": "false"}}}]}
    result = extract("event 0 false", task, backend=ReplayBackend([response], output_protocol="native-v1"))
    item = build_annotation(result)["records"][0]
    assert item["entity"] is None
    assert item["fields"]["event"][0]["value"] == {"name": "event", "count": 0, "active": False}


TABLE = "| Object | Value/g |\n| --- | --- |\n| A | 5 |\n"


class TableBackend:
    name, mode, output_protocol = "synthetic-annotation", "replay", "flat-v1"
    changes = None

    def generate(self, request):
        table = json.loads(request.messages[-1]["content"])["table"]
        def selection(quote):
            entry = next(item for item in table["entries"] if item["quote"] == quote)
            return {"ref": entry["ref"], "quote": quote}
        record = {"record_id": "reading", "record_type": "mass", "scope": "individual", "scope_sources": [],
                  "anchors": [selection("5")], "fields": {
                      "entity": {"value": "A", "sources": [selection("A")], "relation": "row"},
                      "value": {"value": "5", "sources": [selection("5")], "relation": "anchor"},
                      "unit": {"value": "g", "sources": [selection("Value/g")], "relation": "column"},
                      "conditions": {"value": None, "sources": []}}}
        if self.changes:
            record.update(self.changes)
        return BackendResponse(text=json.dumps({"records": [record]}))


def table_result(source=TABLE, backend=None):
    return extract(source, prompt_description="Extract mass", field_list=FIELDS,
                   backend=backend or TableBackend(), source_name="synthetic-tables.md")


def test_two_tables_share_object_display_but_keep_visible_distinct_origins():
    result = table_result(TABLE + "\n" + TABLE)
    data = build_annotation(result)
    assert len(data["records"]) == 1
    items = data["records"][0]["fields"]["mass"]
    assert len(items) == 2
    assert [item["source"] for item in items] == ["表格 table-0001", "表格 table-0002"]
    assert all(item["conditions"] is None and item["scope"] == "individual" for item in items)
    rendered = render_annotation(data)
    assert "table\\-0001" in rendered and "table\\-0002" in rendered
    assert "| 出处 |" in rendered


@pytest.mark.parametrize("changes", [
    {"scope": {"invalid": "scope"}}, {"scope_sources": None}, {"scope_sources": [17]},
    {"fields": None}, {"fields": {"value": 17}}, {"record_type": [1]},
])
def test_invalid_declarations_stay_quarantined_without_render_failure(changes, tmp_path):
    backend = TableBackend()
    backend.changes = changes
    result = table_result(backend=backend)
    before = result.model_dump(mode="json")
    data = build_annotation(result)
    assert data["records"] == [] and len(data["quarantined"]) == 1
    assert "格式隔离" in render_annotation(data)
    save_annotation(result, tmp_path)
    assert result.model_dump(mode="json") == before
    sources = json.loads((tmp_path / "annotation.sources.json").read_text(encoding="utf-8"))
    assert sources["entries"][0]["annotation_pointer"] == "/quarantined/0"
    assert sources["entries"][0]["source_record"] == "/table_results/0/rejections/0/raw"


def test_saved_source_map_covers_every_independent_observation_and_does_not_overwrite(tmp_path):
    result = result_for(record(), record(value="4", conditions="mean"), record("density", "2", "g/mL"))
    save_annotation(result, tmp_path, source_result="../old/result.json")
    data = json.loads((tmp_path / "annotation.json").read_text(encoding="utf-8"))
    mapping = json.loads((tmp_path / "annotation.sources.json").read_text(encoding="utf-8"))
    assert mapping["source_result"] == "../old/result.json"
    assert len(mapping["entries"]) == 3
    assert {entry["source_record"] for entry in mapping["entries"]} == {"/candidates/0", "/candidates/1", "/candidates/2"}
    assert {entry["annotation_pointer"] for entry in mapping["entries"]} == {
        "/records/0/fields/mass/0", "/records/0/fields/mass/1", "/records/0/fields/density/0"}
    assert "candidate_id" not in json.dumps(data) and "source_record" not in json.dumps(data)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(FileExistsError):
        save_annotation(result, tmp_path)
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}


def test_arbitrary_markdown_and_structure_key_credentials_are_safe(tmp_path):
    secret = "records"
    quote = '<script>bad</script>|**[go](target)\n$\\mathrm{A}$ records'
    result = result_for(record(entity=quote, quote=quote), source=quote)
    data = build_annotation(result)
    rendered = render_annotation(data, secrets=[secret])
    assert "<script>" not in rendered and "$" not in rendered and "records" not in rendered
    assert "[go](target)" not in rendered
    assert data["records"][0]["entity"] == quote
    save_annotation(result, tmp_path, secrets=[secret])
    assert secret not in (tmp_path / "annotation.json").read_text(encoding="utf-8")
    assert "[REDACTED]" in (tmp_path / "annotation.json").read_text(encoding="utf-8")
