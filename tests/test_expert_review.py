"""Review rows retain independent observations without inventing database entities."""

from copy import deepcopy
from html.parser import HTMLParser
import json

import pytest

from attestextract import extract, ExtractionResult, RunTrace
from attestextract.backends import ReplayBackend
from attestextract.expert_review import build_expert_review, render_expert_review, save_expert_review
from attestextract.models import BackendResponse, CandidateLink, CheckResult, ReviewItem
from attestextract.reporting import render_markdown_report
from attestextract.run_output import save_run_result


SOURCE = "A has porosity 10~20% (mean 15%) and permeability 7 mD under warm conditions."
FIELDS = {"porosity": "Reported porosity", "permeability": "Reported permeability", "pressure": "Reported pressure"}


def record(field="porosity", *, entity="A", value="10~20", unit="%", conditions="warm", quote=SOURCE):
    values = {"entity": entity, "value": value, "unit": unit, "conditions": conditions}
    return {"record_type": field, "values": values,
            "quotes": {name: quote if value is not None else "" for name, value in values.items()}}


def result_for(*records, source=SOURCE):
    return extract(source, prompt_description="Extract exactly reported properties.", field_list=FIELDS,
                   backend=ReplayBackend([{"records": list(records)}], output_protocol="flat-v1"), source_name="synthetic.md")


def test_exact_labels_make_review_row_with_multiple_fields_and_all_values_unchanged():
    result = result_for(record(), record(value="15", conditions="warm"),
                        record("permeability", value="7", unit="mD"))
    before = result.model_dump(mode="json")
    review = build_expert_review(result)
    assert len(review["rows"]) == 1
    row = review["rows"][0]
    assert row["entity"] == "A" and row["conditions"] == "warm"
    assert row["identity_verified"] is False
    assert list(row["fields"]) == ["porosity", "permeability"]
    assert [item["value"] for item in row["fields"]["porosity"]] == ["10~20", "15"]
    assert review["targets_without_retained_value"] == ["pressure"]
    first = row["fields"]["porosity"][0]
    assert first["evidence"] == [{"fields": ["entity", "value", "unit", "conditions"],
                                  "quote": SOURCE, "location_status": "matched",
                                  "span": {"start": 0, "end": len(SOURCE)},
                                  "matches": [{"start": 0, "end": len(SOURCE)}]}]
    assert first["source_record"] == "/candidates/0"
    assert first["candidate_id"] == result.candidates[0].candidate_id
    assert result.model_dump(mode="json") == before
    assert "min" not in first and "max" not in first


def test_different_conditions_missing_objects_and_different_labels_stay_separate():
    result = result_for(record(), record(conditions="cold"), record(conditions=None),
                        record(entity=None, value="1"), record(entity=" ", value="2"),
                        record(entity="A ", value="3"))
    rows = build_expert_review(result)["rows"]
    assert len(rows) == 6
    assert rows[2]["conditions_reported"] is False
    assert rows[3]["grouping"] == rows[4]["grouping"] == "individual_candidate"
    assert rows[5]["entity"] == "A "


def test_null_and_blank_value_remain_reviewable_not_counted_as_reported_target():
    result = result_for(record(value=" "), record("pressure", value=None))
    review = build_expert_review(result)
    assert review["targets_without_retained_value"] == list(FIELDS)
    assert review["rows"][0]["fields"]["porosity"][0]["value"] == " "
    assert review["rows"][0]["fields"]["pressure"][0]["value"] is None


def test_old_non_simple_result_retains_native_fields_and_false_zero_values():
    task = {"name": "Generic", "record_types": {"event": {"fields": {
        "name": {"type": "string"}, "count": {"type": "integer"}, "active": {"type": "boolean"}}}}}
    response = {"records": [{"record_type": "event", "fields": {
        "name": {"value": "event", "quote": "event"}, "count": {"value": 0, "quote": "0"},
        "active": {"value": False, "quote": "false"}}}]}
    result = extract("event 0 false", task, backend=ReplayBackend([response], output_protocol="native-v1"))
    saved = result.model_dump(mode="json")
    saved["schema_version"] = "0.3"
    old = ExtractionResult.model_validate(saved)
    review = build_expert_review(old)
    item = review["rows"][0]["fields"]["event"][0]
    assert review["rows"][0]["grouping"] == "individual_candidate"
    assert item["fields"] == {"name": "event", "count": 0, "active": False}
    assert item["source_record"] == "/candidates/0"


TABLE = "| Sample | Porosity/% |\n| --- | --- |\n| A | 15 |\n"


