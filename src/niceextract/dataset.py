"""Assemble schema-preserving research tables from frozen single-subject observations."""

from collections import defaultdict
from copy import deepcopy

from .dataset_admission import apply_field_verdict, delivered_context, delivery_columns, provisional_row
from .dataset_extraction_owner import ExtractionOwners
from .dataset_field_fit import POLICY_COLUMNS, VALUE_FORMS, apply_value_type, check_competing_cell_fields, field_policy_blockers
from .dataset_filing import FilingContext, proposed_row
from .dataset_prose_rows import prose_record_keys
from .dataset_row_relation import apply_row_relation
from .dataset_resolution_table import reconcile_table_unit
from .dataset_snapshot import checked_snapshot, reconcile_presentation_checks
from .dataset_statistics import group_statistic_rows, set_statistic_columns
from .evidence import is_missing
from .execution import digest
from .value_location import locate_value


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
        if field not in schema or not isinstance(policy, dict) or not set(policy) <= POLICY_COLUMNS.keys() | {"allowed_value_forms"}:
            raise ValueError(f"Unknown dataset field or policy for {field!r}.")
        for key, values in policy.items():
            if (not isinstance(values, list) or not values
                    or any(not isinstance(v, str) or not v.strip() for v in values)):
                raise ValueError(f"{key} requires a nonempty list of exact allowed strings.")
            if key == "allowed_value_forms" and not set(values) <= VALUE_FORMS:
                raise ValueError("allowed_value_forms accepts description, number and quantity.")


def _has_value_source(item, source):
    """A pointer to the owner alone cannot ground a numeric measurement."""
    if is_missing(item["value"]):
        return False
    for evidence in item["source_evidence"]:
        if ("value" not in evidence["fields"] or len(evidence.get("matches", [])) > 1
                or len(evidence.get("source_matches", [])) > 1):
            continue
        span = evidence.get("span")
        if span and locate_value(source, item["value"], span["start"], span["end"]):
            return True
    return False


def _observation(item, resolution, evidence, source, policies, owners, definition):
    item = reconcile_table_unit(item, resolution.get("table_context"))
    detail = {**deepcopy(item), **deepcopy(resolution), "field": item["record_type"],
              "evidence": [evidence[key] for key in dict.fromkeys(item["evidence_ids"]
                                                                  + item["binding_evidence_ids"])]}
    detail.setdefault("statistic", item.get("statistic"))
    detail.setdefault("related", [])
    owners.apply(detail, definition)
    apply_row_relation(owners, detail)
    owners.apply_identity(detail)
    lookup = detail.pop("partition_lookup", None)
    direct = detail["direct"]
    if (lookup and direct["status"] == "supported" and direct["label"] == lookup["mention"]
            and not direct.get("name_status") and not detail.get("name_resolution")):
        direct["alias_names"] = list(dict.fromkeys([*direct.get("alias_names", []), lookup["label"]]))
        direct["partition_evidence"] = lookup["evidence"]
    elif lookup and direct["status"] != "supported" and not detail.get("name_resolution"):
        detail["candidates"].append({"status": "unknown", "id": None, "label": lookup["label"],
                                     "scope": None, "reason": "partition_member", "basis": "source_partition",
                                     "evidence": lookup["evidence"]})
    detail["original_checks"] = {key: deepcopy(item.get(key)) for key in
                                 ("subject_id", "binding_status", "semantic_status", "reason",
                                  "target_match", "property_name", "statistic", "blockers")}
    if "archived_blockers" in detail:
        detail["original_checks"]["blockers"] = detail.pop("archived_blockers")
    reasons = []
    detail["value_located"] = _has_value_source(item, source)
    if not detail["value_located"]:
        reasons.append("missing_value" if is_missing(item["value"]) else "value_without_unique_source")
    qualifier_review = (detail["fit"]["reason"] == "field_qualifier_unverified"
                        and all(b.get("code") == "target_field_unknown" for b in item["blockers"]))
    if item["blockers"] and not qualifier_review and not detail.get("binding_owner_blockers_resolved"):
        reasons.append("original_check_blocked")
    reasons.extend(field_policy_blockers(detail, policies.get(detail["field"], {})))
    detail["fit_blockers"] = reasons
    if reasons and detail["fit"]["status"] != "contradicted":
        prior = detail["fit"]
        detail["fit"] = {**prior, "status": "unknown", "reason": ", ".join(reasons),
                         "prior_decision": prior}
    detail["eligible"] = not detail.get("corroborates") and all(
        detail[key]["status"] == "supported" for key in ("direct", "target", "fit"))
    if direct["status"] == "supported":
        detail["direct_subject_status"] = "source_linked"
    elif item.get("entity"):
        detail["direct_subject_status"] = "model_proposed"
    else:
        detail["direct_subject_status"] = "unreported"
    detail["delivery"] = "detail"
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
            and (any(label in span["quote"] for span in target["evidence"])
                 or target.get("reason") == "extracted_row_subject" and bool(target["evidence"])))


