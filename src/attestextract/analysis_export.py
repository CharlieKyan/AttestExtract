"""Readable, relational exports of proposed bindings; never accepts extracted facts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import sqlite3

from .diagnostics import RunTrace
from .expert_review import _literal


_NOTICE = "模型提出的对象与归属，待人工复核；原机器检查问题仍然有效，不是专家确认。"
_FILES = (
    "analysis.json", "analysis.sqlite", "analysis.md", "objects.json", "object_table.csv",
    "objects.csv", "observations.csv", "relations.csv", "evidence.csv",
    "related_observations.csv", "unbound_observations.csv", "unmatched_observations.csv",
)
_REVIEW_FILES = ("review_groups.json", "review_groups.csv", "evidence_contexts.json", "evidence_contexts.csv")
_CONTEXT_COLUMNS = ("evidence_id", "artifact_id", "passage_id", "start", "end", "text", "model_visible")
_GROUP_COLUMNS = ("job_id", "observation_ids", "record_types", "response_details", "validation_details",
                  "validation_summary", "response_text", "diagnostic")
_PARTIAL_GROUP_COLUMNS = (*_GROUP_COLUMNS, "target_observation_ids", "applied_observation_ids")
_OBSERVATION_COLUMNS = (
    "observation_id", "candidate_id", "source_record", "record_type", "source_kind",
    "entity", "value", "unit", "conditions", "subject_id", "binding_status", "reason",
    "blockers", "evidence_ids", "binding_evidence_ids", "semantic_status",
    "target_match", "property_name", "statistic",
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _index(rows: object, key: str) -> dict[str, dict]:
    if not isinstance(rows, list):
        raise ValueError("Analysis collections must be lists.")
    indexed = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError("Analysis entries require nonempty stable identifiers.")
        if row[key] in indexed:
            raise ValueError("Analysis identifiers must be unique within their collection.")
        indexed[row[key]] = row
    return indexed


def _refs(row: dict, field: str, known: dict) -> list[str]:
    values = row.get(field, [])
    if (not isinstance(values, list) or any(not isinstance(value, str) for value in values)
            or len(set(values)) != len(values) or any(value not in known for value in values)):
        raise ValueError("Analysis evidence references must be unique and present in the snapshot.")
    return values


def _validate(data: dict, *, check_context_text=True) -> None:
    if (not isinstance(data, dict) or data.get("analysis_version") not in {"0.1", "0.2", "0.3", "0.4"}
            or data.get("semantic_status") != "model_proposed"
            or data.get("status") not in {"complete", "partial", "failed", "pending"}):
        raise ValueError("Unsupported analysis snapshot or verification status.")
    task = data.get("task")
    fields = task.get("fields") if isinstance(task, dict) else None
    if not isinstance(fields, dict) or any(not isinstance(key, str) or not key for key in fields):
        raise ValueError("Analysis task requires a field mapping.")
    objects = _index(data.get("objects"), "object_id")
    evidence = _index(data.get("evidence"), "evidence_id")
    observations = _index(data.get("observations"), "observation_id")
    relations = _index(data.get("relations"), "relation_id")
    for item in evidence.values():
        start, end = item.get("start"), item.get("end")
        if not isinstance(item.get("quote"), str):
            raise ValueError("Analysis evidence requires its original quotation.")
        if (start is not None or end is not None) and (
            type(start) is not int or type(end) is not int or start < 0 or end < start
        ):
            raise ValueError("Analysis evidence positions must be paired nonnegative offsets.")
    for item in [*objects.values(), *observations.values(), *relations.values()]:
        if item.get("semantic_status") != "model_proposed":
            raise ValueError("Analysis exports cannot claim human verification.")
    for item in objects.values():
        if (not isinstance(item.get("name"), str) or not isinstance(item.get("kind"), str)
                or type(item.get("is_table_subject")) is not bool):
            raise ValueError("Analysis objects require names, kinds and explicit table membership.")
        _refs(item, "mention_evidence_ids", evidence)
    for item in observations.values():
        subject = item.get("subject_id")
        if subject is not None and (not isinstance(subject, str) or subject not in objects):
            raise ValueError("Observation subjects must reference an existing object.")
        statuses = {"supported", "unknown", "pending", "failed"}
        if data["analysis_version"] in {"0.3", "0.4"}:
            statuses.add("review_required")
        if (item.get("binding_status") not in statuses
                or item.get("binding_status") == "supported" and subject is None):
            raise ValueError("Observation binding status must agree with its subject reference.")
        if not isinstance(item.get("record_type"), str) or item["record_type"] not in fields:
            raise ValueError("Observation targets must be declared by the analysis task.")
        if data["analysis_version"] in {"0.2", "0.3", "0.4"}:
            target_match = item.get("target_match", "unknown")
            if not isinstance(target_match, str) or target_match not in {"supported", "unsupported", "unknown"}:
                raise ValueError("Observation target matching must be supported, unsupported or unknown.")
            for field in ("property_name", "statistic"):
                if item.get(field) is not None and not isinstance(item[field], str):
                    raise ValueError("Analysis property and statistic labels must be strings or null.")
        if (not isinstance(item.get("source_record"), str) or not item["source_record"].startswith("/")
                or not isinstance(item.get("blockers"), list)):
            raise ValueError("Observations require original result pointers and explicit blockers.")
        _refs(item, "evidence_ids", evidence)
        binding_evidence = _refs(item, "binding_evidence_ids", evidence)
        if data["analysis_version"] == "0.4" and (
                item["binding_status"] == "supported" and not binding_evidence
                or item["binding_status"] == "unknown" and subject is not None):
            raise ValueError("Retained binding proposals must satisfy ownership and evidence requirements.")
    for item in relations.values():
        if (not isinstance(item.get("source_id"), str) or item["source_id"] not in objects
                or not isinstance(item.get("target_id"), str) or item["target_id"] not in objects
                or not isinstance(item.get("predicate"), str) or not item["predicate"]):
            raise ValueError("Relations require existing object endpoints and a predicate.")
        _refs(item, "evidence_ids", evidence)
    if data["analysis_version"] in {"0.3", "0.4"}:
        _validate_review_materials(data, observations, evidence, check_context_text=check_context_text)


def _validate_partial_group(group, job, observations):
    targets = _refs(group, "target_observation_ids", observations)
    held = _refs(group, "observation_ids", observations)
    applied = _refs(group, "applied_observation_ids", observations)
    if (not targets or job.get("observation_ids") != targets
            or job.get("review_observation_ids") != held or job.get("error_code") != "proposal_review"
            or set(held).intersection(applied) or set(held).union(applied) != set(targets)
            or any(observations[key]["binding_status"] not in {"supported", "unknown"} for key in applied)):
        raise ValueError("Partial decisions must partition all job targets into retained and held observations.")
    diagnostic = group["diagnostic"]
    reviews = diagnostic.get("reviews")
    if not isinstance(reviews, list) or not reviews:
        raise ValueError("Partial decisions must retain their rejected proposals and issues.")
    affected, paths = set(), set()
    for item in reviews:
        if not isinstance(item, dict):
            raise ValueError("Rejected proposals must be objects.")
        kind, path, raw = item.get("proposal_type"), item.get("path"), item.get("raw_proposal")
        folder = {"object": "objects", "relation": "relations", "binding": "bindings"}.get(kind)
        if (folder is None or not isinstance(path, str) or not re.fullmatch(rf"{folder}\[[0-9]+\]", path)
                or path in paths or not isinstance(raw, dict)
                or not isinstance(item.get("proposal_id"), str) or not item["proposal_id"]
                or not isinstance(item.get("code"), str) or not item["code"]):
            raise ValueError("Rejected proposals require unique canonical paths and original payloads.")
        paths.add(path)
        ids = _refs(item, "observation_ids", observations)
        if not set(ids).issubset(held):
            raise ValueError("Rejected proposal dependencies cannot include retained observations.")
        affected.update(ids)
        if kind == "binding" and (ids != [item["proposal_id"]] or raw.get("observation_id") != item["proposal_id"]):
            raise ValueError("Rejected bindings must identify their original target observation.")
        dependencies = item.get("dependencies")
        issues = item.get("issues")
        if (not isinstance(dependencies, list) or any(not isinstance(value, str) or not value for value in dependencies)
                or len(set(dependencies)) != len(dependencies)
                or not isinstance(issues, list) or not issues):
            raise ValueError("Rejected proposals need explicit dependency lists and validation issues.")
        for issue in issues:
            if (not isinstance(issue, dict) or any(not isinstance(issue.get(key), str) or not issue[key]
                    for key in ("code", "phase", "message", "path"))):
                raise ValueError("Proposal issues require paths, codes, phases and explanations.")
    if affected != set(held):
        raise ValueError("Every held observation must be explained by a rejected proposal.")
    for summary in (group["validation_summary"], diagnostic):
        for key, expected in (("proposal_count", len(reviews)), ("observation_count", len(held)),
                              ("applied_count", len(applied))):
            if type(summary.get(key)) is not int or summary[key] != expected:
                raise ValueError("Partial decision counts must agree with the retained and rejected proposals.")


def _validate_review_materials(data, observations, evidence, *, check_context_text):
    groups = _index(data.get("review_groups"), "job_id")
    jobs = _index(data.get("jobs"), "job_id")
    if data["analysis_version"] == "0.4":
        targets = set()
        for job in jobs.values():
            ids = _refs(job, "observation_ids", observations)
            if not ids or targets.intersection(ids):
                raise ValueError("Verification jobs must have nonempty, disjoint target sets.")
            targets.update(ids)
        if targets != set(observations):
            raise ValueError("Verification job targets must cover every original observation.")
    held = set()
    for key, group in groups.items():
        job = jobs.get(key, {})
        ids = _refs(group, "observation_ids", observations)
        if (job.get("status") != "review_required" or held.intersection(ids)
                or data["analysis_version"] == "0.3" and (not ids or job.get("observation_ids") != ids)):
            raise ValueError("Review groups must reference disjoint isolated jobs and observations.")
        held.update(ids)
        expected_fields = sorted({observations[value]["record_type"] for value in ids})
        if group.get("record_types") != expected_fields:
            raise ValueError("Review groups must retain all affected target fields.")
        for value in ids:
            item = observations[value]
            if (item["binding_status"] != "review_required" or item.get("subject_id") is not None
                    or item.get("binding_evidence_ids") or item.get("target_match") != "unknown"):
                raise ValueError("Isolated proposals cannot enter applied observation bindings.")
        for field, folder, suffix in (("response_details", "responses", "txt"),
                                      ("validation_details", "validation", "errors.json")):
            path = group.get(field)
            if path is not None and (not isinstance(path, str) or not re.fullmatch(
                    rf"analysis/{folder}/{re.escape(key)}\.step-[0-9]+\.{re.escape(suffix)}", path)):
                raise ValueError("Review artifact references must use canonical diagnostic paths.")
        code = "proposal_review" if data["analysis_version"] == "0.4" else "quote_ambiguous"
        if not isinstance(group.get("validation_summary"), dict) or group["validation_summary"].get("code") != code:
            raise ValueError("Review groups must identify the applicable validation policy.")
        if (not isinstance(group.get("response_text"), str) or not group["response_text"]
                or not isinstance(group.get("diagnostic"), dict)
                or group["diagnostic"].get("code") != code):
            raise ValueError("Review groups must retain the original answer and validation diagnostic.")
        if data["analysis_version"] == "0.4":
            _validate_partial_group(group, job, observations)
    if (held != {key for key, item in observations.items() if item["binding_status"] == "review_required"}
            or set(groups) != {key for key, job in jobs.items() if job.get("status") == "review_required"}
            or groups and data["status"] not in {"partial", "failed"}):
        raise ValueError("Isolated review groups must remain visible as incomplete analysis.")
    contexts = data.get("evidence_contexts")
    if not isinstance(contexts, list):
        raise ValueError("Display evidence contexts must be an explicit collection.")
    seen = set()
    for item in contexts:
        if not isinstance(item, dict):
            raise ValueError("Display evidence contexts must be objects.")
        key, artifact = item.get("evidence_id"), item.get("artifact_id")
        identity = (key, artifact)
        if (not isinstance(key, str) or key not in evidence or not isinstance(artifact, str)
                or not re.fullmatch(r"verify-[0-9]+\.step-[0-9]+", artifact) or identity in seen):
            raise ValueError("Display contexts need unique evidence and verification-step references.")
        seen.add(identity)
        entry = evidence[key]
        left, right, text = item.get("start"), item.get("end"), item.get("text")
        if (type(left) is not int or type(right) is not int or not 0 <= left < right
                or type(entry.get("start")) is not int or type(entry.get("end")) is not int
                or not left <= entry["start"] < entry["end"] <= right
                or not isinstance(text, str) or type(item.get("model_visible")) is not bool
                or not isinstance(item.get("passage_id"), str)):
            raise ValueError("Display context must contain its original evidence span and visibility label.")
        if check_context_text and (len(text) != right - left
                or text[entry["start"] - left:entry["end"] - left] != entry["quote"]):
            raise ValueError("Display context cannot rewrite the original quoted evidence.")


def _direct(item: dict) -> bool:
    return item.get("subject_id") is not None and item["binding_status"] == "supported"


def _object_view(data: dict) -> dict:
    rows, related = [], []
    by_id = {}
    for item in data["objects"]:
        row = {**item, "fields": {field: [] for field in data["task"]["fields"]}}
        by_id[item["object_id"]] = row
        (rows if item["is_table_subject"] else related).append(row)
    unbound, related_observations, unmatched = [], [], []
    for item in data["observations"]:
        if not _direct(item):
            unbound.append(item)
            continue
        if data["analysis_version"] in {"0.2", "0.3", "0.4"} and item.get("target_match") != "supported":
            unmatched.append(item)
            continue
        subject = by_id[item["subject_id"]]
        subject["fields"][item["record_type"]].append(item)
        if not subject["is_table_subject"]:
            related_observations.append(item)
    return {"verification": _NOTICE, "semantic_status": "model_proposed",
            "table_subject": data.get("table_subject"), "objects": rows, "related_objects": related,
            "related_observations": related_observations, "unbound_observations": unbound,
            "unmatched_observations": unmatched}


def _csv_value(value: object) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else _json(value)
    # Spreadsheet applications may execute formulas even in quoted CSV fields.
    # Preserve raw strings in JSON/SQLite; the apostrophe is presentation only.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def _write_csv(path: Path, columns: tuple | list, rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow([_csv_value(column) for column in columns])
        writer.writerows([_csv_value(row.get(column)) for column in columns] for row in rows)


def _database(path: Path, data: dict) -> None:
    with path.open("xb"):
        pass
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
            CREATE TABLE objects (object_id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                scope_json TEXT NOT NULL, is_table_subject INTEGER NOT NULL, semantic_status TEXT NOT NULL);
            CREATE TABLE evidence (evidence_id TEXT PRIMARY KEY, quote TEXT NOT NULL, start INTEGER, end INTEGER);
            CREATE TABLE observations (observation_id TEXT PRIMARY KEY, candidate_id TEXT,
                source_record TEXT NOT NULL, record_type TEXT NOT NULL, source_kind TEXT, entity_json TEXT,
                value_json TEXT NOT NULL, unit_json TEXT NOT NULL, conditions_json TEXT NOT NULL,
                subject_id TEXT REFERENCES objects(object_id), binding_status TEXT NOT NULL,
                reason TEXT, blockers_json TEXT NOT NULL, semantic_status TEXT NOT NULL,
                target_match TEXT, property_name TEXT, statistic TEXT);
            CREATE TABLE relations (relation_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES objects(object_id),
                target_id TEXT NOT NULL REFERENCES objects(object_id), predicate TEXT NOT NULL, semantic_status TEXT NOT NULL);
            CREATE TABLE object_evidence (object_id TEXT REFERENCES objects(object_id),
                evidence_id TEXT REFERENCES evidence(evidence_id), PRIMARY KEY (object_id, evidence_id));
            CREATE TABLE observation_evidence (observation_id TEXT REFERENCES observations(observation_id),
                evidence_id TEXT REFERENCES evidence(evidence_id), role TEXT NOT NULL,
                PRIMARY KEY (observation_id, evidence_id, role));
            CREATE TABLE relation_evidence (relation_id TEXT REFERENCES relations(relation_id),
                evidence_id TEXT REFERENCES evidence(evidence_id), PRIMARY KEY (relation_id, evidence_id));
        """)
        if data["analysis_version"] in {"0.3", "0.4"}:
            connection.executescript("""
                CREATE TABLE review_groups (job_id TEXT PRIMARY KEY, observation_ids_json TEXT NOT NULL,
                    record_types_json TEXT NOT NULL, response_details TEXT, validation_details TEXT,
                    validation_summary_json TEXT NOT NULL, response_text TEXT NOT NULL, diagnostic_json TEXT NOT NULL);
                CREATE TABLE evidence_contexts (evidence_id TEXT REFERENCES evidence(evidence_id),
                    artifact_id TEXT NOT NULL, passage_id TEXT NOT NULL, start INTEGER NOT NULL,
                    end INTEGER NOT NULL, text TEXT NOT NULL, model_visible INTEGER NOT NULL,
                    PRIMARY KEY (evidence_id, artifact_id));
            """)
            if data["analysis_version"] == "0.4":
                connection.execute("ALTER TABLE review_groups ADD COLUMN target_observation_ids_json TEXT NOT NULL DEFAULT '[]'")
                connection.execute("ALTER TABLE review_groups ADD COLUMN applied_observation_ids_json TEXT NOT NULL DEFAULT '[]'")
        with connection:
            connection.executemany("INSERT INTO metadata VALUES (?, ?)",
                                   [(key, _json(value)) for key, value in data.items()
                                    if key not in {"objects", "observations", "relations", "evidence",
                                                   "review_groups", "evidence_contexts"}])
            connection.executemany("INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?)", [
                (item["object_id"], item["name"], item["kind"], _json(item.get("scope")),
                 int(item["is_table_subject"]), item["semantic_status"]) for item in data["objects"]])
            connection.executemany("INSERT INTO evidence VALUES (?, ?, ?, ?)", [
                (item["evidence_id"], item["quote"], item.get("start"), item.get("end")) for item in data["evidence"]])
            connection.executemany("INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                (item["observation_id"], item.get("candidate_id"), item["source_record"], item["record_type"],
                 item.get("source_kind"), _json(item.get("entity")), _json(item.get("value")),
                 _json(item.get("unit")), _json(item.get("conditions")), item.get("subject_id"),
                 item["binding_status"], item.get("reason"), _json(item["blockers"]), item["semantic_status"],
                 item.get("target_match"), item.get("property_name"), item.get("statistic"))
                for item in data["observations"]])
            connection.executemany("INSERT INTO relations VALUES (?, ?, ?, ?, ?)", [
                (item["relation_id"], item["source_id"], item["target_id"], item["predicate"], item["semantic_status"])
                for item in data["relations"]])
            connection.executemany("INSERT INTO object_evidence VALUES (?, ?)", [
                (item["object_id"], key) for item in data["objects"] for key in item.get("mention_evidence_ids", [])])
            connection.executemany("INSERT INTO observation_evidence VALUES (?, ?, ?)", [
                (item["observation_id"], key, role) for item in data["observations"]
                for field, role in (("evidence_ids", "observation"), ("binding_evidence_ids", "binding"))
                for key in item.get(field, [])])
            connection.executemany("INSERT INTO relation_evidence VALUES (?, ?)", [
                (item["relation_id"], key) for item in data["relations"] for key in item.get("evidence_ids", [])])
            if data["analysis_version"] in {"0.3", "0.4"}:
                connection.executemany("INSERT INTO review_groups (job_id, observation_ids_json, record_types_json, "
                    "response_details, validation_details, validation_summary_json, response_text, diagnostic_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
                    (item["job_id"], _json(item["observation_ids"]), _json(item["record_types"]),
                     item.get("response_details"), item.get("validation_details"), _json(item["validation_summary"]),
                     item["response_text"], _json(item["diagnostic"]))
                    for item in data["review_groups"]])
                if data["analysis_version"] == "0.4":
                    connection.executemany("UPDATE review_groups SET target_observation_ids_json=?, "
                        "applied_observation_ids_json=? WHERE job_id=?", [
                            (_json(item["target_observation_ids"]), _json(item["applied_observation_ids"]), item["job_id"])
                            for item in data["review_groups"]])
                connection.executemany("INSERT INTO evidence_contexts VALUES (?, ?, ?, ?, ?, ?, ?)", [
                    (item["evidence_id"], item["artifact_id"], item["passage_id"], item["start"], item["end"],
                     item["text"], int(item["model_visible"])) for item in data["evidence_contexts"]])
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Analysis database contains invalid references.")
    finally:
        connection.close()


