"""Attach source and interpretation receipts to displayed values without changing them."""

from .evidence import is_missing


BASIS_LABELS = {"reported": "原文明示", "inferred": "模型推断", "unknown": "尚未判明"}
CITATION_LABELS = {"located": "已定位", "unverified": "引文未核实"}
TYPED_READING_COLUMNS = ("value_role", "numeric_shape", "numeric_value", "numeric_unit",
                        "field_type_status", "field_type_reason")


def _reading(obs):
    entry = {"observation_id": obs["observation_id"], "value": obs["value"],
             "statement_basis": obs.get("statement_basis", "unknown"),
             "citation_status": obs.get("citation_status", "unverified"),
             "citation_reason": obs.get("citation_reason"), "evidence": obs.get("citation_evidence", []),
             "measurement_use": obs.get("measurement_use", "unspecified"), "statistic": obs.get("statistic")}
    if "jev_review" in obs:
        entry["review_opinion"] = obs["jev_review"]["state"]
    entry.update({key: obs[key] for key in TYPED_READING_COLUMNS if key in obs})
    if "reviewed_filing_subject" in obs:
        entry["reviewed_filing_subject"] = obs["reviewed_filing_subject"]
    return entry


def filing_evidence(endpoint, citations):
    """Resolve the filing subject's mentions and relationship citations on their own."""
    spans = []
    for ref in [*endpoint.get("evidence", []), *endpoint.get("evidence_ids", []), *endpoint.get("relation_evidence", [])]:
        span = citations.get(ref) if isinstance(ref, str) else ref
        if isinstance(span, dict) and "start" not in span:
            span = citations.get(span.get("evidence_id"))
        if (span and type(span.get("start")) is int and type(span.get("end")) is int
                and span["end"] > span["start"] >= 0 and isinstance(span.get("quote"), str)
                and len(span["quote"]) == span["end"] - span["start"] and span not in spans):
            spans.append(span)
    return spans


def _subject(obs, endpoint, citations):
    spans = filing_evidence(endpoint, citations)
    return {"observation_id": obs["observation_id"], "subject_id": endpoint.get("id"),
            "direct_subject_id": obs["direct"].get("id"), "relation_id": endpoint.get("relation_id"),
            "value": endpoint.get("label"), "statement_basis": endpoint.get("statement_basis", "unknown"),
            "citation_status": endpoint.get("filing_citation_status", "located" if spans else "unverified"),
            "citation_reason": endpoint.get("filing_citation_reason"), "evidence": spans}


def finalize_annotations(data):
    """Rebuild cell-level receipts after ordinary projection or a saved review."""
    observations = {o["observation_id"]: o for o in data["observations"]}
    citations = {s["evidence_id"]: s for s in data["lineage"]["evidence"]}
    label = data.get("subject_label_field")
    stats = [c for parts in data.get("statistic_columns", {}).values() for c in parts.values()]
    fields = [*data["schema"], *stats]
    used = {*fields, *data.get("context_columns", {}).values(), data["status_column"]}
    columns = {}
    for row, proof in zip(data["rows"], data["lineage"]["rows"], strict=True):
        receipts = {}
        subjects = []
        for field, ids in proof["cells"].items():
            if is_missing(row[field]):
                continue
            parts = []
            for oid in ids:
                obs = observations[oid]
                if obs["field"] != field and field == label:
                    for endpoint in obs.get("filing_subjects", [obs["target"]]):
                        if endpoint.get("label") and (not proof.get("subject_id") or endpoint.get("id") == proof["subject_id"]):
                            parts.append(_subject(obs, endpoint, citations))
                else:
                    parts.append(_reading(obs))
            receipts[field] = parts
        for field, entry in proof.get("statistics", {}).items():
            if not is_missing(entry["value"]):
                receipts[field] = [_reading(observations[oid]) for oid in entry["observations"]]
        for oid in proof["observations"]:
            obs = observations[oid]
            for endpoint in obs.get("filing_subjects", [obs["target"]]):
                if endpoint.get("label") and (not proof.get("subject_id") or endpoint.get("id") == proof["subject_id"]):
                    subjects.append(_subject(obs, endpoint, citations))
        proof["cell_annotations"] = receipts
        proof["subject_annotations"] = subjects
        if proof["status"].startswith("已确认"):
            proof["status"] = "已入表" + proof["status"].removeprefix("已确认")
        for field in receipts:
            if field in columns:
                continue
            columns[field] = {}
            for key, suffix in (("statement_basis", "_依据"), ("citation_status", "_引文状态")):
                name = field + suffix
                while name in used:
                    name += "（抽取）"
                used.add(name)
                columns[field][key] = name
    data["annotation_columns"] = columns


def display_annotations(receipts, key, fallback):
    labels = BASIS_LABELS if key == "statement_basis" else CITATION_LABELS
    values = {entry.get(key, fallback) for entry in receipts}
    if len(values) <= 1:
        return labels[next(iter(values), fallback)]
    # Merged min/max/average cells may contain several differently grounded readings.
    parts = []
    for entry in receipts:
        label = labels[entry.get(key, fallback)]
        if entry.get("statistic"):
            label = f'{entry["statistic"]}，{label}'
        parts.append(f'{entry["value"]}（{label}）')
    return "｜".join(dict.fromkeys(parts))