def _project(schema, observations, identity_fields, source_hash, policies, label_field, objects,
             prose_records=None):
    """Project confirmed facts together; keep a provisional assertion at its own grain."""
    by_subject = _identity_values(observations, identity_fields)
    by_id = {obs["observation_id"]: obs for obs in observations}
    has_attributes = bool(set(schema).difference(identity_fields))
    groups = defaultdict(list)
    for obs in observations:
        if obs["eligible"] and (obs["direct"]["id"] == obs["target"]["id"] or
                                (obs.get("associated_row") and obs["field"] not in identity_fields)):
            record = obs.get("record_group") or obs["observation_id"]
            key = (obs["target"]["id"], obs["direct"]["id"], digest(record),
                   obs["conditions"], obs.get("statistic"), obs.get("purpose"))
            if (prose_records and len(identity_fields) == 1 and label_field in identity_fields
                    and obs["field"] != label_field and obs["observation_id"] in prose_records):
                key = prose_records[obs["observation_id"]]
            groups[key].append(obs)
    rows, lineage = [], []
    for items in groups.values():
        target = items[0]["target"]
        field_names = [obs["field"] for obs in items]
        # Repeated measures in a block have no documented pairing by list position.
        partitions = [items] if len(set(field_names)) == len(items) else [[item] for item in items]
        for members in partitions:
            if has_attributes and all(obs["field"] in identity_fields for obs in members):
                continue
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
            member_ids = list(dict.fromkeys(key for keys in cells.values() for key in keys))
            row_id = "row-" + digest([source_hash, target["id"], member_ids])[:24]
            rows.append(row)
            lineage.append({"row_id": row_id, "subject_id": target["id"], "subject_label": target["label"],
                            "scope": target.get("scope"), "cells": cells, "observations": member_ids,
                            "delivery": "confirmed",
                            "status": "已确认，按关联主体归档，数值属于直接对象" if members[0].get("associated_row") else "已确认",
                            "context": delivered_context(members)})
            for key in member_ids:
                obs = by_id[key]
                obs["delivery"] = "confirmed"
                obs["row_ids"].append(row_id)
    for obs in observations:
        if obs["row_ids"]:
            continue
        key = (obs["target"]["id"], obs["conditions"], obs.get("statistic"))
        if "filing_subjects" in obs:
            provisional = proposed_row(obs, schema, label_field)
        else:
            provisional = provisional_row(obs, schema, identity_fields, label_field, objects, policies,
                                          by_subject.get(key, {}))
        if provisional is None:
            continue
        row, proof, reasons = provisional
        oid = obs["observation_id"]
        row_id = "row-" + digest([source_hash, "provisional", oid])[:24]
        rows.append(row)
        lineage.append({**proof, "row_id": row_id})
        for key in proof["observations"]:
            if key != oid:
                by_id[key]["delivery"] = "confirmed"
                by_id[key]["row_ids"].append(row_id)
        obs.update(delivery="provisional", delivery_reasons=reasons, delivery_candidates=proof["subject_candidates"])
        obs["row_ids"].append(row_id)
    return rows, lineage


