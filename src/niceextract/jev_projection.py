"""Apply closed-choice model receipts to a copy of a frozen dataset."""

from collections import Counter, defaultdict
from copy import deepcopy

from .dataset import _confirmed_corroborations, _project
from .dataset_annotations import finalize_annotations
from .dataset_statistics import set_statistic_columns
from .execution import digest


def _assert_supported_option(obs, packet, endpoint):
    if (packet["guard_reasons"] or not endpoint.get("id") or not endpoint.get("label")
            or not endpoint.get("evidence") or obs["fit"]["status"] == "contradicted"
            or obs.get("target_match") == "unsupported"):
        raise ValueError("Jev selection violates a frozen evidence constraint.")
    local_id = endpoint.get("local_subject_id")
    if local_id and not (obs["direct"]["status"] == "supported" and obs["target"]["status"] != "supported"
                         and obs["direct"].get("basis") == "extraction_assignment"
                         and obs["direct"]["id"] == local_id == "mention-" + obs["observation_id"]
                         and obs["direct"]["label"] == endpoint["label"]):
        raise ValueError("Jev selection changes the local source subject.")
    for axis in ("direct", "target"):
        if obs[axis]["status"] == "supported" and obs[axis]["id"] != endpoint["id"]:
            if axis == "direct" and local_id:
                continue
            raise ValueError("Jev selection changes a supported subject or transfers its property.")


def _accept(obs, endpoint):
    if endpoint.get("local_subject_id"):
        direct = obs["direct"]
        obs["direct"] = {**direct, "id": endpoint["id"], "original_subject_id": direct["id"],
                         "basis": "jev_choice", "reason": "source_mention_identified",
                         "evidence": direct["evidence"] + deepcopy(endpoint["evidence"])}
    for axis in ("direct", "target"):
        if obs[axis]["status"] != "supported":
            obs[axis] = {**deepcopy(endpoint), "status": "supported", "basis": "jev_choice",
                         "reason": "selected_evidence_backed_subject"}
    if obs["fit"]["status"] != "supported":
        obs["fit"] = {"status": "supported", "basis": "jev_choice", "reason": "selected_field_and_subject",
                      "evidence": deepcopy(endpoint["evidence"])}
    obs.update(eligible=True, fit_blockers=[], delivery="detail", row_ids=[])
    obs.pop("projection_reason", None)
    obs.pop("delivery_reasons", None)
    obs.pop("delivery_candidates", None)


def _measurement(obs):
    """Compare exact value occurrences even if two answers quote different amounts of context."""
    spans = []
    value = str(obs["value"]).strip()
    for item in obs["source_evidence"]:
        span, quote = item.get("span"), item.get("quote", "")
        if (not span or "value" not in item["fields"] or not value or quote.count(value) != 1
                or len(quote) != span["end"] - span["start"]):
            continue
        left = span["start"] + quote.index(value)
        spans.append((left, left + len(value)))
    if not spans:
        return None
    return digest([obs["field"], obs["value"], obs["unit"], obs["conditions"],
                   obs.get("purpose"), obs.get("statistic"), sorted(set(spans))])


def _group_key(proof, by_id, identities):
    # Only share a row when the source provides an explicit common record group.
    readings = [by_id[oid] for field, ids in proof["cells"].items() if field not in identities for oid in ids]
    if not readings or not all(obs.get("record_group") for obs in readings):
        return None
    keys = {digest([proof["subject_id"], obs["direct"]["id"], obs["record_group"], obs["conditions"], obs.get("statistic"),
                    obs.get("purpose")]) for obs in readings}
    return keys.pop() if len(keys) == 1 else None


def _share_confirmed_names(by_id, selected):
    """Index proven spellings of the same stable ID in the final searchable view."""
    reviewed = {by_id[oid]["direct"]["id"] for oid in selected if by_id[oid]["delivery"] == "confirmed"}
    names = defaultdict(dict)
    for obs in by_id.values():
        owner = obs["direct"]
        if obs["delivery"] != "confirmed" or owner["id"] not in reviewed:
            continue
        for label in [owner["label"], *owner.get("alias_names", [])]:
            if label:
                names[owner["id"]].setdefault(label, {"observation_id": obs["observation_id"],
                                                       "basis": owner.get("basis")})
    for obs in by_id.values():
        owner = obs["direct"]
        if obs["delivery"] != "confirmed" or owner["id"] not in reviewed:
            continue
        aliases = set(owner.get("alias_names", []))
        added = {label: receipt for label, receipt in names[owner["id"]].items()
                 if label != owner["label"] and label not in aliases}
        if added:
            owner["alias_names"] = sorted(aliases.union(added))
            owner["jev_alias_sources"] = added


