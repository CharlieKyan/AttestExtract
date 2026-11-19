"""Project original entity-first proposals without losing their frozen decisions."""

from collections import Counter, defaultdict, deque
from copy import deepcopy

from .admission_receipts import collect_receipts
from .admission_tables import assess_table
from .admission_text import assess_text
from .dataset import _POLICY_COLUMNS, _project
from .entity_first_protocol import Fact
from .evidence import is_missing
from .execution import digest
from .structure import build_structure


def _baseline(report, data):
    observations = defaultdict(deque)
    rejected = defaultdict(deque)
    conflicts = {}
    for obs in data["observations"]:
        if "model_claim" in obs:
            observations[(obs["block_id"], digest(obs["model_claim"]))].append(obs)
    for issue in report["issues"]:
        if "rejected_fact" in issue:
            rejected[(issue["block"], digest(issue["rejected_fact"]))].append(issue["reason"])
        if "competing_claim" in issue:
            conflicts[(issue["block"], digest(issue["competing_claim"]))] = issue["observation_id"]
    return observations, rejected, conflicts


def _candidate_label(packet, answer, ref):
    if not ref:
        return None, "not_proposed"
    entries = [(c["label"], "shown_card") for c in packet["objects"] if c["object_id"] == ref]
    entries.extend((n["label"], "same_answer_nomination") for n in answer["nominations"] if n["key"] == ref)
    return entries[0] if len(entries) == 1 else (None, "ambiguous_or_unknown_reference")


def _unknown(reason):
    return {"id": None, "label": None, "status": "unknown", "evidence": [], "reason": reason}


def _detail(data, prior, fact, update, window, proposal_id, baseline, label):
    field = fact["field"]
    item = deepcopy(prior) if prior else {
        "source_record": None, "candidates": [], "related": [], "evidence": [],
        "direct": _unknown("local_subject_unresolved"), "target": _unknown("target_unresolved"),
        "fit": {**_unknown("field_unverified"),
                "status": "contradicted" if fact["fit"] == "unsupported" else "unknown"},
        "value_source": None, "property_source": None, "record_group": proposal_id,
    }
    if not prior and label:
        item["candidates"].append({"label": label, "id": None, "status": "proposed"})
    item.pop("projection_reason", None)
    item.update(update)
    if not update and prior:
        item["fit"] = {**item["fit"], "status": "unknown", "reason": "source_relation_requires_review"}
    if prior and not item.get("value_source") and prior.get("value_source"):
        # The older source coordinate remains inspectable even if a different
        # ownership or field check fails in this view.
        item["value_source"] = prior["value_source"]
        if item["value_source"] not in item["evidence"]:
            item["evidence"] = [item["value_source"], *item["evidence"]]
    item.update(field=field, value=fact["value"], unit=fact["unit"], conditions=fact["conditions"],
                statistic=fact["statistic"], purpose=fact["purpose"], model_reason=fact["reason"],
                block_id=window["id"], source_kind=window["kind"], row_ids=[], proposal_ids=[proposal_id])
    item["entity"] = item["direct"].get("label") if item["direct"]["status"] == "supported" else None
    blockers = [k for k, allowed in data["field_policies"].get(field, {}).items()
                if item.get(_POLICY_COLUMNS[k]) not in allowed]
    target = item["target"]
    if field not in data["schema"]:
        blockers.append("undeclared_field")
    if is_missing(item["value"]):
        blockers.append("missing_value")
    if field == data["subject_label_field"] and target["status"] == "supported":
        labels = {target.get("label"), *target.get("alias_names", ())}
        if item["value"] not in labels:
            blockers.append("conflicting_subject_label")
    if baseline == "not_matched":
        blockers.append("proposal_without_matching_frozen_disposition")
    if baseline == "coalesced_conflict" or prior and prior["reason"] == "conflicting_assignments":
        blockers.append("conflicting_assignments")
    if fact["fit"] == "unsupported":
        item["fit"] = {**item["fit"], "status": "contradicted", "reason": fact["reason"]}
    item["fit_blockers"] = blockers
    item["eligible"] = bool(item.get("value_source") and not blockers
                            and all(item[k]["status"] == "supported" for k in ("direct", "target", "fit"))
                            and target.get("id") and item["direct"].get("id") == target["id"])
    item["qualified"] = item["eligible"]
    item["owner_supported"] = item["direct"]["status"] == "supported"
    item["owner_id"] = target.get("id") if target["status"] == "supported" else None
    item["field_supported"] = item["fit"]["status"] == "supported" and not blockers
    item["field_fit"] = fact["fit"]
    item["reason"] = next(iter(blockers), None) or next(
        (item[k].get("reason") or k + "_unresolved" for k in ("direct", "target", "fit")
         if item[k]["status"] != "supported"), "source_supported")
    return item