def assemble_dataset(source: str, result: dict, analysis: dict | None = None, *,
                     identity_fields=(), row_subject=None, field_policies=None, subject_label_field=None,
                     reader_view=None, reading_review=None, _filing_trace=None) -> dict:
    """Build a new offline view. Original extraction and adjudication snapshots stay intact."""
    from .dataset_resolution import resolve_observations
    from .dataset_supplement import discover_gaps

    snapshot, gaps = checked_snapshot(source, result, analysis, label_field=subject_label_field)
    snapshot = {**snapshot, "observations": [reconcile_presentation_checks(source, item)
                                              for item in snapshot["observations"]]}
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
    owners = ExtractionOwners(source, snapshot, subject == snapshot.get("table_subject"), subject_label_field)
    details = [_observation(item, resolutions[item["observation_id"]], evidence, source, field_policies or {},
                            owners, fields[item["record_type"]])
               for item in snapshot["observations"]]
    relation_protocol = result["task"].get("row_relation_protocol")
    archived = {}
    if _filing_trace is not None and reader_view is not False:
        from .filing_replay import replay_filing_issues
        archived = replay_filing_issues(source, result, _filing_trace)
    for obs in details:
        replay = archived.get(obs["source_record"])
        if replay and obs.get("row_relation_issues"):
            obs["archived_row_relation_issues"] = obs.pop("row_relation_issues")
            obs["filing_replay_attempt"] = replay["attempt"]
            obs["filing_replay_record_index"] = replay["record_index"]
            obs["row_relations"] = replay["receipts"]
        receipts = obs.get("row_relations", [])
        if receipts:
            obs["row_relation_status"] = "located" if all(r["status"] == "located" for r in receipts) else "unresolved"
        else:
            obs["row_relation_status"] = "not_proposed" if relation_protocol else "not_asked"
    check_competing_cell_fields(source, details, fields, identity_fields)
    if reader_view is None:
        reader_view = bool(result["task"].get("statement_protocol") or reading_review is not None)
    if type(reader_view) is not bool or reading_review is not None and not reader_view:
        raise ValueError("reader_view must be boolean; applying a reading review requires the annotated view.")
    if reader_view:
        filing = FilingContext(source, snapshot, owners.ends, result.get("table_results", ()),
                               statement_protocol=result["task"].get("statement_protocol"))
        for obs in details:
            filing.apply(obs, subject == snapshot.get("table_subject"), field_policies or {})
        if reading_review is not None:
            from .dataset_reading_review import apply_reading_review
            apply_reading_review(source, details, reading_review, fields, field_policies or {})
        if result["task"].get("statement_protocol") in {"source-v1", "source-v2"}:
            for obs in details:
                apply_field_verdict(obs, evidence)
    types = result["task"].get("field_types") or {}
    for obs in details:
        forms = (field_policies or {}).get(obs["field"], {}).get("allowed_value_forms")
        apply_value_type(source, obs, types.get(obs["field"]), forms)
    if reader_view and result["task"].get("statement_protocol") == "source-v2":
        from .dataset_repeat import collect_repeats
        collect_repeats(details, evidence)
    source_hash = snapshot["source"]["sha256"]
    objects = {obj["object_id"]: obj for obj in snapshot["objects"]} if subject == snapshot.get("table_subject") else {}
    rows, lineage_rows = _project(fields, details, identity_fields, source_hash, field_policies or {},
                                  subject_label_field, objects, prose_record_keys(source, owners.ends, details))
    columns = delivery_columns(fields, filing_subject=not subject_label_field and result["task"].get("statement_protocol") == "source-v2")
    group_statistic_rows(rows, lineage_rows, details, identity_fields, source)
    data = {"dataset_version": "2", "document_id": snapshot["source"].get("document_id") or source_hash,
            "status_column": columns["status"], "context_columns": {k: value for k, value in columns.items() if k != "status"},
            "source_sha256": source_hash, "schema": list(fields), "field_definitions": fields,
            **({"field_types": types} if types else {}),
            "row_relation_protocol": relation_protocol,
            **({"statement_protocol": "source-v2"} if result["task"].get("statement_protocol") == "source-v2" else {}),
            "row_subject": subject, "identity_fields": list(identity_fields), "field_policies": field_policies or {},
            "subject_label_field": subject_label_field,
            "row_identity_location": "schema_columns" if identity_fields else "lineage_rows_in_csv_order",
            "rows": rows, "observations": details, "coverage_gaps": gaps + discover_gaps(source, result, snapshot),
            "lineage": {"rows": lineage_rows, "evidence": list(evidence.values()), "objects": snapshot["objects"],
                        "relations": snapshot["relations"], "quarantined": snapshot["quarantined"]},
            "budget": {"assembly_calls": 0, "prior_analysis": snapshot.get("budget", {}),
                       "extraction_http_attempts": result["run"]["request_attempts"]}}
    set_statistic_columns(data)
    if reader_view:
        from .dataset_annotations import finalize_annotations
        finalize_annotations(data)
    return data