def _filing_opinion(obs, packet, decision):
    """Keep the reader-visible original claim and attach the independent opinion to it."""
    review = obs["jev_review"] = {"state": "not_scored", "prior_delivery": obs["delivery"],
                                  "guard_reasons": list(packet["guard_reasons"])}
    if decision is None:
        return
    choice = decision["choice"]
    review.update(choice=choice, confidence=decision["confidence"], job_id=decision.get("job_id"))
    if choice in {"review", "reject"}:
        review["state"] = "review" if choice == "review" else "disagreed"
        return
    target = packet["options"].get(choice)
    if (packet["guard_reasons"] or not target or target.get("role") != "filing_subject"
            or not target.get("id") or not target.get("label") or not target.get("evidence")):
        raise ValueError("Jev filing choice needs an offered, cited filing subject.")
    obs["reviewed_filing_subject"] = deepcopy(target)
    review.update(state="filing_suggested", endpoint_id=target["id"], evidence=deepcopy(target["evidence"]))


def final_dataset(dataset, packets, decisions):
    """Preserve base rows, replace adjudicated provisional facts and attach per-cell provenance."""
    result = deepcopy(dataset)
    schema, identities = result["schema"], result["identity_fields"]
    by_id = {obs["observation_id"]: obs for obs in result["observations"]}
    original = {obs["observation_id"]: obs for obs in dataset["observations"]}
    selected, removed, duplicate = set(), set(), {}
    already_published = _confirmed_corroborations(dataset["observations"])
    for packet in packets:
        oid = packet["observation_id"]
        obs = by_id[oid]
        if (oid, obs.get("corroborates")) in already_published:
            continue
        decision = decisions.get(oid)
        if packet.get("review_axis") == "field_type":
            # An opinion about the column cannot change an owner or create a normalized number.
            verdict = decision["choice"] if decision else None
            if verdict and verdict not in packet["request"]["questions"]["disposition"]["criteria"]:
                raise ValueError("Field opinion was not offered for this observation.")
            obs["jev_review"] = {"state": "field_opinion" if decision else "not_scored",
                                 "field_verdict": verdict, "guard_reasons": packet["guard_reasons"],
                                 "job_id": decision.get("job_id") if decision else None}
            continue
        if "annotation_columns" in dataset and obs["row_ids"]:
            _filing_opinion(obs, packet, decision)
            continue
        if obs["delivery"] == "confirmed":
            raise ValueError("Jev cannot replace a frozen confirmed observation.")
        review = obs["jev_review"] = {"state": "not_scored", "prior_delivery": obs["delivery"],
                                      "guard_reasons": list(packet["guard_reasons"])}
        if decision is None:
            continue
        choice = decision["choice"]
        review.update(choice=choice, confidence=decision["confidence"], job_id=decision.get("job_id"))
        if choice == "review":
            review["state"] = "review"
        elif choice == "reject":
            # If confirmation was unavailable because the source omits a qualification,
            # a closed-choice refusal leaves the sourced base property reportable.
            if (obs["delivery"] == "provisional" and obs["fit"]["reason"] == "field_qualifier_unverified"
                    and set(packet["guard_reasons"]) == {"unreported_field_qualification"}):
                review["state"] = "rejected_as_confirmed"
            else:
                review["state"] = "rejected"
                removed.add(oid)
            obs["eligible"] = False
        else:
            if choice not in packet["options"]:
                raise ValueError("Jev choice is absent from this observation's options.")
            endpoint = packet["options"][choice]
            _assert_supported_option(obs, packet, endpoint)
            review.update(state="selected", endpoint_id=endpoint["id"], evidence=deepcopy(endpoint["evidence"]),
                          prior_axes={k: obs[k] for k in ("direct", "target", "fit", "fit_blockers")})
            _accept(obs, endpoint)
            selected.add(oid)

    occurrences = defaultdict(list)
    for oid, obs in by_id.items():
        if oid in selected or obs["delivery"] == "confirmed":
            key = _measurement(obs)
            if key is not None:
                occurrences[key].append(oid)
    for ids in occurrences.values():
        new = [oid for oid in ids if oid in selected]
        if not new or len(ids) == 1:
            continue
        if len({by_id[oid]["direct"]["id"] for oid in ids}) > 1:
            # Two different direct subjects for this very occurrence need a scope decision.
            # Even a high model score cannot turn it into two independent measurements.
            for oid in new:
                original_obs = deepcopy(original[oid])
                original_obs["jev_review"] = by_id[oid]["jev_review"]
                original_obs["jev_review"].update(state="conflicting_assignment", competing_observations=ids)
                by_id[oid].update(original_obs)
                selected.remove(oid)
            continue
        canonical = next((oid for oid in ids if oid not in selected), new[0])
        for oid in new:
            if oid != canonical:
                duplicate[oid] = canonical
                by_id[oid]["jev_review"].update(state="duplicate", same_observation_as=canonical)
                by_id[oid]["eligible"] = False
                selected.remove(oid)

    # Reuse the ordinary row projection for new facts and composite identity fields.
    # Work on copies: the projector also assigns backlinks to identity witnesses.
    working = [deepcopy(o) for oid, o in by_id.items() if oid in selected or
               (o["field"] in identities and o["eligible"] and oid not in duplicate and oid not in removed)]
    for obs in working:
        obs.update(delivery="detail", row_ids=[])
    additions, proofs = _project(schema, working, identities, result["source_sha256"], result["field_policies"],
                                 result["subject_label_field"], {})
    additions = [(row, proof) for row, proof in zip(additions, proofs, strict=True)
                 if proof["delivery"] == "confirmed" and selected.intersection(proof["observations"])]
    delivered = {oid for _, proof in additions for oid in proof["observations"]}
    unprojected = selected - delivered
    for oid in unprojected:
        by_id[oid]["jev_review"]["state"] = "selected_without_row"
    for oid in unprojected.union(oid for oid, canonical in duplicate.items() if canonical in unprojected):
        # Preserve any pending baseline row, e.g. when a further identity dimension is unresolved.
        prior = original[oid]
        by_id[oid].update({k: deepcopy(prior[k]) for k in ("direct", "target", "fit", "fit_blockers", "eligible")})
        for key in ("delivery_reasons", "delivery_candidates", "projection_reason"):
            if key in prior:
                by_id[oid][key] = deepcopy(prior[key])

    removed.update(selected.intersection(delivered))
    removed.update(oid for oid, canonical in duplicate.items() if canonical in delivered
                   or original[canonical]["delivery"] == "confirmed")
    kept = [(row, proof) for row, proof in zip(result["rows"], result["lineage"]["rows"], strict=True)
            if proof["delivery"] == "confirmed" or not removed.intersection(
                oid for oid in proof["observations"] if original[oid]["delivery"] == "provisional")]
    groups = defaultdict(list)
    for row, proof in kept:
        if proof["delivery"] == "confirmed":
            groups[_group_key(proof, by_id, identities)].append((row, proof))
        elif any(by_id[oid].get("jev_review", {}).get("state") == "rejected_as_confirmed"
                 for oid in proof["observations"]):
            proof["status"] += "｜Jev 否决确认为目标字段，保留原文待定值"
    additions_per_group = Counter(_group_key(proof, by_id, identities) for _, proof in additions)
    for row, proof in additions:
        accepted = [oid for oid in proof["observations"] if oid in selected]
        proof.update(jev_observations=accepted, status="Jev 模型复核入表")
        key = _group_key(proof, by_id, identities)
        compatible = groups[key] if key is not None else []
        if len(compatible) == 1 and additions_per_group[key] == 1:
            base, base_proof = compatible[0]
            # Never associate two repeats by list position or overwrite a frozen cell.
            if all(base[f] is None or row[f] is None or (f in identities and row[f] == base[f]) for f in schema):
                for field in proof["cells"]:
                    if base[field] is None:
                        base[field], base_proof["cells"][field] = row[field], proof["cells"][field]
                    elif field in identities:
                        base_proof["cells"][field] = list(dict.fromkeys(base_proof["cells"][field] + proof["cells"][field]))
                base_proof["observations"] = list(dict.fromkeys(base_proof["observations"] + proof["observations"]))
                base_proof["context"] = list(dict.fromkeys(base_proof["context"] + proof["context"]))
                base_proof.setdefault("jev_observations", []).extend(accepted)
                base_proof["status"] = "已确认｜含 Jev 模型复核值"
                continue
        kept.append((row, proof))

    result["rows"] = [row for row, _ in kept]
    result["lineage"]["rows"] = [proof for _, proof in kept]
    for obs in by_id.values():
        obs.update(delivery="detail", row_ids=[])
    for _, proof in kept:
        for oid in proof["observations"]:
            obs = by_id[oid]
            obs["row_ids"].append(proof["row_id"])
            if "annotation_columns" in dataset and original[oid]["row_ids"]:
                obs["delivery"] = original[oid]["delivery"]
            elif proof["delivery"] == "confirmed" or obs["field"] in identities:
                obs["delivery"] = "confirmed"
            else:
                obs["delivery"] = "provisional"
    _share_confirmed_names(by_id, selected)
    if "statistic_columns" in result:
        set_statistic_columns(result)
    if "annotation_columns" in result:
        finalize_annotations(result)
    return result