class TableBackend:
    name, mode, output_protocol = "synthetic-review-table", "replay", "flat-v1"

    def generate(self, request):
        payload = json.loads(request.messages[-1]["content"])
        entries = payload["table"]["entries"]
        def source(quote):
            entry = next(item for item in entries if item["quote"] == quote)
            return {"ref": entry["ref"], "quote": quote}
        good = {"record_id": "good", "record_type": "porosity", "fields": {
            "entity": {"value": "A", "sources": [source("A")], "relation": "row"},
            "value": {"value": "15", "sources": [source("15")], "relation": "anchor"},
            "unit": {"value": "%", "sources": [source("Porosity/%")], "relation": "column"},
            "conditions": {"value": None, "sources": [], "relation": None}},
            "anchors": [source("15")], "scope": "individual", "scope_sources": []}
        bad = deepcopy(good)
        bad["record_id"] = "bad"
        del bad["fields"]["value"]["relation"]
        return BackendResponse(text=json.dumps({"records": [good, bad, None]}))


def test_table_candidates_and_quarantined_declarations_are_separate_and_locatable():
    result = extract(TABLE, prompt_description="Extract porosity", field_list=FIELDS,
                     backend=TableBackend(), source_name="synthetic-table.md")
    before = result.model_dump(mode="json")
    review = build_expert_review(result)
    assert review["summary"] == {"review_rows": 1, "prose_candidates": 0,
                                 "table_candidates": 1, "quarantined_records": 2}
    item = review["rows"][0]["fields"]["porosity"][0]
    assert item["value"] == "15"
    assert item["source_record"] == "/table_results/0/candidates/0"
    assert item["table_status"] == "partial_format_error"
    assert item["relations"]["entity"] == "row"
    value_evidence = next(entry for entry in item["evidence"] if "value" in entry["fields"])
    assert value_evidence["span"] == {"start": TABLE.index("15"), "end": TABLE.index("15") + 2}
    rejected = review["quarantined"][0]
    assert rejected["status"] == "quarantined"
    assert rejected["value"] == "15" and rejected["relations"]["value"] is None
    assert rejected["issues"][0]["loc"] == ["fields", "value"]
    assert all(entry["location_status"] == "not_checked" for entry in rejected["evidence"])
    assert rejected["source_record"] == "/table_results/0/rejections/0/raw"
    assert review["quarantined"][1]["unparsed_record"] is None
    report = render_expert_review(review)
    assert "格式隔离记录" in report and "未通过格式检查" in report
    assert result.model_dump(mode="json") == before


@pytest.mark.parametrize("changes", [
    {"scope_sources": None}, {"scope_sources": [17]}, {"scope_sources": "invalid"},
    {"scope_sources": [{"ref": [1], "quote": {"invalid": "quote"}}]},
    {"scope": {"bad": "scope"}}, {"scope": ["individual"]}, {"scope": "unknown"},
    {"fields": None}, {"fields": {"value": 17}},
    {"record_type": {"bad": "target"}, "record_id": [1]},
])
def test_quarantined_raw_declarations_need_not_satisfy_table_contract(changes, tmp_path):
    class MalformedTableBackend(TableBackend):
        def generate(self, request):
            response = super().generate(request)
            raw = json.loads(response.text)["records"][0]
            raw.update(deepcopy(changes))
            return response.model_copy(update={"text": json.dumps({"records": [raw]})})

    result = extract(TABLE, prompt_description="Extract porosity", field_list=FIELDS,
                     backend=MalformedTableBackend(), source_name="synthetic-table.md")
    before = result.model_dump(mode="json")
    assert result.format_status == "format_error"
    save_expert_review(result, tmp_path)
    review = json.loads((tmp_path / "review.json").read_text(encoding="utf-8"))
    assert review["summary"]["quarantined_records"] == 1
    assert review["rows"] == []
    rejected = review["quarantined"][0]
    assert rejected["source_record"] == "/table_results/0/rejections/0/raw"
    assert rejected["issues"]
    assert all(item["location_status"] == "not_checked" for item in rejected["evidence"])
    for name in {"scope", "scope_sources"} & changes.keys():
        assert rejected[name] == changes[name]
    markdown = (tmp_path / "review.md").read_text(encoding="utf-8")
    assert "隔离原因" in markdown and "未通过格式检查" in markdown
    assert result.model_dump(mode="json") == before