def _verification_stop_reason(job: dict) -> str | None:
    step = next((item for item in reversed(job.get("steps", []))
                 if item.get("error") or item.get("validation_summary")), {})
    summary = step.get("validation_summary")
    if not isinstance(summary, dict):
        # Historical snapshots retain their original diagnostic presentation.
        return job.get("error") or job.get("reason") or step.get("error") or job.get("error_code")
    if summary.get("category") == "internal_state":
        reason = "原文或内部状态校验失败；不能当作模型回答错误纠正。"
    elif summary.get("code") == "quote_ambiguous":
        reason = "引文存在多个可见位置；整组提议隔离待复核，未应用到对象、关系或汇总表。"
    elif summary.get("code") == "proposal_review":
        reason = "不合格提议及其明确引用依赖未应用；同组独立通过检查的提议已保留。原始观测未删除，仍需复核。"
    elif summary.get("code") == "binding_scope_mismatch":
        def count(key):
            value = summary.get(key)
            return value if type(value) is int and value >= 0 else "未报告"

        reason = (f"作答范围不符：期望{count('expected_count')}条，返回{count('returned_count')}条；"
                  f"遗漏{count('missing_count')}条，重复ID {count('duplicate_count')}个，"
                  f"越界{count('unexpected_count')}条（其中{count('reference_count')}条来自只读参考）。")
    else:
        reason = {"quote_not_found": "引文未在指定原文中找到；本次提议未应用。",
                  "occurrence_out_of_range": "引文出现序号越界；本次提议未应用。",
                  "not_visible": "引文不在该查证任务实际可见的片段中；本次提议未应用。"}.get(
                      summary.get("code"), "模型回答未通过查证协议检查；本次提议未应用。")
    details = step.get("validation_details")
    if isinstance(details, str) and re.fullmatch(r"analysis/validation/[A-Za-z0-9_.-]+\.errors\.json", details):
        reason += " 详情：diagnostics/" + details
    return reason