def _anchor(item):
    value, prop = item.get("value_source"), item.get("property_source")
    if not value:
        return None
    return (item["field"], value["start"], value["end"],
            (prop["start"], prop["end"]) if prop else None)


def audit_dataset(source, report, strict, run_dir=None, *, frozen_view=None):
    """Return a new view. The strict snapshot and each model claim remain intact."""
    receipts, coverage = collect_receipts(source, report, run_dir)
    data = deepcopy(strict)
    if frozen_view is None:
        frozen_view = strict
    by_claim, rejections, conflicts = _baseline(report, frozen_view)
    old_items = {o["observation_id"]: o for o in frozen_view["observations"]}
    strict_rows = frozen_view["rows"]
    superseded = {}
    superseded_seen = set()
    for call in report["calls"]:
        for event in call.get("revisit_decisions", []):
            if event["action"] == "replaced":
                previous = event["previous_observation"]
                key = (previous["block_id"], digest(previous["model_claim"]))
                superseded[key] = previous
    anchored_ids = {_anchor(o): oid for oid, o in old_items.items() if _anchor(o) is not None}
    tables = build_structure(source).tables if any(
        r["packet"]["windows"][0].get("kind") == "table" for r in receipts) else []
    proposals = []
    observations = {}
    matched = {}
    for receipt in receipts:
        packet, answer = receipt["packet"], receipt["answer"]
        window = packet["windows"][0]
        block = window["id"]
        revisit_events = {event["fact_index"]: event for event in
                          report["calls"][receipt["call"] - 1].get("revisit_decisions", [])}
        table = next((t for t in tables if t.span.start <= window["start"] < t.span.end), None)
        for index, raw in enumerate(answer.get("facts", [])):
            fact = Fact.model_validate(raw).model_dump(mode="json")
            pid = receipt["id"] + "/facts/" + str(index)
            key = (block, digest(fact))
            old_reason = None
            event = revisit_events.get(index)
            replaced = superseded.get(key) if receipt["stage"] == "attributes" else None
            if replaced:
                baseline = "coalesced_before_revisit" if key in superseded_seen else "superseded_by_revisit"
                prior, old_reason = None, replaced["reason"]
                superseded_seen.add(key)
            elif event:
                baseline = {"replaced": "retained", "coalesced": "coalesced",
                            "conflicting": "coalesced_conflict", "rejected": "rejected"}[event["action"]]
                prior = old_items.get(event["observation_id"]) if baseline != "rejected" else None
                old_reason = event["reason"]
            elif by_claim[key]:
                prior = by_claim[key].popleft()
                baseline = "retained"
                matched[key] = prior
            elif rejections[key]:
                prior, baseline, old_reason = None, "rejected", rejections[key].popleft()
            elif key in conflicts:
                prior, baseline = old_items.get(conflicts[key]), "coalesced_conflict"
            elif key in matched:
                prior, baseline = matched[key], "coalesced"
            else:
                prior, baseline = None, "not_matched"
            if prior and not event:
                old_reason = prior["reason"]
            label, label_basis = _candidate_label(packet, answer, fact["owner"])
            args = (packet, answer, fact, report, prior, data["subject_label_field"])
            update = assess_table(source, table, *args) if window["kind"] == "table" else assess_text(source, *args)
            item = _detail(data, prior, fact, update, window, pid, baseline, label)
            span = item.get("value_source")
            # Cite each proposal separately even when evidence identifies the same measurement.
            anchor = _anchor(item)
            if replaced:
                oid = replaced["observation_id"]
            elif prior:
                oid = prior["observation_id"]
                if anchor is not None:
                    anchored_ids.setdefault(anchor, oid)
            elif anchor is not None:
                oid = anchored_ids.setdefault(anchor, "auditobs-" + digest([data["source_sha256"], anchor])[:24])
            else:
                oid = None
            item["observation_id"] = oid
            unmatched_revisit = receipt["stage"] == "revisit" and prior is None
            if unmatched_revisit:
                item.update(qualified=False, eligible=False, reason="unmatched_revisit")
            if replaced or unmatched_revisit:
                pass  # These are independently visible proposals, never competing measurements.
            elif oid in observations:
                previous = observations[oid]
                previous["proposal_ids"].append(pid)
                compared = ("value", "unit", "conditions", "statistic", "purpose", "owner_id", "eligible")
                if any(previous.get(k) != item.get(k) for k in compared):
                    previous.update(eligible=False, qualified=False, reason="conflicting_assignments")
            elif span:
                observations[oid] = item
            proposals.append({
                "proposal_id": pid, "direct_subject": item["entity"],
                "source_property": item.get("source_property") or (item.get("property_source") or {}).get("quote"),
                "value": fact["value"], "unit": fact["unit"], "conditions": fact["conditions"],
                "statistic": fact["statistic"], "purpose": fact["purpose"],
                "candidate_subject": label, "suggested_field": fact["field"],
                "reason": item["reason"], "source_kind": window["kind"], "block_id": block,
                "table_id": table.table_id if table is not None and window["kind"] == "table" else None,
                "observation_id": oid if span and not unmatched_revisit else None, "value_source": span, "evidence": item["evidence"],
                "local_mention": item.get("local_mention") or item.get("owner_source"),
                "members": item.get("members", []),
                "direct": item["direct"], "target": item["target"], "fit": item["fit"],
                "candidate_basis": label_basis, "prior_disposition": baseline, "prior_reason": old_reason,
                "revisit_decision": ({k: event[k] for k in ("action", "observation_id", "reason")} if event else None),
                "prior_decisions": {k: prior[k] for k in ("direct", "target", "fit", "fit_blockers")}
                                   if prior else ({"owner_supported": replaced["owner_supported"],
                                                   "field_supported": replaced["field_supported"],
                                                   "qualified": replaced["qualified"]} if replaced else None),
                "policy_blockers": item["fit_blockers"],
                "prior_evidence_issues": prior.get("evidence_issues", []) if prior else [],
                "prior_row_ids": prior["row_ids"] if prior else [], "raw_claim": raw,
                "call": receipt["call"], "stage": receipt["stage"], "answer_index": index,
                "artifact_stem": receipt["artifact_stem"], "request_digest": receipt["request_digest"],
                "answer_digest": receipt["answer_digest"],
            })
    # Gaps in historical diagnostic coverage do not erase the independently frozen observations.
    for oid, old in old_items.items():
        if oid not in observations:
            observations[oid] = {**deepcopy(old), "row_ids": [], "proposal_ids": [],
                                 "eligible": False, "qualified": False, "reason": "missing_proposal_receipt"}
    items = list(observations.values())
    rows, lineage = _project(data["schema"], items, data["identity_fields"], data["source_sha256"],
                             data["field_policies"], data["subject_label_field"])
    for p in proposals:
        item = observations.get(p["observation_id"])
        replaced = p["prior_disposition"] in {"superseded_by_revisit", "coalesced_before_revisit"}
        if item and replaced:
            item["proposal_ids"].append(p["proposal_id"])
        p["row_ids"] = item["row_ids"] if item and not replaced else []
        p["disposition"] = ("superseded" if replaced else "in_main" if p["row_ids"]
                            else "located_detail" if p["value_source"] else "proposal_only")
        if item and not replaced:
            p["reason"] = item.get("projection_reason") or item["reason"]
    data.update(rows=rows, observations=items, proposals=proposals, admission_version="1",
                admission={"coverage": coverage, "strict_row_count": len(strict_rows),
                           "calls": [{"id": r["id"], "call": r["call"], "stage": r["stage"],
                                      "shown_objects": r["packet"]["objects"], "nominations": r["answer"]["nominations"],
                                      "windows": [{"id": w["id"], "role": w["role"], "start": w["start"],
                                                   "end": w["start"] + len(w["text"])}
                                                  for w in r["packet"]["windows"]]} for r in receipts],
                           "frozen_details_without_receipt": [o["observation_id"] for o in items if not o["proposal_ids"]],
                           "prior_dispositions": dict(Counter(p["prior_disposition"] for p in proposals)),
                           "strict_rows": strict_rows})
    data["lineage"]["rows"] = lineage
    objects = {o["object_id"]: o for o in data["lineage"]["objects"]}
    evidence = {(s["start"], s["end"]): s for s in data["lineage"]["evidence"]}
    for o in items:
        target = o["target"]
        if target.get("id") and target["status"] == "supported":
            objects.setdefault(target["id"], {**target, "object_id": target["id"]})
        for span in [*o["evidence"], *target.get("evidence", [])]:
            evidence[(span["start"], span["end"])] = span
    data["lineage"].update(objects=list(objects.values()), evidence=list(evidence.values()))
    return data
