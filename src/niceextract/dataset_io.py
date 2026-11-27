"""Local I/O for immutable, source-linked dataset snapshots."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from uuid import uuid4

from .dataset_annotations import display_annotations
from .errors import ConfigurationError
from .measurement_syntax import _range_match
from .storage import _reject_constant, _unique_object


_USE_LABELS = {"model_input": "模型设定值", "process_setting": "工艺设定值",
               "derived_setting": "计算设定值", "measured": "实测值",
               "classification_threshold": "分类阈值", "sensitivity_input_range": "敏感性分析范围",
               "boundary_condition": "边界条件"}


def _read_json_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except ValueError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def _check_keys(options) -> None:
    keys = {"identity_fields", "subject_label_field", "row_subject", "field_policies"}
    if not isinstance(options, dict) or options.keys() - keys:
        raise ConfigurationError("Dataset options accept only identity_fields, subject_label_field, row_subject and field_policies.")


def load_dataset_options(path: Path | None) -> dict:
    options = _read_json_object(path) if path is not None else {}
    _check_keys(options)
    return options


def check_dataset_request(task, options) -> None:
    if options is None:
        return
    from .dataset import validate_dataset_options
    _check_keys(options)
    if task.input_materials is None:
        raise ConfigurationError("dataset_options requires a simple task.")
    validate_dataset_options(list(task.input_materials.field_list), **options)


def assemble_from_files(source_path: str | Path, run_dir: str | Path, *, output_dir=None,
                        result_path=None, analysis_path=None, raw_only: bool = False, **options) -> dict:
    """Rebuild a dataset from one source and its frozen run without loading credentials."""
    from .dataset import assemble_dataset

    run_dir = Path(run_dir)
    # read_text() performs universal newline translation; evidence offsets must survive CRLF.
    source = Path(source_path).read_bytes().decode("utf-8")
    result = _read_json_object(Path(result_path) if result_path is not None else run_dir / "result.json")
    if type(raw_only) is not bool or raw_only and analysis_path is not None:
        raise ValueError("raw_only must be a boolean and cannot select an analysis_path when enabled.")
    analysis = None
    if not raw_only:
        analysis = _read_json_object(Path(analysis_path) if analysis_path is not None
                                     else run_dir / "analysis" / "analysis.json")
        if analysis_path is None and result_path is None:
            from .verification_replay import revalidate_archived_relations
            analysis = revalidate_archived_relations(source, analysis, run_dir / "diagnostics" / "analysis")
    manifest = run_dir / "diagnostics" / "manifest.json"
    if not raw_only and analysis_path is None and result_path is None and manifest.is_file():
        version = _read_json_object(manifest).get("package_version", "")
        prefix = "0.1.0.dev"
        if isinstance(version, str) and version.startswith(prefix):
            release = version[len(prefix):]
            if release.isdecimal() and int(release) < 57:
                options.setdefault("reader_view", True)
    if result_path is None and analysis_path is None:
        options["_filing_trace"] = run_dir / "diagnostics"
    data = assemble_dataset(source, result, analysis, **options)
    if output_dir is not None:
        save_dataset(data, output_dir)
    return data


def _json_file(path: Path, data: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def _csv_file(path: Path, columns, rows) -> None:
    stream = path.open("x", encoding="utf-8-sig", newline="")
    try:
        with stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    key: json.dumps(value, ensure_ascii=False, allow_nan=False)
                    if isinstance(value, (list, dict)) else value
                    for key, value in row.items()
                })
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def export_observations(dataset: dict, destination: str | Path, subject=None, **filters) -> Path:
    """Save a query_dataset selection as one UTF-8 BOM CSV row per observation, to a new file."""
    from .dataset import query_dataset
    from .dataset_annotations import TYPED_READING_COLUMNS

    columns = ("observation_id", "direct_subject", "direct_scope", "target_subject", "target_scope",
               "target_candidates", "reviewed_target", "field", "value", "unit", "statement_basis",
               "citation_status", "citation_reason", "measurement_use", "purpose", "conditions", "statistic", "delivery")
    if dataset.get("field_types") or any("field_type_status" in o for o in dataset["observations"]):
        columns += TYPED_READING_COLUMNS
    if filters.get("include_descendants") or filters.get("self_only") or (subject is not None
            and filters.get("include_descendants") is None and dataset.get("statement_protocol") == "source-v2"):
        columns += ("query_scope", "query_path")
    rows, seen = [], set()
    for obs in query_dataset(dataset, subject, **filters):
        oid = obs["observation_id"]
        if oid in seen:
            continue
        seen.add(oid)
        choices = obs.get("filing_subjects", obs.get("delivery_candidates", []))
        direct = obs["direct"]
        target = obs["target"] if "filing_subjects" not in obs else {}
        candidates = []
        if len(choices) == 1:
            target = choices[0]
        elif len(choices) > 1:
            target = {}
            for option in choices:
                if option.get("label"):
                    scope = option.get("scope") or option.get("id")
                    candidates.append(f'{option["label"]}（{scope}）' if scope else option["label"])
        row = {key: obs.get(key) for key in columns}
        row.update(direct_subject=direct.get("label") or obs.get("entity"), direct_scope=direct.get("scope"),
                   target_subject=target.get("label"), target_scope=target.get("scope"),
                   target_candidates="｜".join(candidates), reviewed_target=obs.get("reviewed_filing_subject", {}).get("label"),
                   statement_basis=obs.get("statement_basis", "unknown"))
        rows.append(row)
    target_path = Path(destination).absolute()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _csv_file(target_path, columns, rows)
    return target_path


def _main_rows(data, status_column):
    columns = data.get("context_columns", {})
    observations = {obs["observation_id"]: obs for obs in data["observations"]}
    for row, lineage in zip(data["rows"], data["lineage"]["rows"], strict=True):
        annotations = [lineage["status"]]
        label_field = data.get("subject_label_field")
        if ("cell_annotations" in lineage and label_field and lineage.get("subject_label")
                and row.get(label_field) != lineage["subject_label"]):
            annotations.append("归档主体 " + lineage["subject_label"])
        if not data.get("subject_label_field") and lineage.get("subject_label"):
            annotations.append("主体 " + lineage["subject_label"])
            if "subject_annotations" in lineage:
                annotations.extend(display_annotations(lineage["subject_annotations"], key, fallback)
                                   for key, fallback in (("statement_basis", "unknown"), ("citation_status", "unverified")))
        for oid in lineage.get("observations", []):
            observation = observations[oid]
            opinion = observation.get("jev_review", {})
            if opinion.get("state") == "field_opinion":
                verdict = {"supported": "支持按此字段使用", "mismatch": "认为不适用于此字段"}.get(
                    opinion.get("field_verdict"), "仍待核")
                annotations.append(f"Jev 对 {observation['field']} 的意见：{verdict}")
            for endpoint in observation.get("filing_subjects", []):
                if endpoint.get("explanation"):
                    annotations.append("归属说明 " + endpoint["explanation"])
        annotations.extend(lineage["context"])
        context = {}
        if columns:
            if "subject" in columns:
                context[columns["subject"]] = lineage.get("subject_label") or "归属待定"
            members = [observations[oid] for field, ids in lineage["cells"].items()
                       if field not in data["identity_fields"] for oid in ids]
            if not members and "cell_annotations" in lineage:
                members = [observations[oid] for oid in lineage["observations"]]
            names = [o.get("reviewed_direct_name") or (o["direct"].get("local_mention") or {}).get("quote") or o.get("entity")
                     or o["direct"].get("label") for o in members]
            context[columns["direct"]] = "｜".join(dict.fromkeys(n for n in names if n))
            qualifiers = []
            for obs in members:
                statistic = obs.get("statistic")
                if not statistic and _range_match(str(obs["value"]), str(obs.get("unit") or "")):
                    statistic = "范围"
                reading = "、".join(dict.fromkeys(str(c) for c in (statistic, obs.get("conditions"))
                                                  if c))
                use = _USE_LABELS.get(obs.get("measurement_use")) or _USE_LABELS.get(obs.get("purpose"))
                if not use and obs.get("measurement_use") not in (None, "unspecified"):
                    use = obs["measurement_use"]
                if use and use not in reading:
                    reading = "、".join(filter(None, (reading, use)))
                if reading:
                    qualifiers.append((obs["field"] + " " if len(members) > 1 else "") + reading)
            context[columns["statistic"]] = "｜".join(dict.fromkeys(qualifiers))
        statistics = lineage.get("statistics", {})
        for field, receipts in lineage.get("cell_annotations", {}).items():
            labels = data["annotation_columns"][field]
            context[labels["statement_basis"]] = display_annotations(receipts, "statement_basis", "unknown")
            context[labels["citation_status"]] = display_annotations(receipts, "citation_status", "unverified")
            if field in data["schema"]:
                for receipt in receipts:
                    reason = receipt.get("citation_reason")
                    if reason and receipt["citation_status"] == "unverified":
                        annotations.append(f"{field} 引文未核实：{reason}")
                    elif reason and reason.startswith("引文已找回"):
                        annotations.append(f"{field} {reason}")
                    if receipt.get("reviewed_filing_subject"):
                        annotations.append(f'{field} Jev 建议归属 {receipt["reviewed_filing_subject"]["label"]}')
                    elif receipt.get("review_opinion") == "disagreed":
                        annotations.append(f"{field} Jev 对原判断持异议")
        yield {**row, **{k: c["value"] for k, c in statistics.items()}, **context,
               status_column: "｜".join(dict.fromkeys(annotations))}


def save_dataset(data: dict, directory: str | Path) -> Path:
    """Publish both CSV views and JSON provenance together to a fresh directory."""
    target = Path(directory).absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Dataset destination already exists: {target}. Choose a new directory.")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / (".niceextract-dataset-" + uuid4().hex)
    # Inherit the destination directory's ACL, including under managed Windows profiles.
    stage.mkdir()
    try:
        _json_file(stage / "dataset.json", data)
        _json_file(stage / "lineage.json", data["lineage"])
        status_column = data.get("status_column")
        if status_column:
            statistics = [col for mapping in data.get("statistic_columns", {}).values() for col in mapping.values()]
            columns = []
            for field in [*data["schema"], *statistics]:
                columns.append(field)
                columns.extend(data.get("annotation_columns", {}).get(field, {}).values())
            columns.extend([*data.get("context_columns", {}).values(), status_column])
            _csv_file(stage / "main.csv", columns, _main_rows(data, status_column))
        else:
            _csv_file(stage / "main.csv", data["schema"], data["rows"])
        # Keep the first columns immediately usable, including when the export is empty.
        columns = ["observation_id", "entity", "direct_subject", "direct_subject_status", "target_subject",
                   "field", "value", "unit", "conditions", "statistic", "purpose", "delivery", "source_record"]
        details = []
        for observation in data["observations"]:
            detail = dict(observation)
            detail["direct_subject"] = (observation.get("direct") or {}).get("label") or observation.get("entity")
            detail["target_subject"] = (observation.get("target") or {}).get("label")
            details.append(detail)
            for key in detail:
                if key not in columns:
                    columns.append(key)
        _csv_file(stage / "observations.csv", columns, details)
        stage.rename(target)
    except BaseException:
        for name in ("dataset.json", "lineage.json", "main.csv", "observations.csv"):
            (stage / name).unlink(missing_ok=True)
        stage.rmdir()
        raise
    return target
