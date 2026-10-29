"""Readable reports preserve candidate distinctions and safely display source text."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from attestextract import extract
from attestextract.backends import ReplayBackend
from attestextract.models import CandidateLink, EvidenceRef, MeasurementEvidence, ReviewItem
from attestextract.reporting import render_markdown_report


TASK = {"name": "合成审阅任务", "record_types": {"measurement": {"fields": {
    "subject": {"type": "string", "required": True},
    "amount": {"type": "number"},
    "condition": {"type": "string"},
    "enabled": {"type": "boolean"},
}, "condition_fields": ["condition"]}}}


def response(**fields):
    return {"record_type": "measurement", "fields": {
        name: {"value": value, "quote": quote} for name, (value, quote) in fields.items()
    }}


def run(text, *records):
    return extract(text, TASK, backend=ReplayBackend([{"records": list(records)}]), source_name="合成🙂.md")


def test_unicode_report_keeps_both_observations_and_original_offsets_without_mutation():
    text = "前置🙂\r\nA 在暖态时流量为 12。A 在冷态时流量为 12。"
    warm, cold = "A 在暖态时流量为 12。", "A 在冷态时流量为 12。"
    result = run(text,
        response(subject=("A", warm), amount=(12, warm), condition=("暖态", warm)),
        response(subject=("A", cold), amount=(12, cold), condition=("冷态", cold)),
    )
    before = result.model_dump(mode="json")
    report = render_markdown_report(result)

    assert "候选：2；待复核候选：0" in report
    assert report.count("| amount | 12 |") == 2
    assert '"暖态"' in report and '"冷态"' in report
    assert "合成🙂\\.md" in report
    assert f"\\[{text.index(warm)}, {text.index(warm) + len(warm)}\\)" in report
    assert f"\\[{text.index(cold)}, {text.index(cold) + len(cold)}\\)" in report
    assert "仍为候选" in report and "不代表事实已核验" in report
    assert result.model_dump(mode="json") == before
    assert render_markdown_report(result) == report


def test_report_escapes_source_markup_and_keeps_evidence_line_breaks_inside_cells():
    quote = "<script>alert('x')</script>|`*_[link](target)\\\r\n# next"
    result = run(quote, response(subject=(quote, quote)))
    report = render_markdown_report(result)

    assert "<script>" not in report and "</script>" not in report
    assert "&lt;script&gt;" in report
    assert "\\|\\`\\*\\_\\[link\\]\\(target\\)\\\\<br>\\# next" in report
    assert not any(line.startswith("# next") for line in report.splitlines())
    subject_row = next(line for line in report.splitlines() if line.startswith("| subject |"))
    assert len(re.split(r"(?<!\\)\|", subject_row)) == 8


def test_report_distinguishes_zero_false_null_and_string_values():
    text = "A has 0 and false."
    result = run(text, response(subject=("false", "false"), amount=(0, "0"), enabled=(False, "false"), condition=(None, "")))
    report = render_markdown_report(result)

    assert '| subject | "false" | false |' in report
    assert "| amount | 0 | 0 |" in report
    assert "| enabled | false | false |" in report
    assert "| condition | null |  | missing |" in report
    assert "待复核候选：1" in report


def test_report_lists_ambiguous_matches_and_missing_required_checks_and_review_reasons():
    text = "12 appears twice: 12."
    result = run(text, response(amount=(12, "12")))
    report = render_markdown_report(result)

    assert "| amount | 12 | 12 | ambiguous | 多个匹配：\\[0, 2\\); \\[18, 20\\) |" in report
    assert "evidence\\_ambiguous" in report
    assert "missing\\_required\\_field" in report
    assert "required\\_field\\_present（字段：subject）" in report
    assert "required\\_field\\_present（字段：condition）" in report
    assert "A required identity, condition, or task field is missing\\." in report


def test_empty_result_is_presented_without_claiming_the_document_has_no_information():
    result = run("合成原文。")
    result.run.usage = {"total_tokens": 37, "prompt_tokens": 25}
    report = render_markdown_report(result)

    assert "候选：0；待复核候选：0；复核项：0" in report
    assert "不能证明原文没有可抽取信息" in report
    assert "| prompt\\_tokens | 25 |" in report
    assert "| total\\_tokens | 37 |" in report
    assert "## 候选 1" not in report


def test_secret_redaction_precedes_markup_escaping_and_keeps_result_unchanged():
    secret = 'synthetic_secret-*<tag>\\"token'
    text = "prefix " + secret
    result = run(text, response(subject=(secret, secret)))
    result.source.name = secret + ".md"
    before = result.model_dump(mode="json")
    report = render_markdown_report(result, secrets=(item for item in [secret]))

    assert "synthetic" not in report
    assert "tag" not in report and "token" not in report
    assert "\\[REDACTED\\]" in report
    assert result.model_dump(mode="json") == before


def test_latex_evidence_is_literal_code_with_exact_characters_and_no_active_markup():
    quote = "$\\mathrm { ~ C ~ a ~ O ~ }$|`**<script>bad</script>\n含量 1 . 2 % \\~ 3 %"
    result = run(quote, response(subject=("合成材料", quote)))
    before = result.model_dump(mode="json")
    report = render_markdown_report(result)
    subject_row = next(line for line in report.splitlines() if line.startswith("| subject |"))
    assert subject_row.count("|") == 7
    assert "$" not in subject_row and "<script>" not in subject_row
    assert "<code>" in subject_row and "&#92;mathrm" in subject_row

    class CodeText(HTMLParser):
        def __init__(self):
            super().__init__()
            self.inside_code = False
            self.text = ""

        def handle_starttag(self, tag, attrs):
            if tag == "code":
                self.inside_code = True
            elif tag == "br" and self.inside_code:
                self.text += "\n"

        def handle_endtag(self, tag):
            if tag == "code":
                self.inside_code = False

        def handle_data(self, data):
            if self.inside_code:
                self.text += data

    parser = CodeText()
    parser.feed(subject_row)
    assert parser.text == quote
    assert result.model_dump(mode="json") == before


def test_report_caps_ambiguous_locations_but_keeps_all_result_spans():
    result = run("m m m m m m m", response(subject=("m", "m")))
    report = render_markdown_report(result)
    assert "共 7 处（完整位置见 JSON）" in report
    assert "\\[8, 9\\)" in report
    assert "\\[10, 11\\)" not in report
    assert [(span.start, span.end) for span in result.candidates[0].fields["subject"].evidence.matches] == [
        (0, 1), (2, 3), (4, 5), (6, 7), (8, 9), (10, 11), (12, 13),
    ]


def test_non_dollar_math_delimiters_are_also_displayed_as_literal_code():
    for quote in (r"\(\alpha\)", r"\[x^2\]", r"\begin{equation}x=2\end{equation}"):
        report = render_markdown_report(run(quote, response(subject=("合成", quote))))
        row = next(line for line in report.splitlines() if line.startswith("| subject |"))
        assert "<code>" in row and "&#92;" in row
        assert quote not in row


def test_report_separates_field_states_measurement_spans_and_unconfirmed_links():
    result = run("A: 12 m. B: 12 m.",
                 response(subject=("A", "A"), amount=(12, "A: 12 m.")),
                 response(subject=("B", "B"), amount=(12, "B: 12 m.")))
    first, second = result.candidates
    first.field_status = {"subject": "reported", "condition": "required_missing", "enabled": "not_applicable"}
    first.measurements = {"size": MeasurementEvidence(
        context=EvidenceRef(quote="A: 12 m.", status="matched", start=0, end=8),
        value=EvidenceRef(quote="12", status="matched", start=3, end=5),
        unit=EvidenceRef(quote="m", status="matched", start=6, end=7),
        unit_source="context", condition_fields=["condition"], issues=["condition_missing"],
    )}
    result.review = [
        ReviewItem(candidate_id=first.candidate_id, code="missing_condition", field="condition", message="Review scenario."),
        ReviewItem(candidate_id=first.candidate_id, code="unreported", field="enabled", message="Information only.", severity="info"),
    ]
    result.links = [CandidateLink(kind="possible_conflict", candidate_ids=[first.candidate_id, second.candidate_id],
                                  compared_fields=["subject", "amount"], differing_fields=["subject"],
                                  unresolved_fields=["condition"])]
    before = result.model_dump(mode="json")
    report = render_markdown_report(result)

    assert "需复核问题：1；信息提示：1" in report
    assert "未提供字段：condition；状态：required\\_missing" in report
    assert "未提供字段：enabled；状态：not\\_applicable" in report
    assert "| 可能冲突 | 1, 2 | subject, amount | subject | condition |" in report
    assert "没有合并、删除或选定任何候选" in report
    assert "| 测量上下文（整条引文） | A: 12 m\\. | matched | \\[0, 8\\) |" in report
    assert "| 数值片段 | 12 | matched | \\[3, 5\\) |" in report
    assert "| 单位片段 | m | matched | \\[6, 7\\) |" in report
    assert "语义状态：未核验" in report and "关联条件字段：condition" in report
    assert "- 信息：unreported（字段：enabled）：Information only\\." in report
    assert result.model_dump(mode="json") == before


def test_report_keeps_header_quote_when_column_binding_failed():
    result = run("Length (m) Width (cm)\n12", response(subject=("A", "Length (m)")))
    result.candidates[0].measurements = {"size": MeasurementEvidence(
        context=EvidenceRef(quote="12", status="matched", start=22, end=24),
        value=EvidenceRef(quote="12", status="matched", start=22, end=24),
        unit=EvidenceRef(quote="m", status="unmatched"),
        unit_context=EvidenceRef(quote="Length (m)", status="matched", start=0, end=10),
        unit_source="table_header", condition_fields=[], issues=["measurement_header_mismatch"],
    )}
    report = render_markdown_report(result)
    assert "| 单位片段 | m | unmatched | 未定位 |" in report
    assert "| 表头上下文（不代表列关联通过） | Length \\(m\\) | matched | \\[0, 10\\) |" in report
    assert "measurement\\_header\\_mismatch" in report
