"""Decide which evidence-backed, yet unresolved, observations can be displayed."""

import re


def apply_field_verdict(obs, evidence):
    """Respect a source-backed decision on field meaning, independently of the row subject."""
    rejected = obs.get("target_match") == "unsupported"
    proof = [evidence[key] for key in obs.get("binding_evidence_ids", []) if key in evidence]
    reason = obs.get("reason")
    fit = obs.get("fit", {})
    if fit.get("status") == "contradicted" and fit.get("evidence") and fit.get("reason") in {
        "explicit_property_exclusion", "requested_property_unreported", "planned_value_in_measurement_field"
    }:
        rejected, proof, reason = True, fit["evidence"], fit["reason"]
    for review in obs.get("reading_reviews", []):
        verdict = review.get("field_applicability")
        if verdict in {"included", "excluded"}:
            rejected = verdict == "excluded"
            proof = [review["evidence"]]
            reason = review["reason"]
            if not rejected and obs.get("projection_reason") == "model_excluded_field":
                # The prior proposal is retained in row_relations and filing_disagreement.
                obs.pop("field_excluded", None)
                obs.pop("projection_reason", None)
    if rejected and proof:
        obs.update(eligible=False, field_excluded=True, projection_reason="review_excluded_field",
                   field_exclusion={"reason": reason, "evidence": proof})


def _scope_status(candidates):
    scopes = list(dict.fromkeys(c["scope"] for c in candidates if c.get("scope")))
    # A source may explicitly name the differing dimension, e.g. "Batch: A"
    # and "Batch: B". Free descriptions stay verbatim; never infer a dimension.
    keys = [re.match(r"^([^:：\n]{1,32})[:：]", scope) for scope in scopes]
    labels = {match[1].strip() for match in keys if match}
    dimension = labels.pop() if keys and all(keys) and len(labels) == 1 else "适用范围"
    text = "主体已知，" + dimension + "待定"
    if len(scopes) > 1:
        text += "（" + " 或 ".join(scopes) + "）"
    return text

def _pending_subjects(obs, objects):
    direct, target = obs["direct"], obs["target"]
    alternatives = [c for c in obs["candidates"]
                    if c.get("basis") in {"source_alternatives", "source_scope_candidates"} and c.get("evidence")]
    if alternatives:
        return alternatives
    if direct["status"] != "supported":
        return []
    if target["status"] == "supported":
        # A sourced parent association does not transfer a part's measurement.
        return [target] if (direct["id"] == target["id"] or obs.get("associated_row")
                            or obs.get("association_scope_pending")) else []
    if obs.get("extracted_subject_candidate"):
        return [direct]
    if target["reason"] != "target_membership_unresolved":
        return []
    # A scoped local owner can be usable before its identity is resolved.
    # Only reuse a proposed target when its binding cites this owner's value
    # statement. A lexical candidate or disconnected source mentions do not suffice.
    obj = objects.get(obs.get("subject_id"))
    if not (obj and obj.get("is_table_subject") and obs.get("binding_status") == "supported"):
        return []
    for binding in obs["candidates"]:
        if binding.get("id") != obj["object_id"] or binding.get("reason") != "original_binding":
            continue
        for quote in binding["evidence"]:
            if direct["label"] in quote["quote"] and any(
                "value" in e["fields"] and e.get("span") and
                quote["start"] <= e["span"]["start"] < e["span"]["end"] <= quote["end"]
                for e in obs["source_evidence"]
            ):
                return [direct]
    return []


