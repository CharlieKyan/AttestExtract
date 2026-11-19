"""Prepare bounded, source-grounded choices for the dataset decision pass."""

from bisect import bisect_right
import hashlib
import json
import re

from .chunking import _protected_positions, _sentence_ends
from .dataset import _has_value_source
from .dataset_resolution import _UNCERTAIN, _literal, _spans
from .dataset_supplement_packs import _valid


_INSTRUCTIONS = (
    "Judge this one recorded observation under the given task. Only original source excerpts "
    "establish scientific support; metadata and candidate names are hypotheses. "
    "Select a subject only when the cited source directly supports that exact subject, value, "
    "property definition, conditions and row scope. Keep distinct measurements separate. "
    "Choose review for ambiguous or insufficient evidence. Choose reject for a contradicted "
    "assignment, a different property or an incompatible scope. Subject names alone are insufficient."
)
_FALLBACKS = {"review": "Evidence is incomplete or ambiguous. Keep available for review.",
              "reject": "Evidence contradicts using this observation for the requested row and field."}
_SOFT_BLOCKERS = {"target_field_unknown"}
_MAX_CHOICES = 6


def _local_proof(source, boundaries, observation, anchors):
    """A prose value's own statement, or the verified owner/header/value path in a table."""
    if observation.get("source_kind") == "table":
        table = observation.get("table_context") or {}
        spans = [table.get("owner"), table.get("owner_role"), *table.get("headers", []),
                 *table.get("context", []), table.get("value"), *anchors]
    else:
        spans = []
        value = str(observation.get("value") if observation.get("value") is not None else "").strip()
        for a in anchors:
            if not value or a["quote"].count(value) != 1:
                continue
            if not _has_value_source({"value": value, "source_evidence": [{"fields": ["value"], "span": a}]}, source):
                continue
            start = a["start"] + a["quote"].index(value)
            left = boundaries[bisect_right(boundaries, start) - 1]
            right = boundaries[bisect_right(boundaries, start + len(value) - 1)]
            spans.append({"start": left, "end": right, "quote": source[left:right]})
    return list({(s["start"], s["end"]): {k: s[k] for k in ("start", "end", "quote")}
                 for s in spans if _valid(source, s)}.values())


def _guards(dataset, obs, source, anchors, local):
    reasons = []
    if not anchors or not _has_value_source(obs, source):
        reasons.append("value_without_unique_source")
    if obs["field"] in dataset.get("identity_fields", []):
        reasons.append("identity_only")
    if obs["fit"]["status"] == "contradicted" or obs.get("target_match") == "unsupported":
        reasons.append("field_contradicted")
    if obs["fit"].get("reason") == "field_qualifier_unverified":
        reasons.append("unreported_field_qualification")
    blockers = {b.get("code") for b in obs.get("blockers", [])}
    if blockers.difference(_SOFT_BLOCKERS):
        reasons.append("source_check_blocked")
    reasons.extend(r for r in obs.get("fit_blockers", [])
                   if r != "original_check_blocked" or not blockers or blockers.difference(_SOFT_BLOCKERS))
    if obs.get("purpose") in {"classification_threshold", "aggregate"}:
        reasons.append("collective_or_classification")
    if obs.get("source_kind") == "table":
        table = obs.get("table_context") or {}
        cell = table.get("value")
        if (not _valid(source, table.get("owner")) or not _valid(source, cell) or not table.get("headers")
                or not all(_valid(source, h) for h in table["headers"])
                or not any(cell["start"] <= a["start"] < a["end"] <= cell["end"] for a in anchors)
                or table.get("scope") != "individual" or table.get("gap") or table.get("qualifier_gap")):
            reasons.append("table_scope_unresolved")
    direct, target = obs["direct"], obs["target"]
    if direct["status"] == target["status"] == "supported" and direct["id"] != target["id"]:
        reasons.append("associated_subject_only")
    if direct.get("reason") in {"conflicting_scoped_definitions", "competing_bound_owner", "competing_source_subjects"}:
        reasons.append("conflicting_subject_evidence")
    if (direct.get("reason") == "qualified_or_competing_statement" or
            (obs.get("source_kind") != "table" and any(_UNCERTAIN.search(s["quote"]) for s in local))):
        reasons.append("qualified_or_competing_statement")
    return reasons


