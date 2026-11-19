"""Project the frozen two-pass decisions into rows and human-readable proposal receipts."""

from copy import deepcopy

from .dataset import _POLICY_COLUMNS, _project


def project_two_pass(report, task, info, options):
    fields = task.input_materials.field_list
    label = options.get("subject_label_field")
    identities = options.get("identity_fields") or ([label] if label else [])
    policies = options.get("field_policies", {})
    objects = {o["object_id"]: o for o in report["objects"]}
    observations = deepcopy(report["observations"])
    evidence = {}
    for obj in objects.values():
        for span in obj["evidence"]:
            evidence[span["start"], span["end"]] = span
    for index, obs in enumerate(observations):
        obs.update(source_record=f"/candidates/{index}", proposal_ids=[], row_ids=[])
        blockers = [key for key, allowed in policies.get(obs["field"], {}).items()
                    if obs.get(_POLICY_COLUMNS[key]) not in allowed]
        target = objects.get(obs["target"].get("id"))
        if target:
            names = {target["label"], *(a["name"] for a in target["aliases"])}
            if obs["field"] == label and obs["value"] not in names:
                blockers.append("conflicting_subject_label")
            obs["identity_attribute"] = (obs["value"] in names and obs.get("owner_source") is not None
                                         and obs.get("owner_source") == obs.get("value_source"))
        obs["fit_blockers"] = blockers
        obs["eligible"] = obs["qualified"] and not blockers
        if blockers:
            obs["projection_reason"] = blockers[0]
        for span in obs["evidence"]:
            evidence[span["start"], span["end"]] = span
    rows, row_lineage = _project(fields, observations, identities, info.sha256, policies, label,
                                 require_attribute=True)
    regions = [{"block_id": window["block_id"], **region} for block in report["blocks"]
               for window in [block, *block.get("recovery", [])] for region in window.get("review_regions", [])]
    by_id = {o["observation_id"]: o for o in observations}
    proposals, coverage = [], []
    for number, call in enumerate(report["calls"], 1):
        if call["stage"] != "attributes":
            continue
        entry = {"call": number, "blocks": call["blocks"], "stage": "attributes",
                 "artifact_stem": call["artifact_stem"]}
        if call["state"] != "complete":
            coverage.append({**entry, "status": "gap", "reason": call["state"], "proposals": None})
            continue
        receipt = call["assignment_receipt"]
        claims = receipt["answer"]["facts"]
        choices = {o["object_id"]: o["label"] for o in receipt["packet"]["objects"]}
        choices.update({o["key"]: o["label"] for o in receipt["answer"].get("nominations", [])})
        coverage.append({**entry, "status": "complete", "proposals": len(claims)})
        for index, (claim, decision) in enumerate(zip(claims, call["fact_decisions"], strict=True)):
            if index != decision["fact_index"]:
                raise ValueError("Frozen proposal order differs from its decision.")
            obs = by_id[decision["observation_id"]]
            local = {**obs, **decision.get("assessment", {})}
            pid = f"{report['admission_run_id']}/{call['artifact_stem']}/facts/{index}"
            obs["proposal_ids"].append(pid)
            ref = claim.get("target_id") or claim.get("nomination_key")
            proposals.append({
                "proposal_id": pid, "observation_id": obs["observation_id"], "row_ids": list(obs["row_ids"]),
                "direct_subject": local["direct"].get("label"), "source_subject": claim.get("source_subject"),
                "candidate_subjects": obs["candidates"],
                "candidate_subject": choices.get(ref), "candidate_basis": (
                    "shown_card" if claim.get("target_id") else "same_answer_nomination" if ref else "not_proposed"),
                "direct": obs["direct"], "target": obs["target"], "fit": obs["fit"],
                "source_property": (local.get("property_source") or {}).get("quote"),
                "value": claim["value"], "unit": claim.get("unit"), "conditions": claim.get("conditions"),
                "statistic": claim.get("statistic"), "purpose": claim["purpose"], "suggested_field": claim["field"],
                "evidence": local["evidence"], "value_source": local.get("value_source"), "block_id": obs["block_id"],
                "source_kind": obs["source_kind"], "table_id": obs.get("table_id"), "members": local.get("members", []),
                "reason": obs.get("projection_reason") or obs["reason"], "policy_blockers": obs["fit_blockers"],
                "disposition": "in_main" if obs["row_ids"] else "located_detail" if local.get("value_source") else "proposal_only",
                "call": number, "answer_index": index, "artifact_stem": call["artifact_stem"],
                "action": decision["action"], "assignment_assessment": decision["assessment"], "raw_claim": claim,
            })
    return {
        "dataset_version": "1", "protocol_version": report["protocol_version"], "subject_strategy": "entity-first",
        "document_id": info.document_id, "source_name": info.name, "source_sha256": info.sha256,
        "schema": list(fields), "field_definitions": fields, "identity_fields": list(identities),
        "subject_label_field": label, "row_subject": report["table_subject"], "field_policies": policies,
        "row_identity_location": "schema_columns" if identities else "lineage_rows_in_csv_order",
        "rows": rows, "observations": observations, "proposals": proposals, "coverage_gaps": report["issues"],
        "admission": {"coverage": coverage},
        "lineage": {"rows": row_lineage, "objects": report["objects"], "evidence": list(evidence.values()),
                    "relations": [], "quarantined": [], "nominations": report["nominations"], "review_regions": regions},
        "budget": {"assembly_calls": 0, "entity_first": report["budget"],
                   "extraction_http_attempts": report["budget"]["http_attempts"]},
    }