def provisional_row(obs, schema, identities, label_field, objects, policies, identity_values):
    """Return one unconfirmed row or None; preserve the resolver's original decisions."""
    if (obs.get("field_excluded") or obs["fit_blockers"] or obs.get("target_match") == "unsupported" or obs.get("row_relation_conflict")
            or obs.get("corroborates")):
        return None
    fit = obs["fit"]
    field_pending = fit["status"] == "unknown" and fit["reason"] == "field_qualifier_unverified"
    candidates = _pending_subjects(obs, objects)
    related_pending = fit["reason"] == "relation_field_scope_uncertain" and obs.get("association_scope_pending")
    if not candidates or (fit["status"] != "supported" and not (field_pending or related_pending)):
        return None
    if obs["field"] in identities:
        return None
    labels = list(dict.fromkeys(c["label"] for c in candidates if c.get("label")))
    if not labels:
        return None
    label = " 或 ".join(labels)
    row = dict.fromkeys(schema)
    row[obs["field"]] = obs["value"]
    oid = obs["observation_id"]
    cells = {obs["field"]: [oid]}
    bound_target = {}
    if len(candidates) == 1 and candidates[0]["status"] == obs["target"]["status"] == "supported":
        bound_target = obs["target"]
    if bound_target:
        for name in identities:
            possibilities = identity_values.get(name, {})
            if len(possibilities) == 1:
                value, ids = next(iter(possibilities.items()))
                row[name], cells[name] = value, list(ids)
    if label_field and row[label_field] is None:
        if policies.get(label_field) or (bound_target and identity_values.get(label_field)):
            return None
        row[label_field], cells[label_field] = label, [oid]
    if identities and not label_field and any(row[name] is None for name in identities):
        return None
    reasons = []
    if obs.get("field_type_status") == "review":
        reasons.append("字段类型待核：" + obs["field_type_reason"])
    if obs.get("association_scope_pending"):
        reasons.append("关联归档，直接对象是否属于字段允许的类别待定")
    if len(labels) > 1:
        reasons.append("具体主体待定")
    elif any(c.get("basis") in {"source_alternatives", "source_scope_candidates"} for c in candidates):
        reasons.append(_scope_status(candidates))
    elif obs["target"]["status"] != "supported":
        reasons.append("原文主体已知，主表身份待定")
    missing = [name for name in identities if name != label_field and row[name] is None]
    if missing:
        reasons.append("主体已知，" + "、".join(missing) + "待定")
    if field_pending:
        prop = fit["reported_property"]
        # Preserve a more specific original property phrase if the verifier kept
        # it as a statistic or condition (e.g. "mean length" versus "length").
        original = [q for q in (obs.get("statistic"), obs.get("conditions"))
                    if isinstance(q, str) and q.endswith(prop)
                    and any(q in e["quote"] for e in obs["source_evidence"])]
        reported = min(original, key=len, default=prop)
        reasons.append(f"{obs['field']} 字段口径待定：原文为{reported}，"
                       f"是否为{fit['requested_property']}待定")
    if not reasons:
        return None
    lineage = {"subject_id": bound_target.get("id"), "subject_label": label, "scope": bound_target.get("scope"),
               "cells": cells, "observations": list(dict.fromkeys(key for ids in cells.values() for key in ids)),
               "delivery": "provisional", "status": "；".join(reasons), "context": delivered_context([obs]),
               "subject_candidates": candidates}
    return row, lineage, reasons


def delivery_columns(schema, *, filing_subject=False):
    """Allocate the exported context columns without shadowing any user field."""
    used, columns = set(schema), {}
    fields = [("direct", "直接对象"), ("statistic", "观测口径"), ("status", "记录状态")]
    if filing_subject:
        fields.insert(0, ("subject", "主表主体"))
    for key, name in fields:
        while name in used:
            name += "（抽取）"
        used.add(name)
        columns[key] = name
    return columns


def delivered_context(observations):
    """Keep source-backed qualifiers visible beside values in the schema-only CSV."""
    annotations = []
    for obs in observations:
        spans = list(obs["source_evidence"]) + obs["fit"]["evidence"]
        qualifiers = dict.fromkeys(str(v) for v in (obs["conditions"], obs.get("statistic"))
                                   if v and any(str(v) in span["quote"] for span in spans))
        if qualifiers:
            annotations.append(obs["field"] + " " + "、".join(qualifiers))
    return list(dict.fromkeys(annotations))
