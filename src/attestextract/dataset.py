"""Assemble schema-preserving research tables from frozen single-subject observations."""

from collections import defaultdict
from copy import deepcopy
import re

from .dataset_snapshot import checked_snapshot
from .evidence import is_missing
from .execution import digest
from .measurement_syntax import numeric_tokens


_POLICY_COLUMNS = {"allowed_units": "unit", "allowed_purposes": "purpose",
                   "allowed_statistics": "statistic", "required_conditions": "conditions"}


def validate_dataset_options(schema, *, identity_fields=(), row_subject=None, field_policies=None,
                             subject_label_field=None):
    if (not isinstance(identity_fields, (list, tuple))
            or any(not isinstance(key, str) or key not in schema for key in identity_fields)
            or len(set(identity_fields)) != len(identity_fields)):
        raise ValueError("identity_fields must be distinct field names from the full schema.")
    if subject_label_field is not None and (
        not isinstance(subject_label_field, str) or subject_label_field not in schema
        or identity_fields and subject_label_field not in identity_fields
    ):
        raise ValueError("subject_label_field must name a schema field within the configured identity.")
    if row_subject is not None and (not isinstance(row_subject, str) or not row_subject.strip()):
        raise ValueError("row_subject must be a nonempty description.")
    if field_policies is None:
        return
    if not isinstance(field_policies, dict):
        raise ValueError("field_policies must be a mapping keyed by schema field.")
    for field, policy in field_policies.items():
        if field not in schema or not isinstance(policy, dict) or not set(policy) <= _POLICY_COLUMNS.keys():
            raise ValueError(f"Unknown dataset field or policy for {field!r}.")
        for key, values in policy.items():
            if (not isinstance(values, list) or not values
                    or any(not isinstance(v, str) or not v.strip() for v in values)):
                raise ValueError(f"{key} requires a nonempty list of exact allowed strings.")


def _has_value_source(item, source):
    """A pointer to the owner alone cannot ground a numeric measurement."""
    if is_missing(item["value"]):
        return False
    literal = str(item["value"]).strip()
    numeric = numeric_tokens(literal) == [(0, len(literal))]
    for evidence in item["source_evidence"]:
        if ("value" not in evidence["fields"] or len(evidence.get("matches", [])) > 1
                or len(evidence.get("source_matches", [])) > 1):
            continue
        span = evidence.get("span")
        if not span:
            continue
        text = source[span["start"]:span["end"]]
        for match in re.finditer(re.escape(literal), text):
            left, right = span["start"] + match.start(), span["start"] + match.end()
            if numeric:
                before, after = source[max(0, left - 2):left], source[right:right + 4]
                if (re.search(r"[A-Za-z_\d.+−-]$|\d,$", before)
                        or re.match(r"\d|[.,]\d|[eE][+−-]?\d", after)):
                    continue
            return True
    return False


def _observation(item, resolution, evidence, source, policies):
    detail = {**deepcopy(item), **deepcopy(resolution), "field": item["record_type"],
              "evidence": [evidence[key] for key in dict.fromkeys(item["evidence_ids"]
                                                                  + item["binding_evidence_ids"])]}
    detail.setdefault("statistic", item.get("statistic"))
    detail.setdefault("related", [])
    detail["original_checks"] = {key: deepcopy(item.get(key)) for key in
                                 ("subject_id", "binding_status", "semantic_status", "reason",
                                  "target_match", "property_name", "statistic", "blockers")}
    reasons = []
    if not _has_value_source(item, source):
        reasons.append("missing_value" if is_missing(item["value"]) else "value_without_unique_source")
    if item["blockers"]:
        reasons.append("original_check_blocked")
    for policy, allowed in policies.get(detail["field"], {}).items():
        if detail.get(_POLICY_COLUMNS[policy]) not in allowed:
            reasons.append(policy)
    detail["fit_blockers"] = reasons
    if reasons:
        prior = detail["fit"]
        detail["fit"] = {**prior, "status": "unknown", "reason": ", ".join(reasons),
                         "prior_decision": prior}
    detail["eligible"] = all(detail[key]["status"] == "supported" for key in ("direct", "target", "fit"))
    detail["row_ids"] = []
    return detail


def _identity_values(observations, identity_fields):
    """Keep all alternatives, so composite identities with conflicts cannot leak across rows."""
    by_subject = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for obs in observations:
        if (obs["eligible"] and obs["field"] in identity_fields
                and obs["direct"]["id"] == obs["target"]["id"]):
            key = (obs["target"]["id"], obs["conditions"], obs.get("statistic"))
            by_subject[key][obs["field"]][obs["value"]].append(obs["observation_id"])
    return by_subject


def _attested_label(target):
    label = target.get("label")
    return (isinstance(label, str) and bool(label.strip())
            and any(label in span["quote"] for span in target["evidence"]))


