"""Decide which evidence-backed, yet unresolved, observations can be displayed."""

import re


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
        return [target] if direct["id"] == target["id"] else []
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
    if obs["fit_blockers"] or obs.get("target_match") == "unsupported":
        return None
    fit = obs["fit"]
    field_pending = fit["status"] == "unknown" and fit["reason"] == "field_qualifier_unverified"
    candidates = _pending_subjects(obs, objects)
    if not candidates or (fit["status"] != "supported" and not field_pending):
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


def delivery_status_column(schema):
    name = "记录状态"
    while name in schema:
        name += "（抽取）"
    return name


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