def _options(obs, registry, citations, local, names_by_id, source):
    direct, target = obs["direct"], obs["target"]
    chosen = obs.get("subject_id")
    prior = registry.get(chosen)
    if obs.get("binding_status") == "supported" and prior and (
            not prior.get("is_table_subject") or prior.get("kind") in {"group", "type"}):
        return {}, ["direct_subject_outside_row_grain"]

    fixed = direct.get("id") if direct["status"] == "supported" else None
    endpoints = [target, direct, *obs.get("candidates", [])]
    unrelated = {s.get("id") for s in [*obs.get("related", []), *endpoints]
                 if s.get("reason") in {"member_of", "part_of", "sample_of", "used_in", "different_from"}}
    candidates = {}
    for obj in registry.values():
        if obj.get("is_table_subject") and obj.get("kind") not in {"group", "type"}:
            candidates[obj["object_id"]] = (obj["name"], obj.get("scope"),
                [citations[k] for k in obj.get("mention_evidence_ids", []) if k in citations])
    if target["status"] == "supported" and target.get("id") == fixed and fixed not in registry:
        candidates[fixed] = (target["label"], target.get("scope"),
                             [s for s in target.get("evidence", []) if _valid(source, s)])

    current = obs.get("entity")
    options = {}
    # For tables, restrict subject matching to the actual owner cell (headers and values
    # remain visible to the reviewer). For text, use the statement containing the value.
    owner_context = local
    if obs.get("source_kind") == "table":
        owner = obs["table_context"]["owner"]
        owner_context = [s for s in local if s["start"] == owner["start"] and s["end"] == owner["end"]]
    for key, (label, scope, refs) in sorted(candidates.items()):
        if (not label or key in unrelated or (fixed and fixed != key)
                or (target["status"] == "supported" and key != target["id"])):
            continue
        proof = [s for s in refs if re.search(_literal(label), s["quote"])]
        names = [label]
        if key == chosen and current and current in label and any(current in s["quote"] for s in proof):
            names.append(current)
        # Registration sometimes appends a model-written scope note to the source
        # name. Only expose the literal original name, when both registration and
        # value statement independently cite it. Keep the registered ID.
        if (not proof and current and key == chosen and obs.get("binding_status") == "supported"
                and re.fullmatch(re.escape(current) + r"\s*[（(].+[）)]", label)):
            label, names = current, [current]
            proof = [s for s in refs if re.search(_literal(label), s["quote"])]
        if current and key == chosen and obs.get("binding_status") == "supported":
            local_ids = [s for s in names_by_id.get((key, current), [])
                         if any(t["start"] <= s["start"] < s["end"] <= t["end"] for t in owner_context)]
            if local_ids:
                names.append(current)
                if not proof:
                    label, proof = current, local_ids
        if key == fixed:
            names.extend(direct.get("alias_names", []))
        # An explicitly named component must not be assigned to an overlapping
        # parent name that merely appears elsewhere in the same statement.
        if current and current not in names:
            continue
        local_refs = [s for s in owner_context if any(re.search(_literal(n), s["quote"]) for n in names if n)]
        if not proof or not local_refs:
            continue
        # Selection certifies field and source value as well as ownership. In a
        # table these references belong to distinct cells; retain all in lineage.
        evidence = {(s["start"], s["end"]): {k: s[k] for k in ("start", "end", "quote")}
                    for s in [*local, proof[0]]}
        options[f"bind_{len(options) + 1}"] = {
            "id": key, "label": label, "scope": scope, "evidence": list(evidence.values())}
    if len(options) > _MAX_CHOICES:
        return {}, ["too_many_competing_subjects"]
    return (options, []) if options else ({}, ["no_locally_grounded_task_subject"])


def _packet(dataset, obs, source, registry, citations, boundaries, names_by_id):
    anchors = _spans(source, obs)
    local = _local_proof(source, boundaries, obs, anchors)
    guards = _guards(dataset, obs, source, anchors, local)
    options = {}
    if not guards:
        options, guards = _options(obs, registry, citations, local, names_by_id, source)
    state = {"row_subject": dataset.get("row_subject"),
             "definition": dataset["field_definitions"][obs["field"]],
             "policy": dataset.get("field_policies", {}).get(obs["field"], {}),
             "observation": {k: obs.get(k) for k in (
                 "field", "value", "unit", "conditions", "statistic", "purpose", "entity", "property_name")},
             "source": local or [s for s in obs.get("evidence", []) if _valid(source, s)][:3],
             "subjects": options, "guard_reasons": list(dict.fromkeys(guards))}
    encoded = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    # Use UTF-8 bytes as a conservative prompt bound. Never clip a definition or
    # assertion and then permit a positive decision based on its shortened version.
    if len(encoded.encode("utf-8")) > 6400:
        options = {}
        guards = [*guards, "evidence_exceeds_prompt_limit"]
        encoded = "Partial evidence (review or reject only):\n" + encoded.encode("utf-8")[:5000].decode("utf-8", "ignore")
    criteria = {key: f"Assign to the subject identified by {key} in subjects."
                for key in options}
    request = {"state": encoded, "questions": {"disposition": {
        "type": "choice", "instructions": _INSTRUCTIONS, "criteria": {**criteria, **_FALLBACKS}}}}
    return {"observation_id": obs["observation_id"], "request": request, "options": options,
            "guard_reasons": list(dict.fromkeys(guards))}


def make_packets(dataset, source):
    """Return one native Jev decision problem for every pending dev48 observation."""
    if dataset.get("dataset_version") != "2":
        raise ValueError("Jev review requires a dataset with version 2 delivery statuses.")
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != dataset.get("source_sha256"):
        raise ValueError("Jev review source differs from the frozen dataset.")
    registry = {o["object_id"]: o for o in dataset["lineage"]["objects"]}
    citations = {s["evidence_id"]: s for s in dataset["lineage"]["evidence"] if _valid(source, s)}
    protected, blocked = _protected_positions(source)
    boundaries = sorted({0, len(source), *_sentence_ends(source, 0, len(source), protected, blocked),
                         *(m.end() for m in re.finditer("[;；\n]", source) if not protected[m.start()])})
    names_by_id = {}
    for obs in dataset["observations"]:
        key, name = obs.get("subject_id"), obs.get("entity")
        if (key in registry and isinstance(name, str) and name and obs.get("field") == dataset.get("subject_label_field")
                and obs.get("value") == name and obs.get("binding_status") == obs.get("target_match") == "supported"
                and not obs.get("blockers")):
            names_by_id.setdefault((key, name), []).extend(
                s for s in _spans(source, obs) if re.search(_literal(name), s["quote"]))
    packets = []
    seen = set()
    for obs in dataset["observations"]:
        if not obs.get("observation_id") or obs["observation_id"] in seen:
            raise ValueError("Jev review requires unique observation IDs.")
        seen.add(obs["observation_id"])
        if obs.get("delivery") not in {"confirmed", "detail", "provisional"}:
            raise ValueError("Missing or invalid observation delivery status.")
        if obs["delivery"] != "confirmed":
            packets.append(_packet(dataset, obs, source, registry, citations, boundaries, names_by_id))
    return packets