def _project(schema, observations, identity_fields, source_hash, policies, label_field):
    by_subject = _identity_values(observations, identity_fields)
    groups = defaultdict(list)
    for obs in observations:
        if obs["eligible"] and obs["direct"]["id"] == obs["target"]["id"]:
            record = obs.get("record_group") or obs["observation_id"]
            key = (obs["target"]["id"], digest(record), obs["conditions"], obs.get("statistic"))
            groups[key].append(obs)
    rows, lineage = [], []
    for items in groups.values():
        target = items[0]["target"]
        field_names = [obs["field"] for obs in items]
        # Repeated measures in a block have no documented pairing by list position.
        partitions = [items] if len(set(field_names)) == len(items) else [[item] for item in items]
        for members in partitions:
            row = dict.fromkeys(schema)
            cells = {}
            for obs in members:
                row[obs["field"]] = obs["value"]
                cells[obs["field"]] = [obs["observation_id"]]
            for field in identity_fields:
                if row[field] is not None:
                    continue
                identity_key = (target["id"], members[0]["conditions"], members[0].get("statistic"))
                possibilities = by_subject[identity_key][field]
                if len(possibilities) == 1:
                    value, ids = next(iter(possibilities.items()))
                    row[field], cells[field] = value, list(ids)
                elif (not possibilities and field == label_field and not policies.get(field)
                      and _attested_label(target)):
                    row[field] = target["label"]
                    cells[field] = [obs["observation_id"] for obs in members]
            if any(is_missing(row[field]) for field in identity_fields):
                for obs in members:
                    obs["projection_reason"] = "identity_missing_or_conflicting"
                continue
            member_ids = [obs["observation_id"] for obs in members]
            row_id = "row-" + digest([source_hash, target["id"], member_ids])[:24]
            rows.append(row)
            lineage.append({"row_id": row_id, "subject_id": target["id"], "subject_label": target["label"],
                            "scope": target.get("scope"), "cells": cells, "observations": member_ids})
            for obs in members:
                obs["row_ids"].append(row_id)
    return rows, lineage


def assemble_dataset(source: str, result: dict, analysis: dict | None = None, *,
                     identity_fields=(), row_subject=None, field_policies=None, subject_label_field=None) -> dict:
    """Build a new offline view. Original extraction and adjudication snapshots stay intact."""
    from .dataset_resolution import resolve_observations
    from .dataset_supplement import discover_gaps

    if result.get("entity_first") is not None:
        if analysis is not None:
            raise ValueError("Entity-first already records its own bindings; omit the legacy analysis snapshot.")
        from .entity_first_output import project_archive
        options = {key: val for key, val in {"identity_fields": identity_fields, "subject_label_field": subject_label_field,
                                              "row_subject": row_subject, "field_policies": field_policies}.items() if val}
        return project_archive(source, result, options)
    snapshot, gaps = checked_snapshot(source, result, analysis)
    fields = snapshot["task"]["fields"]
    validate_dataset_options(fields, identity_fields=identity_fields, row_subject=row_subject,
                             field_policies=field_policies, subject_label_field=subject_label_field)
    if subject_label_field is not None and not identity_fields:
        identity_fields = [subject_label_field]
    subject = row_subject or snapshot.get("table_subject")
    # A column containing a time, serial number or site code is not the subject's display name.
    resolutions = resolve_observations(source, result, snapshot, identity_fields=[subject_label_field]
                                       if subject_label_field else [],
                                       row_subject=subject or "")
    evidence = {item["evidence_id"]: item for item in snapshot["evidence"]}
    details = [_observation(item, resolutions[item["observation_id"]], evidence, source, field_policies or {})
               for item in snapshot["observations"]]
    source_hash = snapshot["source"]["sha256"]
    rows, lineage_rows = _project(fields, details, identity_fields, source_hash, field_policies or {},
                                  subject_label_field)
    return {"dataset_version": "1", "document_id": snapshot["source"].get("document_id") or source_hash,
            "source_sha256": source_hash, "schema": list(fields), "field_definitions": fields,
            "row_subject": subject, "identity_fields": list(identity_fields), "field_policies": field_policies or {},
            "subject_label_field": subject_label_field,
            "row_identity_location": "schema_columns" if identity_fields else "lineage_rows_in_csv_order",
            "rows": rows, "observations": details, "coverage_gaps": gaps + discover_gaps(source, result, snapshot),
            "lineage": {"rows": lineage_rows, "evidence": list(evidence.values()), "objects": snapshot["objects"],
                        "relations": snapshot["relations"], "quarantined": snapshot["quarantined"]},
            "budget": {"assembly_calls": 0, "prior_analysis": snapshot.get("budget", {}),
                       "extraction_http_attempts": result["run"]["request_attempts"]}}


def query_dataset(dataset, subject=None, *, exploratory=False, field=None, purpose=None, association_only=False):
    """Query schema-qualified facts, supported associations, or exploratory candidates by source ID."""
    if association_only and subject is None:
        raise ValueError("An association-only query needs a subject label or ID.")
    found = []
    for obs in dataset["observations"]:
        if field is not None and obs["field"] != field or purpose is not None and obs["purpose"] != purpose:
            continue
        if not exploratory and not association_only and not obs["row_ids"]:
            continue
        endpoints = [obs["direct"]]
        if exploratory or association_only:
            endpoints.extend([obs["target"], *obs.get("related", [])])
        labels = {v for endpoint in endpoints if endpoint.get("status") == "supported"
                  for v in (endpoint.get("id"), endpoint.get("label")) if isinstance(v, str)}
        if obs["direct"].get("basis") == "explicit_definition" and isinstance(obs["entity"], str):
            labels.add(obs["entity"])
        if exploratory:
            for candidate in obs.get("candidates", []):
                labels.update(v for v in (candidate.get("id"), candidate.get("label")) if isinstance(v, str))
            labels.update(obs.get("members", []))
            if isinstance(obs["entity"], str):
                labels.add(obs["entity"])
        if subject is None or subject in labels:
            found.append(obs)
    return found
