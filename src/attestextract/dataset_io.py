"""Local I/O for immutable, source-linked dataset snapshots."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from uuid import uuid4

from .errors import ConfigurationError
from .storage import _reject_constant, _unique_object


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
                        result_path=None, analysis_path=None, raw_only: bool = False,
                        audit_admission: bool = False, **options) -> dict:
    """Rebuild a dataset from one source and its frozen run without loading credentials."""
    from .dataset import assemble_dataset

    run_dir = Path(run_dir)
    # read_text() performs universal newline translation; evidence offsets must survive CRLF.
    source = Path(source_path).read_bytes().decode("utf-8")
    result = _read_json_object(Path(result_path) if result_path is not None else run_dir / "result.json")
    if type(raw_only) is not bool or raw_only and analysis_path is not None:
        raise ValueError("raw_only must be a boolean and cannot select an analysis_path when enabled.")
    analysis = None
    if result.get("entity_first") is not None and (analysis_path is not None or raw_only):
        raise ValueError("A frozen entity-first run contains its own bindings; omit analysis_path and raw_only.")
    if audit_admission and result.get("entity_first") is None:
        raise ValueError("Admission auditing requires an entity-first run.")
    if result.get("entity_first") is not None:
        from .entity_first_output import project_archive
        data = project_archive(source, result, options, audit_admission=audit_admission, run_dir=run_dir)
    else:
        if not raw_only:
            analysis = _read_json_object(Path(analysis_path) if analysis_path is not None
                                         else run_dir / "analysis" / "analysis.json")
        data = assemble_dataset(source, result, analysis, **options)
    if output_dir is not None:
        save_dataset(data, output_dir)
    return data


def _json_file(path: Path, data: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def _csv_file(path: Path, columns, rows) -> None:
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False, allow_nan=False)
                if isinstance(value, (list, dict)) else value
                for key, value in row.items()
            })


def save_dataset(data: dict, directory: str | Path) -> Path:
    """Publish both CSV views and JSON provenance together to a fresh directory."""
    target = Path(directory).absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Dataset destination already exists: {target}. Choose a new directory.")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / (".attestextract-dataset-" + uuid4().hex)
    # Inherit the destination directory's ACL, including under managed Windows profiles.
    stage.mkdir()
    try:
        _json_file(stage / "dataset.json", data)
        _json_file(stage / "lineage.json", data["lineage"])
        _csv_file(stage / "main.csv", data["schema"], data["rows"])
        if data.get("protocol_version") == "entity-first-v3":
            counts = Counter(r["subject_id"] for r in data["lineage"]["rows"])
            columns = ["object_id", "label", "qualifier", "aliases", "kind", "stage", "main_rows", "evidence"]
            roster = [{**{k: obj.get(k) for k in columns}, "main_rows": counts[obj["object_id"]]}
                      for obj in data["lineage"]["objects"]]
            _csv_file(stage / "roster.csv", columns, roster)
        # Keep the first columns immediately usable, including when the export is empty.
        columns = ["observation_id", "entity", "direct_subject", "target_subject",
                   "field", "value", "unit", "conditions", "purpose", "source_record"]
        details = []
        for observation in data["observations"]:
            detail = dict(observation)
            for key in ("direct", "target"):
                detail[key + "_subject"] = (observation.get(key) or {}).get("label")
            details.append(detail)
            for key in detail:
                if key not in columns:
                    columns.append(key)
        _csv_file(stage / "observations.csv", columns, details)
        if "proposals" in data:
            from .annotation_view import admission_annotation, render_annotation
            proposals = data["proposals"]
            columns = ["direct_subject", "source_property", "value", "unit", "conditions", "statistic",
                       "candidate_subject", "suggested_field", "disposition", "reason", "source_kind", "block_id"]
            if proposals:
                columns.extend(k for k in proposals[0] if k not in columns)
            _csv_file(stage / "proposals.csv", columns, proposals)
            annotation, sources = admission_annotation(data)
            _json_file(stage / "annotation.json", annotation)
            _json_file(stage / "annotation.sources.json", {"source_result": "dataset.json", "entries": sources})
            (stage / "annotation.md").write_text(render_annotation(annotation), encoding="utf-8")
        stage.rename(target)
    except BaseException:
        for name in ("dataset.json", "lineage.json", "main.csv", "observations.csv", "proposals.csv",
                     "roster.csv", "annotation.json", "annotation.md", "annotation.sources.json"):
            (stage / name).unlink(missing_ok=True)
        stage.rmdir()
        raise
    return target
