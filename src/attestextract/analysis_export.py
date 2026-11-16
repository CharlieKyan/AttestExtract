"""Readable, relational exports of proposed bindings; never accepts extracted facts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import sqlite3

from .diagnostics import RunTrace
from .expert_review import _literal


_NOTICE = "模型提出的对象与归属，待人工复核；原机器检查问题仍然有效，不是专家确认。"
_FILES = (
    "analysis.json", "analysis.sqlite", "analysis.md", "objects.json", "object_table.csv",
    "objects.csv", "observations.csv", "relations.csv", "evidence.csv",
    "related_observations.csv", "unbound_observations.csv",
)
_OBSERVATION_COLUMNS = (
    "observation_id", "candidate_id", "source_record", "record_type", "source_kind",
    "entity", "value", "unit", "conditions", "subject_id", "binding_status", "reason",
    "blockers", "evidence_ids", "binding_evidence_ids", "semantic_status",
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


def _validate(data: dict) -> None:
    if (not isinstance(data, dict) or data.get("analysis_version") != "0.1"
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
        if (item.get("binding_status") not in {"supported", "unknown", "pending", "failed"}
                or item.get("binding_status") == "supported" and subject is None):
            raise ValueError("Observation binding status must agree with its subject reference.")
        if not isinstance(item.get("record_type"), str) or item["record_type"] not in fields:
            raise ValueError("Observation targets must be declared by the analysis task.")
        if (not isinstance(item.get("source_record"), str) or not item["source_record"].startswith("/")
                or not isinstance(item.get("blockers"), list)):
            raise ValueError("Observations require original result pointers and explicit blockers.")
        _refs(item, "evidence_ids", evidence)
        _refs(item, "binding_evidence_ids", evidence)
    for item in relations.values():
        if (not isinstance(item.get("source_id"), str) or item["source_id"] not in objects
                or not isinstance(item.get("target_id"), str) or item["target_id"] not in objects
                or not isinstance(item.get("predicate"), str) or not item["predicate"]):
            raise ValueError("Relations require existing object endpoints and a predicate.")
        _refs(item, "evidence_ids", evidence)


def _direct(item: dict) -> bool:
    return item.get("subject_id") is not None and item["binding_status"] == "supported"


def _object_view(data: dict) -> dict:
    rows, related = [], []
    by_id = {}
    for item in data["objects"]:
        row = {**item, "fields": {field: [] for field in data["task"]["fields"]}}
        by_id[item["object_id"]] = row
        (rows if item["is_table_subject"] else related).append(row)
    unbound, related_observations = [], []
    for item in data["observations"]:
        if not _direct(item):
            unbound.append(item)
            continue
        subject = by_id[item["subject_id"]]
        subject["fields"][item["record_type"]].append(item)
        if not subject["is_table_subject"]:
            related_observations.append(item)
    return {"verification": _NOTICE, "semantic_status": "model_proposed",
            "table_subject": data.get("table_subject"), "objects": rows, "related_objects": related,
            "related_observations": related_observations, "unbound_observations": unbound}


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
                reason TEXT, blockers_json TEXT NOT NULL, semantic_status TEXT NOT NULL);
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
        with connection:
            connection.executemany("INSERT INTO metadata VALUES (?, ?)",
                                   [(key, _json(value)) for key, value in data.items()
                                    if key not in {"objects", "observations", "relations", "evidence"}])
            connection.executemany("INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?)", [
                (item["object_id"], item["name"], item["kind"], _json(item.get("scope")),
                 int(item["is_table_subject"]), item["semantic_status"]) for item in data["objects"]])
            connection.executemany("INSERT INTO evidence VALUES (?, ?, ?, ?)", [
                (item["evidence_id"], item["quote"], item.get("start"), item.get("end")) for item in data["evidence"]])
            connection.executemany("INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                (item["observation_id"], item.get("candidate_id"), item["source_record"], item["record_type"],
                 item.get("source_kind"), _json(item.get("entity")), _json(item.get("value")),
                 _json(item.get("unit")), _json(item.get("conditions")), item.get("subject_id"),
                 item["binding_status"], item.get("reason"), _json(item["blockers"]), item["semantic_status"])
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
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Analysis database contains invalid references.")
    finally:
        connection.close()


def _markdown(data: dict, view: dict) -> str:
    evidence = {item["evidence_id"]: item for item in data["evidence"]}

    def show(value):
        return _literal(value) if value is not None else "未报告"

    def observation(item):
        quotes = [evidence[key]["quote"] for key in dict.fromkeys(
            [*item.get("evidence_ids", []), *item.get("binding_evidence_ids", [])])]
        problems = "; ".join(str(blocker.get("message") or blocker.get("code") or blocker)
                             if isinstance(blocker, dict) else str(blocker) for blocker in item["blockers"])
        if not _direct(item):
            problems = "归属未确定：" + str(item.get("reason") or item["binding_status"]) + ("；" + problems if problems else "")
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
    lines += ["", "## 对象关系（模型建议）", "", "| 起点ID | 关系 | 终点ID | 原文依据 |",
              "| --- | --- | --- | --- |"]
    for item in data["relations"]:
        values = (item["source_id"], item["predicate"], item["target_id"],
                  " / ".join(evidence[key]["quote"] for key in item.get("evidence_ids", [])))
        lines.append("| " + " | ".join(show(value) for value in values) + " |")
    lines += ["", "## 其他待核对材料", "", "原格式隔离记录：" + str(len(data.get("quarantined", []))),
              "完整隔离声明、任务记录及复核项保存在 analysis.json；隔离声明不会填入目标表。",
              "SQLite / JSON 保留原值和引用；CSV 对可能执行公式的文本添加前导单引号，仅改变展示。", ""]
    return "\n".join(lines)


def save_analysis(analysis: dict, directory: Path, secrets=()) -> None:
    """Create independent exports exclusively, preserving all direct observations."""
    # Round-trip first to prohibit non-JSON values and avoid changing caller data.
    data = json.loads(_json(analysis))
    _validate(data)
    data = RunTrace(None, secrets=secrets)._mask(data)
    _validate(data)  # Redaction must not silently collapse identifiers.
    view = _object_view(data)
    markdown = _markdown(data, view)
    directory = Path(directory)
    if any((directory / name).exists() or (directory / name).is_symlink() for name in _FILES):
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
                           ("unbound_observations.csv", view["unbound_observations"])):
        _write_csv(directory / filename, _OBSERVATION_COLUMNS, rows)
    _write_csv(directory / "relations.csv", ("relation_id", "source_id", "target_id", "predicate",
                                            "evidence_ids", "semantic_status"), data["relations"])
    _write_csv(directory / "evidence.csv", ("evidence_id", "quote", "start", "end"), data["evidence"])
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