def test_review_markdown_is_inert_and_complete_including_markup_newlines_and_math():
    quote = "<script>bad</script>|`**[link](target)\r\n$\\mathrm{A}$"
    result = result_for(record(entity=quote, value=quote, quote=quote), source=quote)
    review = build_expert_review(result)
    rendered = render_expert_review(review)
    assert "<script>" not in rendered and "$" not in rendered
    class Text(HTMLParser):
        text = ""
        def handle_data(self, data):
            self.text += data
    parsed = Text(convert_charrefs=True)
    parsed.feed(rendered)
    assert "<script>bad</script>|`**[link](target)" in parsed.text
    assert "$\\mathrm{A}$" in parsed.text
    assert review["rows"][0]["fields"]["porosity"][0]["evidence"][0]["quote"] == quote


def test_exclusive_files_and_redaction_preserve_json_literals_and_original_result(tmp_path):
    secret = 'sensitive-"\\<tag>'
    result = result_for(record(entity=secret, value="15", quote=secret), source=secret)
    before = result.model_dump(mode="json")
    save_expert_review(result, tmp_path, secrets=[secret, "null"], source_result="../old/result.json")
    raw = (tmp_path / "review.json").read_text(encoding="utf-8")
    saved = json.loads(raw)
    assert saved["source_result"] == "../old/result.json"
    assert secret not in raw and "sensitive" not in (tmp_path / "review.md").read_text(encoding="utf-8")
    assert saved["rows"][0]["entity"] == "[REDACTED]"
    assert result.model_dump(mode="json") == before
    with pytest.raises(FileExistsError):
        save_expert_review(result, tmp_path)
    assert (tmp_path / "review.json").read_text(encoding="utf-8") == raw


def test_managed_run_outputs_include_review_and_detailed_report_indexes_rows(tmp_path):
    result = result_for(record(), record("permeability", value="7", unit="mD"))
    trace = RunTrace(tmp_path / "diagnostics")
    save_run_result(result, tmp_path, trace)
    review = json.loads((tmp_path / "review.json").read_text(encoding="utf-8"))
    assert review["summary"]["review_rows"] == 1
    assert (tmp_path / "review.md").is_file()
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "按对象与条件整理的审阅行" in report
    assert report.index("按对象与条件整理") < report.index("## 候选 1")
    assert "porosity（1）" in report and "permeability（1）" in report
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) == result.model_dump(mode="json")


@pytest.mark.parametrize("secret", ["rows", "source", "value"])
def test_render_redaction_cannot_rename_schema_keys_and_crash(secret):
    result = result_for(record(entity=secret, quote=secret), source=secret)
    review = build_expert_review(result)
    before = deepcopy(review)
    rendered = render_expert_review(review, secrets=[secret])
    assert "\\[REDACTED\\]" in rendered
    assert f"对象：{secret}" not in rendered
    detailed = render_markdown_report(result, secrets=[secret])
    assert "按对象与条件整理" in detailed
    assert review == before


def test_compact_markdown_preserves_quotes_and_summarizes_routine_information():
    result = result_for(record(conditions=None), record(value="15", conditions=None))
    result.review.append(ReviewItem(candidate_id=result.candidates[0].candidate_id,
                                    field="entity", code="synthetic_review_needed", message="Needs actual review"))
    review = build_expert_review(result)
    report = render_expert_review(review)
    assert "&#95;" not in report
    assert "Needs actual review" in report
    assert report.count("项信息提示") == 1
    assert "The model did not report this optional field" not in report
    assert "location_status" not in report and "matched" in report
    assert "matches" not in report
    assert SOURCE.replace("~", "\\~").replace("%", "%").replace("(", "\\(").replace(")", "\\)").replace(".", "\\.") in report
    assert "范围声明：未声明" in report
    assert review["rows"][0]["scope"] is None
    assert any(item["severity"] == "info" for row in review["rows"] for obs in row["fields"].values()
               for record in obs for item in record["issues"])


def test_existing_links_and_failed_checks_survive_without_review_items():
    result = result_for(record(), record(value="15"))
    result.review = []
    result.candidates[0].checks.append(CheckResult(code="synthetic_blocker", passed=False, field="value"))
    result.candidates[0].needs_review = False
    result.links = [CandidateLink(kind="insufficient_context",
                                 candidate_ids=[item.candidate_id for item in result.candidates],
                                 compared_fields=["value"], unresolved_fields=["conditions"])]
    review = build_expert_review(result)
    first = review["rows"][0]["fields"]["porosity"][0]
    assert first["status"] == "needs_review"
    assert first["failed_checks"][0]["code"] == "synthetic_blocker"
    assert review["associations"][0]["unresolved_fields"] == ["conditions"]
    report = render_expert_review(review)
    assert "synthetic\\_blocker" in report and "关联依据不足" in report
    assert "尚未明确：conditions" in report
