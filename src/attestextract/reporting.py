"""Render extraction candidates for local human review without changing results."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from html import escape
import json
import re

from .diagnostics import redact_text
from .models import EvidenceRef, ExtractionResult


def _cell(value: str, secrets: Iterable[str]) -> str:
    """Keep source-derived text inside one Markdown cell or paragraph."""
    text = escape(redact_text(value, secrets), quote=False).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", r"\\\1", text)
    return text.replace("\n", "<br>")


def _location(evidence: EvidenceRef) -> str:
    if evidence.status == "matched" and evidence.start is not None and evidence.end is not None:
        return f"[{evidence.start}, {evidence.end})"
    if evidence.matches:
        return "多个匹配：" + "; ".join(f"[{span.start}, {span.end})" for span in evidence.matches)
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
    lines = [
        "# 抽取候选审阅", "",
        "本报告展示抽取候选。结构或引文定位检查通过不代表事实已核验，也不代表已接受为正式结果。", "",
        f"- 文献：{cell(result.source.name or '未命名输入')}",
        f"- 任务：{cell(result.task.name)}",
        f"- 程序版本：{cell(result.package_version)}；结果格式：{cell(result.schema_version)}",
        f"- 模型：{cell(result.run.model or '未报告')}；模式：{cell(result.run.mode)}",
        f"- 分块：{result.run.chunk_count}；模型响应：{result.run.response_count}；HTTP/后端尝试：{result.run.request_attempts}",
        f"- 候选：{len(result.candidates)}；待复核候选：{pending}；复核项：{len(result.review)}",
        f"- 输入字符数：{result.source.character_count}；SHA-256：{cell(result.source.sha256)}", "",
        "位置使用原始输入的 Unicode 字符偏移，采用左闭右开区间 `[start, end)`；片段输入的位置相对于片段。", "",
    ]
    if result.run.usage:
        lines += ["## 模型用量", "", "| 指标 | 数量 |", "| --- | ---: |"]
        lines += [f"| {cell(name)} | {count} |" for name, count in sorted(result.run.usage.items())]
        lines.append("")
    counts = Counter(item.code for item in result.review)
    if counts:
        lines += ["## 复核问题汇总", "", "| 问题代码 | 次数 |", "| --- | ---: |"]
        lines += [f"| {cell(code)} | {count} |" for code, count in sorted(counts.items())]
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
            "| 字段 | 值（JSON 标量） | 原文证据 | 证据状态 | 字符位置 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for name, field in candidate.fields.items():
            value = json.dumps(field.value, ensure_ascii=False, allow_nan=False)
            cells = (name, value, field.evidence.quote, field.evidence.status, _location(field.evidence))
            lines.append("| " + " | ".join(cell(item) for item in cells) + " |")
        lines.append("")
        for check in candidate.checks:
            if not check.passed:
                field = f"（字段：{cell(check.field)}）" if check.field is not None else ""
                lines.append(f"- 未通过检查：{cell(check.code)}{field}")
        for item in result.review:
            if item.candidate_id == candidate.candidate_id:
                field = f"（字段：{cell(item.field)}）" if item.field is not None else ""
                lines.append(f"- 复核：{cell(item.code)}{field}：{cell(item.message)}")
        lines.append("")
    return "\n".join(lines)
