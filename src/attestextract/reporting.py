"""Render extraction candidates for local human review without changing results."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from html import escape
import json
import re
import string

from .diagnostics import redact_text
from .models import EvidenceRef, ExtractionResult


def _cell(value: str, secrets: Iterable[str]) -> str:
    """Keep source-derived text inside one Markdown cell or paragraph."""
    text = redact_text(value, secrets).replace("\r\n", "\n").replace("\r", "\n")
    if "$" in text or any(delimiter in text for delimiter in (r"\(", r"\[", r"\begin{")):
        # Math-aware Markdown viewers skip code elements. Encoding punctuation
        # also prevents Markdown, table separators, or source HTML inside them.
        literal = "".join(f"&#{ord(char)};" if char in string.punctuation else char for char in text)
        return "<code>" + literal.replace("\n", "<br>") + "</code>"
    text = escape(text, quote=False)
    text = re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", r"\\\1", text)
    return text.replace("\n", "<br>")


def _location(evidence: EvidenceRef) -> str:
    if evidence.status == "matched" and evidence.start is not None and evidence.end is not None:
        return f"[{evidence.start}, {evidence.end})"
    if evidence.matches:
        locations = "; ".join(f"[{span.start}, {span.end})" for span in evidence.matches[:5])
        if len(evidence.matches) > 5:
            locations += f"；共 {len(evidence.matches)} 处（完整位置见 JSON）"
        return "多个匹配：" + locations
    return "未定位"


def render_markdown_report(result: ExtractionResult, *, secrets: Iterable[str] = ()) -> str:
    """Return a readable view; exact values and evidence remain in the JSON result.

    Locations are half-open Unicode character offsets in the input document.
    A matched quote establishes location only; this report accepts no facts.
    """
    secrets = tuple(secrets)

    def cell(value: str) -> str:
        return _cell(value, secrets)

    pending = sum(candidate.needs_review for candidate in result.candidates)
    blocking = sum(item.severity == "review" for item in result.review)
    lines = [
        "# 抽取候选审阅", "",
        "本报告展示抽取候选。结构或引文定位检查通过不代表事实已核验，也不代表已接受为正式结果。", "",
        f"- 文献：{cell(result.source.name or '未命名输入')}",
        f"- 任务：{cell(result.task.name)}",
        f"- 程序版本：{cell(result.package_version)}；结果格式：{cell(result.schema_version)}",
        f"- 模型：{cell(result.run.model or '未报告')}；模式：{cell(result.run.mode)}",
        f"- 分块：{result.run.chunk_count}；模型响应：{result.run.response_count}；HTTP/后端尝试：{result.run.request_attempts}",
        f"- 候选：{len(result.candidates)}；待复核候选：{pending}；复核项：{len(result.review)}",
        f"- 需复核问题：{blocking}；信息提示：{len(result.review) - blocking}（信息提示本身不阻止候选通过程序检查）",
        f"- 输入字符数：{result.source.character_count}；SHA-256：{cell(result.source.sha256)}", "",
        "位置使用原始输入的 Unicode 字符偏移，采用左闭右开区间 `[start, end)`；片段输入的位置相对于片段。",
        "字段表中的位置覆盖整条引文；测量证据表另列数值和单位片段的位置。matched 仅代表原文定位成功，不代表字段归属或计算情景已经核验。", "",
        "字段状态：reported＝已报告；not_reported＝未报告；not_applicable＝任务声明不适用；required_missing＝必要信息缺失。", "",
    ]
    if result.run.usage:
        lines += ["## 模型用量", "", "| 指标 | 数量 |", "| --- | ---: |"]
        lines += [f"| {cell(name)} | {count} |" for name, count in sorted(result.run.usage.items())]
        lines.append("")
    counts = Counter((item.severity, item.code) for item in result.review)
    if counts:
        lines += ["## 复核问题汇总", "", "| 级别 | 问题代码 | 次数 |", "| --- | --- | ---: |"]
        lines += [f"| {'需复核' if severity == 'review' else '信息'} | {cell(code)} | {count} |"
                  for (severity, code), count in sorted(counts.items())]
        lines.append("")
    if result.links:
        ordinals = {candidate.candidate_id: index for index, candidate in enumerate(result.candidates, 1)}
        labels = {"possible_repeat": "可能重复表述", "possible_conflict": "可能冲突", "insufficient_context": "关联依据不足"}
        lines += ["## 候选之间的待确认关联", "",
                  "以下仅是程序发现的待确认线索，没有合并、删除或选定任何候选。数值相同不能单独证明属于同一观测。", "",
                  "| 关联类型 | 候选序号 | 比较字段 | 不同字段 | 尚未明确的字段 |",
                  "| --- | --- | --- | --- | --- |"]
        for link in result.links:
            cells = (labels[link.kind], ", ".join(str(ordinals.get(identifier, identifier)) for identifier in link.candidate_ids),
                     ", ".join(link.compared_fields), ", ".join(link.differing_fields), ", ".join(link.unresolved_fields))
            lines.append("| " + " | ".join(cell(item) for item in cells) + " |")
        lines.append("")
    if not result.candidates:
        lines += ["没有生成候选；这不能证明原文没有可抽取信息。", ""]
    for index, candidate in enumerate(result.candidates, 1):
        state = "待复核" if candidate.needs_review else "结构与证据定位检查通过（仍为候选）"
        lines += [
            f"## 候选 {index}：{cell(candidate.record_type)}", "",
            f"- ID：{cell(candidate.candidate_id)}",
            f"- 状态：{state}",
            f"- 来源分块：{cell(', '.join(candidate.chunk_ids))}", "",
            "| 字段 | 值（JSON 标量） | 原文证据 | 证据状态 | 引文位置 | 字段状态 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for name, field in candidate.fields.items():
            value = json.dumps(field.value, ensure_ascii=False, allow_nan=False)
            cells = (name, value, field.evidence.quote, field.evidence.status, _location(field.evidence),
                     candidate.field_status.get(name, "未记录（旧结果）"))
            lines.append("| " + " | ".join(cell(item) for item in cells) + " |")
        lines.append("")
        for name, status in candidate.field_status.items():
            if name not in candidate.fields:
                lines.append(f"- 未提供字段：{cell(name)}；状态：{cell(status)}")
        for name, measurement in candidate.measurements.items():
            lines += [f"### 测量证据组：{cell(name)}", "",
                      "语义状态：未核验。以下检查不证明参数、数值、单位及条件在语义上属于同一观测。",
                      f"- 关联条件字段：{cell(', '.join(measurement.condition_fields) or '无')}",
                      f"- 单位来源：{'表头' if measurement.unit_source == 'table_header' else '测量上下文'}", "",
                      "| 片段角色 | 原文 | 定位状态 | 字符位置 |", "| --- | --- | --- | --- |"]
            fragments = [("测量上下文（整条引文）", measurement.context),
                         ("数值片段", measurement.value), ("单位片段", measurement.unit)]
            if measurement.unit_context is not None:
                fragments.append(("表头上下文（不代表列关联通过）", measurement.unit_context))
            for role, evidence in fragments:
                cells = (role, evidence.quote, evidence.status, _location(evidence))
                lines.append("| " + " | ".join(cell(item) for item in cells) + " |")
            lines.append("")
            for code in measurement.issues:
                lines.append(f"- 测量检查问题：{cell(code)}")
        for check in candidate.checks:
            if not check.passed:
                field = f"（字段：{cell(check.field)}）" if check.field is not None else ""
                lines.append(f"- 未通过检查：{cell(check.code)}{field}")
        for item in result.review:
            if item.candidate_id == candidate.candidate_id:
                field = f"（字段：{cell(item.field)}）" if item.field is not None else ""
                label = "复核" if item.severity == "review" else "信息"
                lines.append(f"- {label}：{cell(item.code)}{field}：{cell(item.message)}")
        lines.append("")
    return "\n".join(lines)
