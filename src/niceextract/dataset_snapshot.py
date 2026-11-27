"""Join frozen candidates to their verification snapshot using original Unicode offsets."""

from copy import deepcopy
from hashlib import sha256

from .analysis import _input_observations, _prepare_extraction_assignments
from .analysis_export import _validate
from .models import ExtractionResult
from .value_location import locate_value


def reconcile_presentation_checks(source, item):
    """Discharge only location warnings explained by a unique Markdown presentation escape."""
    if not item.get("blockers"):
        return item
    resolved_fields = set()
    for ref in item["source_evidence"]:
        matches = ref.get("source_matches", [])
        if len(matches) != 1 or ref.get("location_status") != "normalized":
            continue
        span = ref.get("span")
        if not span or matches[0].get("span") != span:
            continue
        start, end = span["start"], span["end"]
        original = source[start:end]
        if (original != matches[0].get("source_quote") or original == ref["quote"] or
                original.replace(r"\~", "~").replace(r"\%", "%") != ref["quote"]):
            continue
        for field in ref["fields"]:
            # Two citations for the same field may represent different assignments.
            if sum(field in other["fields"] for other in item["source_evidence"]) != 1:
                continue
            if field == "value":
                if locate_value(source, item.get("value"), start, end):
                    resolved_fields.add(field)
            elif field in {"entity", "unit", "conditions"}:
                literal = item.get(field)
                if isinstance(literal, str) and literal.strip() and literal in original:
                    resolved_fields.add(field)
    pending, resolved = [], []
    for issue in item["blockers"]:
        if issue.get("field") in resolved_fields and issue.get("code") in {"evidence_normalized", "evidence_matched"}:
            resolved.append(issue)
        else:
            pending.append(issue)
    if not resolved:
        return item
    return {**item, "blockers": pending, "resolved_location_checks": resolved,
            "archived_blockers": item["blockers"]}


def checked_snapshot(source: str, result: dict, analysis: dict | None, *, label_field=None) -> tuple[dict, list]:
    parsed = ExtractionResult.model_validate(result)
    if parsed.task.input_materials is None:
        raise ValueError("Dataset assembly requires the single-observation field_list task.")
    if (sha256(source.encode("utf-8")).hexdigest() != parsed.source.sha256
            or len(source) != parsed.source.character_count):
        raise ValueError("Original source checksum or character count differs from result.json.")
    fields = {name: spec.description for name, spec in parsed.task.record_types.items()}
    task = {"name": parsed.task.name, "description": parsed.task.description, "fields": fields}
    metadata = parsed.source.model_dump(mode="json")
    entries = {}
    originals, rejected = _input_observations(source, parsed, entries)
    by_id = {item["observation_id"]: item for item in originals}
    if analysis is None:
        if parsed.task.row_subject or parsed.task.statement_protocol == "source-v2":
            _prepare_extraction_assignments(originals, label_field, True, statement_protocol=parsed.task.statement_protocol,
                                            has_row_subject=bool(parsed.task.row_subject), source=source)
        return {"source": metadata, "task": task, "table_subject": parsed.task.row_subject, "objects": [],
                "relations": [], "observations": originals, "evidence": list(entries.values()),
                "quarantined": rejected, "status": "not_run", "budget": {}}, []

    if not isinstance(analysis, dict) or not isinstance(analysis.get("source"), dict):
        raise ValueError("Analysis source metadata is missing or invalid.")
    _validate(analysis)
    if (any(analysis["source"].get(key) != metadata.get(key)
            for key in ("sha256", "character_count", "document_id")) or analysis["task"] != task):
        raise ValueError("Analysis source or task differs from result.json.")
    if analysis.get("source_format_status", parsed.format_status) != parsed.format_status:
        raise ValueError("Analysis and result.json have different source format status.")
    for item in analysis["evidence"]:
        start, end = item.get("start"), item.get("end")
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source)
                or source[start:end] != item["quote"]):
            raise ValueError(f"Analysis evidence {item['evidence_id']} differs from original source.")
        if item["evidence_id"] in entries and entries[item["evidence_id"]] != item:
            raise ValueError("Source evidence ID was reused for a different span.")
    for context in analysis.get("evidence_contexts", []):
        if source[context["start"]:context["end"]] != context["text"]:
            raise ValueError("Analysis evidence context differs from original source.")
    observed = {}
    for item in analysis["observations"]:
        oid = item["observation_id"]
        raw = by_id.get(oid)
        if raw is None:
            raise ValueError(f"Analysis observation {oid} has no matching result candidate.")
        for field in ("candidate_id", "source_record", "record_type", "source_kind", "entity", "value",
                      "unit", "conditions", "source_evidence", "evidence_ids", "name_handling", "row_relations",
                      "statement_basis", "reported_use", "source_property", "source_property_evidence_ids",
                      "value_role", "row_relation_issues"):
            if raw.get(field) != item.get(field):
                raise ValueError(f"Frozen candidate {raw['candidate_id']} differs in {field}.")
        if any(issue not in item["blockers"] for issue in raw["blockers"]):
            raise ValueError("Analysis omitted an original extraction blocker.")
        observed[oid] = item
    gaps = [{"kind": "missing_analysis_observation", "observation_id": key,
             "source_record": raw["source_record"]} for key, raw in by_id.items() if key not in observed]
    snapshot = dict(analysis)
    snapshot["observations"] = [deepcopy(observed.get(key, raw)) for key, raw in by_id.items()]
    snapshot["evidence"] = list({**entries, **{entry["evidence_id"]: entry
                                               for entry in analysis["evidence"]}}.values())
    snapshot["quarantined"] = rejected
    return snapshot, gaps
