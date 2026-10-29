"""Readable reports preserve candidate distinctions and safely display source text."""

from __future__ import annotations

import re

from attestextract import extract
from attestextract.backends import ReplayBackend
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
    assert len(re.split(r"(?<!\\)\|", subject_row)) == 7


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