def _markdown(data: dict, view: dict) -> str:
    evidence = {item["evidence_id"]: item for item in data["evidence"]}

    def show(value):
        return _literal(value) if value is not None else "未报告"

    def observation(item):
        quotes = [evidence[key]["quote"] for key in dict.fromkeys(
            [*item.get("evidence_ids", []), *item.get("binding_evidence_ids", [])])]
        problems = "; ".join(str(blocker.get("message") or blocker.get("code") or blocker)
                             if isinstance(blocker, dict) else str(blocker) for blocker in item["blockers"])
        if item["binding_status"] == "review_required" and data["analysis_version"] == "0.4":
            problems = "归属提议不合格、未应用：" + str(item.get("reason") or "请查看提议校验详情") + ("；" + problems if problems else "")
        elif not _direct(item):
            problems = "归属未确定：" + str(item.get("reason") or item["binding_status"]) + ("；" + problems if problems else "")
        if data["analysis_version"] in {"0.2", "0.3", "0.4"}:
            target = item.get("target_match", "unknown")
            matching = {"supported": "模型认为符合目标字段", "unsupported": "不符合目标字段", "unknown": "目标字段匹配未确定"}[target]
            labels = "; ".join(f"{label}：{item[key]}" for key, label in
                               (("property_name", "识别属性"), ("statistic", "统计口径")) if item.get(key))
            problems = "；".join(value for value in (matching, labels, problems) if value)
        values = (item["record_type"], item.get("value"), item.get("unit"), item.get("conditions"),
                  item.get("entity"), " / ".join(quotes) or "缺少可定位证据",
                  problems or "程序未报告阻断；仍待人工复核", item["observation_id"] + " → " + item["source_record"])
        return "| " + " | ".join(show(value) for value in values) + " |"

    header = ["| 字段 | 原值 | 单位 | 条件 | 原文对象 | 依据（各片段独立） | 检查 | 来源 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    status = {"complete": "完成（仅模型分析）", "partial": "部分完成", "failed": "失败", "pending": "待处理"}[data["status"]]
    lines = ["# 对象与观测分析", "", _NOTICE, "", "运行状态：" + status + " / " + show(data["status"]),
             "目标表主体：" + show(data.get("table_subject")),
             "同名对象按不同ID展示。下面仅展示直接绑定的观测；不沿关系传播数值。", ""]
    if data["analysis_version"] in {"0.2", "0.3", "0.4"}:
        lines += ["目标表仅纳入模型判断归属有支持且字段匹配有支持的观测；仍不是专家确认。其他观测单独保留。", ""]
        observations = data["observations"]
        binding_counts = {status: sum(item["binding_status"] == status for item in observations)
                          for status in ("supported", "unknown", "pending", "failed", "review_required")}
        target_counts = {status: sum(item.get("target_match", "unknown") == status for item in observations)
                         for status in ("supported", "unsupported", "unknown")}
        direct_subjects = {item["subject_id"] for item in observations if _direct(item)}
        unbound_objects = sum(item["object_id"] not in direct_subjects for item in data["objects"])
        lines += ["## 查证状态摘要", "",
                  f"观测共{len(observations)}条；模型认为归属有支持{binding_counts['supported']}条，"
                  f"归属未知{binding_counts['unknown']}条，待处理{binding_counts['pending']}条，查证失败{binding_counts['failed']}条。",
                  f"目标字段匹配：模型认为有支持{target_counts['supported']}条，不支持{target_counts['unsupported']}条，"
                  f"未确定{target_counts['unknown']}条。",
                  f"对象共{len(data['objects'])}个；未被归属明确的观测直接引用的对象{unbound_objects}个。"
                  "这些对象可能参与关系或仍待查证，不据此删除或判定错误。",
                  "以上是独立维度的状态计数，不是准确率；运行完成不表示所有归属明确或通过专家确认。", ""]
        if data["analysis_version"] == "0.3":
            groups = data["review_groups"]
            lines += [f"整组隔离待复核：{len(groups)}组、{binding_counts['review_required']}条观测。"
                      "隔离提议不会填入汇总表或作为后续已登记对象；不代表原文没有报告这些字段。", "",
                      "## 整组隔离待复核", "",
                      "| 任务 | 隔离观测数 | 涉及字段 | 原回答 | 定位详情 |",
                      "| --- | --- | --- | --- | --- |"]
            for group in groups:
                values = (group["job_id"], len(group["observation_ids"]), ", ".join(group["record_types"]),
                          "diagnostics/" + group["response_details"] if group.get("response_details") else "review_groups.json 的 response_text",
                          "diagnostics/" + group["validation_details"] if group.get("validation_details") else "review_groups.json 的 diagnostic")
                lines.append("| " + " | ".join(show(value) for value in values) + " |")
            if not groups:
                lines += ["", "无。"]
            else:
                lines += ["", "原回答完整保留；候选位置表示可能的位置，不表示模型同时引用全部位置。"
                          "普通恢复保留隔离状态，不会自动重答或把提议提升为已应用结果。"]
            lines.append("")
        elif data["analysis_version"] == "0.4":
            groups = data["review_groups"]
            proposal_count = sum(len(group["diagnostic"]["reviews"]) for group in groups)
            lines += [f"逐提议隔离：{len(groups)}个任务涉及{proposal_count}项不合格提议，"
                      f"{binding_counts['review_required']}条观测的归属暂未应用。"
                      "其他独立通过检查的提议保留；不能把隔离与合法未知混为一谈。",
                      "所有原始观测保留；不合格对象、关系或归属不进入分析表，也不代表原文未报告。",
                      "只跟踪显式对象引用依赖，不推断未声明的语义依赖；可检索不等于科学归属正确。", "",
                      "## 未应用的提议与受影响观测", "",
                      "| 任务 | 目标观测 | 保留有归属 | 保留未知 | 隔离观测 | 不合格提议 | 涉及字段 |",
                      "| --- | --- | --- | --- | --- | --- | --- |"]
            by_observation = {item["observation_id"]: item for item in observations}
            for group in groups:
                kept = [by_observation[key] for key in group["applied_observation_ids"]]
                values = (group["job_id"], len(group["target_observation_ids"]),
                          sum(item["binding_status"] == "supported" for item in kept),
                          sum(item["binding_status"] == "unknown" for item in kept),
                          len(group["observation_ids"]), len(group["diagnostic"]["reviews"]),
                          ", ".join(group["record_types"]) or "仅对象/关系提议")
                lines.append("| " + " | ".join(show(value) for value in values) + " |")
            if not groups:
                lines += ["", "无。"]
            for group in groups:
                lines += ["", "任务：" + show(group["job_id"]),
                          "原回答：" + show("diagnostics/" + group["response_details"] if group.get("response_details")
                              else "review_groups.json 的 response_text"),
                          "已发现的校验问题与未应用原提议：" + show("diagnostics/" + group["validation_details"]
                              if group.get("validation_details") else "review_groups.json 的 diagnostic"), "",
                          "| 提议位置 | 问题代码 | 受影响观测 | 显式依赖 |", "| --- | --- | --- | --- |"]
                for item in group["diagnostic"]["reviews"]:
                    values = (item["path"], item["code"], ", ".join(item["observation_ids"]), ", ".join(item["dependencies"]))
                    lines.append("| " + " | ".join(show(value) for value in values) + " |")
            lines += ["", "普通恢复保留已应用与未应用的划分，不会自动修改回答或重新调用模型。"
                      "请结合未归属、隔离和未处理数量评估分析表覆盖；这些数量不是准确率或召回率。", ""]
    for title, collection in (("目标对象", view["objects"]), ("相关对象（不作为目标表行）", view["related_objects"])):
        lines += ["## " + title, ""]
        for row in collection:
            lines += ["### " + show(row["name"]) + " · " + show(row["object_id"]), "",
                      "类型：" + show(row["kind"]) + "；范围：" + show(row.get("scope")), "", *header]
            items = [item for values in row["fields"].values() for item in values]
            lines.extend(observation(item) for item in items)
            if not items:
                lines += ["", "没有直接绑定的观测；不能据此认定原文未报告。"]
            lines.append("")
    lines += ["## 尚未确定归属的观测", "", *header]
    lines.extend(observation(item) for item in view["unbound_observations"])
    if not view["unbound_observations"]:
        lines += ["", "无。"]
    if data["analysis_version"] in {"0.2", "0.3", "0.4"}:
        lines += ["", "## 已有归属但尚不能填入目标字段的观测", "", *header]
        lines.extend(observation(item) for item in view["unmatched_observations"])
        if not view["unmatched_observations"]:
            lines += ["", "无。"]
    lines += ["", "## 对象关系（模型建议）", "", "| 起点ID | 关系 | 终点ID | 原文依据 |",
              "| --- | --- | --- | --- |"]
    for item in data["relations"]:
        values = (item["source_id"], item["predicate"], item["target_id"],
                  " / ".join(evidence[key]["quote"] for key in item.get("evidence_ids", [])))
        lines.append("| " + " | ".join(show(value) for value in values) + " |")
    if data["analysis_version"] in {"0.3", "0.4"}:
        lines += ["", "## 引文与审阅上下文", "",
                  "原始引文保持模型选择的范围。上下文由程序从原文补充展示，不参与证据或对象ID，"
                  "不能用它代替模型原本的依据。位置为原文Unicode偏移；脱敏文本长度可能不同。", ""]
        for context in data["evidence_contexts"]:
            visibility = ("整个展示范围曾提供给该查证任务" if context["model_visible"] else
                          "含程序补充上下文；未确认整个展示范围曾提供给该查证任务")
            lines += ["- 证据：" + show(context["evidence_id"]) + "；查证步骤：" + show(context["artifact_id"]),
                      "  - 原始引文：" + show(evidence[context["evidence_id"]]["quote"]),
                      "  - 展示上下文：" + show(context["text"]),
                      f"  - 可见范围：{visibility}；原文位置[{context['start']}, {context['end']})。", ""]
        if not data["evidence_contexts"]:
            lines += ["无已应用查证引文的展示上下文；原抽取证据仍在观测记录中。", ""]
    lines += ["", "## 其他待核对材料", "", "原格式隔离记录：" + str(len(data.get("quarantined", []))),
              "完整隔离声明、任务记录及复核项保存在 analysis.json；未应用提议不会填入目标表。",
              "SQLite / JSON 保留原值和引用；CSV 对可能执行公式的文本添加前导单引号，仅改变展示。", ""]
    if data["analysis_version"] in {"0.2", "0.3", "0.4"}:
        lines += ["## 有限查证任务", "", "| 任务 | 状态 | 模型调用 | 停止原因 |", "| --- | --- | --- | --- |"]
        for job in data.get("jobs", []):
            values = (job.get("job_id"), job.get("status"), job.get("attempts", 0),
                      _verification_stop_reason(job))
            lines.append("| " + " | ".join(show(value) for value in values) + " |")
        if any(job.get("status") == "failed" for job in data.get("jobs", [])):
            lines += ["", "本次失败后未自动重试或纠正回答。普通恢复会保留失败任务；"
                      "可能继续处理待执行任务，但不会修复已失败任务。请先审查失败原因。"]
        lines += ["", "search/read 是对本篇原文的有限查找；未找到不代表全文不存在。详细请求与查找记录保存在 diagnostics/analysis。", ""]
    return "\n".join(lines)


def save_analysis(analysis: dict, directory: Path, secrets=()) -> None:
    """Create independent exports exclusively, preserving all direct observations."""
    # Round-trip first to prohibit non-JSON values and avoid changing caller data.
    data = json.loads(_json(analysis))
    _validate(data)
    data = RunTrace(None, secrets=secrets)._mask(data)
    _validate(data, check_context_text=False)  # Redaction may change lengths, never identifiers or source offsets.
    view = _object_view(data)
    markdown = _markdown(data, view)
    directory = Path(directory)
    filenames = _FILES + (_REVIEW_FILES if data["analysis_version"] in {"0.3", "0.4"} else ())
    if any((directory / name).exists() or (directory / name).is_symlink() for name in filenames):
        raise FileExistsError("Analysis output already exists; choose a new directory.")
    directory.mkdir(parents=True, exist_ok=True)
    for filename, payload in (("analysis.json", data), ("objects.json", view)):
        with (directory / filename).open("x", encoding="utf-8", newline="") as stream:
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
    _write_csv(directory / "objects.csv", ("object_id", "name", "kind", "scope", "is_table_subject",
                                          "mention_evidence_ids", "semantic_status"), data["objects"])
    for filename, rows in (("observations.csv", data["observations"]),
                           ("related_observations.csv", view["related_observations"]),
                           ("unbound_observations.csv", view["unbound_observations"]),
                           ("unmatched_observations.csv", view["unmatched_observations"])):
        _write_csv(directory / filename, _OBSERVATION_COLUMNS, rows)
    _write_csv(directory / "relations.csv", ("relation_id", "source_id", "target_id", "predicate",
                                            "evidence_ids", "semantic_status"), data["relations"])
    _write_csv(directory / "evidence.csv", ("evidence_id", "quote", "start", "end"), data["evidence"])
    if data["analysis_version"] in {"0.3", "0.4"}:
        for stem in ("review_groups", "evidence_contexts"):
            with (directory / (stem + ".json")).open("x", encoding="utf-8", newline="") as stream:
                json.dump(data[stem], stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.write("\n")
        group_columns = _PARTIAL_GROUP_COLUMNS if data["analysis_version"] == "0.4" else _GROUP_COLUMNS
        _write_csv(directory / "review_groups.csv", group_columns, data["review_groups"])
        _write_csv(directory / "evidence_contexts.csv", _CONTEXT_COLUMNS, data["evidence_contexts"])
    # Task field names are arbitrary; an extracted field named "name" must
    # neither replace the object label nor produce duplicate CSV columns.
    metadata = {}
    for key in ("object_id", "name", "kind", "scope", "semantic_status"):
        label = key
        while label in data["task"]["fields"] or label in metadata.values():
            label = "metadata." + label
        metadata[key] = label
    columns = (*metadata.values(), *data["task"]["fields"])
    _write_csv(directory / "object_table.csv", columns, [
        {**{label: row.get(key) for key, label in metadata.items()}, **row["fields"]}
        for row in view["objects"]])
    _database(directory / "analysis.sqlite", data)
    with (directory / "analysis.md").open("x", encoding="utf-8", newline="") as stream:
        stream.write(markdown)
