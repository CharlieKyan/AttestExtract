"""Project cited first-pass associations at the scope of their original observation."""

import re

from .dataset_identity import literal_name
from .execution import digest
from .value_location import locate_value


def _grounded_receipt(source, obs, receipt):
    """Check the cited occurrences. The model supplies the direction and meaning."""
    evidence = receipt["evidence"]
    if receipt["status"] != "located" or not evidence:
        return False
    for entry in evidence:
        start, end = entry.get("start"), entry.get("end")
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source)
                or source[start:end] != entry["quote"]):
            return False
    mentions = [e for e in evidence if re.search(literal_name(receipt["target"]), e["quote"])]
    if not mentions:
        return False
    path = obs.get("table_context")
    if path:
        if path.get("gap") or path.get("qualifier_gap") or not path.get("owner"):
            return False
        # The independently checked source cell and its heading can also
        # establish the parent in a three-column (or transposed) record.
        cells = [path["owner"], *path.get("headers", []), *path.get("row_relation_cells", [])]
        allowed = {cell["ref"] for cell in cells}
        return (all(e.get("ref") in allowed for e in evidence)
                and any(e.get("ref") == path["owner"]["ref"] for e in evidence))
    owner = re.compile(literal_name(obs["direct"]["label"]))
    occurrences = set()
    for entry in obs["source_evidence"]:
        span = entry.get("span")
        if ("entity" not in entry["fields"] or not span or len(entry.get("matches", [])) > 1
                or len(entry.get("source_matches", [])) > 1):
            continue
        occurrences.update((m.start(), m.end()) for m in owner.finditer(source, span["start"], span["end"]))
    if len(occurrences) != 1:
        return False
    left, right = occurrences.pop()
    # A repeated label in another batch is not this occurrence. Cross-block
    # associations can still be supplied by the existing scoped verifier.
    return any(e["start"] <= left < right <= e["end"] for e in mentions)


def _unsettled(obs, reason):
    obs.pop("associated_row", None)
    obs.pop("association_scope_pending", None)
    obs["target"] = {**obs["target"], "status": "unknown", "id": None, "label": None, "reason": reason}


def apply_row_relation(owners, obs):
    if (obs.get("context_role_evidence") and obs["target"].get("reason") == "extracted_row_subject"
            and obs["direct"].get("reason") == "source_linked_extracted_owner"):
        _unsettled(obs, "property_value_without_row_identity")
        obs["target"]["evidence"] = obs["context_role_evidence"]
    receipts = obs.get("row_relations", [])
    if not receipts or not owners.same_task:
        return
    # Current observations carry a model explanation, including applicability to
    # this particular field. FilingContext preserves that judgment for the reader.
    if any("explanation" in r for r in receipts):
        return
    alternatives = all(r.get("alternative") for r in receipts)
    if obs.get("name_handling") != ("uncertain" if alternatives else "related"):
        return
    direct = obs["direct"]
    if direct["status"] != "supported" or direct.get("label") != obs.get("entity"):
        return
    if any(not r["target"] or r["target"] == direct["label"] or
           not _grounded_receipt(owners.source, obs, r) for r in receipts):
        return
    previous = obs["target"]
    targets = {r["target"] for r in receipts}
    judgements = {(r["predicate"], r["field_applicability"], r.get("alternative", False)) for r in receipts}
    existing_parent = previous["status"] == "supported" and previous["id"] != direct["id"]
    if ((existing_parent and (previous["label"] not in targets or alternatives))
            or (not alternatives and (len(targets) != 1 or len(judgements) != 1))):
        _unsettled(obs, "conflicting_relation_targets")
        obs["row_relation_conflict"] = True
        return
    if previous["reason"] in {"multiple_related_targets", "competing_source_subjects", "conflicting_row_membership"}:
        return
    evidence = [e for r in receipts for e in r["evidence"]]
    if alternatives:
        if len(targets) < 2:
            return
        _unsettled(obs, "explicit_alternative_targets")
        for r in receipts:
            obs["candidates"].append({"id": None, "label": r["target"], "source_id": direct["id"],
                                      "reason": r["predicate"], "scope": direct.get("scope"),
                                      "status": "unknown", "basis": "source_alternatives", "evidence": r["evidence"]})
    else:
        receipt = receipts[0]
        label = receipt["target"]
        target_id = (previous["id"] if existing_parent else
                     "row-mention-" + digest([obs["observation_id"], label])[:24])
        target = {"id": target_id, "label": label, "status": "supported", "source_id": direct["id"],
                  "scope": direct.get("scope"), "evidence": evidence, "reason": receipt["predicate"],
                  "basis": "source_linked_extraction_relation"}
        obs["target"] = target
        obs["related"].append(target)
    obs.pop("association_scope_pending", None)
    obs.pop("associated_row", None)
    fit = obs["fit"]
    applicability = {r["field_applicability"] for r in receipts}
    if "excluded" in applicability:
        obs["fit"] = {**fit, "status": "contradicted" if len(applicability) == 1 else "unknown",
                      "reason": "relation_field_scope_excluded", "evidence": evidence}
        return
    if fit["status"] == "contradicted" or obs.get("target_match") == "unsupported":
        return
    uncertain = "uncertain" in applicability
    # The optional model answer also judges this *particular field*. Preserve
    # any contrary field verdict and require a separately located value claim.
    value_claim = any(locate_value(owners.source, obs["value"], e["start"], e["end"]) for e in fit["evidence"])
    if fit["reason"] == "field_definition_unresolved" and value_claim:
        obs["fit"] = {**fit, "status": "unknown" if uncertain else "supported", "id": obs["field"],
                      "label": obs["field"], "basis": "model_proposed",
                      "reason": "relation_field_scope_uncertain" if uncertain else "relation_field_scope_included"}
    if uncertain:
        obs["association_scope_pending"] = True
    elif not alternatives:
        obs["associated_row"] = True