def _confirmed_corroborations(observations):
    """Identify secondary citations already published with a primary observation."""
    return {(other, obs["observation_id"]) for obs in observations
            if obs.get("row_ids")
            for other in obs.get("corroborating_observations", [])}


def query_dataset(dataset, subject=None, *, exploratory=False, field=None, purpose=None,
                  association_only=False, statistic_kind=None, measurement_use=None,
                  statement_basis=None, citation_status=None, include_pending=None, subject_match="exact",
                  include_descendants=None, self_only=False):
    """Browse published observations once by either endpoint, with optional provenance filters."""
    if association_only and subject is None:
        raise ValueError("An association-only query needs a subject label or ID.")
    if subject_match not in ("exact", "contains") or subject_match == "contains" and not (
            isinstance(subject, str) and subject.strip()):
        raise ValueError("subject_match must be exact, or contains with a nonempty subject.")
    if include_pending is None:
        include_pending = "annotation_columns" in dataset
    if include_descendants is None:
        include_descendants = bool(subject is not None and dataset.get("statement_protocol") == "source-v2")
    hierarchy = None
    if include_descendants or self_only:
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("Browsing descendants requires a subject name or ID.")
        from .dataset_hierarchy import SubjectHierarchy
        hierarchy = SubjectHierarchy(dataset, subject, subject_match)
    found = []
    for obs in dataset["observations"]:
        if obs.get("corroborates"):
            continue
        if field is not None and obs["field"] != field or purpose is not None and obs["purpose"] != purpose:
            continue
        if statistic_kind is not None and obs.get("statistic_kind") != statistic_kind:
            continue
        if measurement_use is not None and obs.get("measurement_use") != measurement_use:
            continue
        if statement_basis is not None and obs.get("statement_basis", "unknown") != statement_basis:
            continue
        if citation_status is not None and obs.get("citation_status") != citation_status:
            continue
        if (not exploratory and not association_only
                and (not obs["row_ids"] or not include_pending and obs.get("delivery", "confirmed") != "confirmed")):
            continue
        if hierarchy is not None:
            scope, path = hierarchy.classify(obs)
            if scope and (not self_only or scope == "self"):
                found.append({**obs, "query_scope": scope, "query_path": path})
                continue
            if self_only:
                continue
        endpoints = [obs["direct"]]
        if exploratory or association_only:
            endpoints.append(obs["target"])
            endpoints.extend(r for r in obs.get("related", [])
                             if exploratory or r.get("reason") not in {"different", "different_from"})
        elif obs.get("associated_row") and obs.get("delivery") == "confirmed":
            # A measurement explicitly delivered under this target can be found
            # by either endpoint. Keep its direct owner and its observation ID.
            endpoints.append(obs["target"])
        if obs.get("delivery") == "confirmed" and obs.get("associated_row"):
            endpoints.extend(obs["target"].get("members", []))
        endpoint_ids = {endpoint.get("id") for endpoint in endpoints if endpoint.get("status") == "supported"}
        labels = {v for endpoint in endpoints if endpoint.get("status") == "supported"
                  for v in (endpoint.get("label"), *endpoint.get("alias_names", []))
                  if isinstance(v, str)}
        if include_pending and obs.get("row_ids"):
            for endpoint in (obs["direct"], *obs.get("filing_subjects", [obs["target"]]),
                             obs.get("reviewed_filing_subject", {})):
                endpoint_ids.add(endpoint.get("id"))
                labels.update(v for v in (endpoint.get("label"), *endpoint.get("alias_names", []))
                              if isinstance(v, str) and v)
            if isinstance(obs.get("entity"), str):
                labels.add(obs["entity"])
        if obs["direct"].get("basis") == "explicit_definition" and isinstance(obs["entity"], str):
            labels.add(obs["entity"])
        if exploratory:
            for candidate in obs.get("candidates", []):
                endpoint_ids.add(candidate.get("id"))
                if isinstance(candidate.get("label"), str):
                    labels.add(candidate["label"])
            if isinstance(obs["entity"], str):
                labels.add(obs["entity"])
        if (subject is None or subject_match == "exact" and (subject in labels or subject in endpoint_ids)
                or subject_match == "contains" and any(subject in name for name in labels)):
            found.append({**obs, "query_scope": "pending", "query_path": []} if hierarchy else obs)
    return found
