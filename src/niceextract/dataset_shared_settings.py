"""File one table setting under the explicitly enumerated users of that setting."""

from collections import defaultdict
import re

from .dataset_resolution_table import table_type_lists


_SETTING_TABLE = re.compile(
    r"(?=.*(?:\b(?:model|simulation)\b|模型|模拟))(?=.*(?:\bparameters?\b|参数))|"
    r"\b(?:process|protocol|shared|operating) settings\b|工艺设定|工艺参数", re.I)
_MODEL_CAPTION = re.compile(r"\b(?:model|simulation)\b|模型|模拟|仿真", re.I)
_OTHER_TABLE_USE = re.compile(r"\b(?:measured|measurements|observed|thresholds?|sensitivity|classification|"
                              r"outputs?|limits?|averages?|means?|ranges?)\b|输出|实测|分类|阈值|敏感性|取值范围|平均值", re.I)
_SEP = re.compile(r"\s*(?:[,，、]|\band\b|和|及|与)\s*", re.I)
_LINK = re.compile(r"\s*(?:为|均为|采用|均采用|使用|均使用|are |use |used |share )\s*", re.I)
_EXCEPTION = re.compile(r"\b(?:except|unless|override\w*|instead|not|exclud(?:es?|ed|ing)|if|might|may|hypothetically)\b|"
                        r"除外|例外|排除|另设|不采用|并非|若|可能|除[^，。]{1,40}外", re.I)


def _listed_labels(group, registered):
    """Expand a compact numbered list only when *every* spelling has a bound identity."""
    labels = [s for s in _SEP.split(group) if s]
    if len(labels) < 2 or len(labels) != len(set(labels)):
        return []
    if not all(s in registered for s in labels):
        first, last = re.fullmatch(r"(.*?)(\d+)", labels[0]), re.fullmatch(r"(\d+)([^\d]*)", labels[-1])
        if not first or not last or not all(v.isdecimal() for v in labels[1:-1]):
            return []
        labels = [first[1] + n + last[2] for n in [first[2], *labels[1:-1], last[1]]]
    return labels if len(set(labels)) == len(labels) and all(s in registered for s in labels) else []


def _member_groups(doc, spans, model_rows):
    parents = defaultdict(list)
    for edge in doc.analysis.get("relations", []):
        if edge["predicate"] in {"part_of", "member_of"} and edge["target_id"] in doc.objects:
            parents[edge["source_id"]].append(edge)
    # Each named member must have a grounded identity and the same registered parent.
    # A registry name containing a number is insufficient; its binding and the edge
    # must both cite this group's actual assignment.
    by_citation = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    for item in doc.observations:
        member = doc.objects.get(item.get("subject_id"))
        label = item.get("entity")
        if (not member or not label or member["object_id"] in doc.disputed
                or label != item.get("value") or item.get("record_type") not in doc.identity_fields
                or item.get("binding_status") != "supported" or item.get("target_match") != "supported"
                or not (model_rows or member["is_table_subject"]) or item.get("blockers")):
            continue
        for citation in spans(doc.source, item):
            left, right = citation["start"], citation["end"]
            bindings = doc.entries(item, "binding_evidence_ids")
            if not any(b["start"] <= left and right <= b["end"] for b in bindings):
                continue
            named = member["name"]
            rendered = re.split(r"[（(]", named, maxsplit=1)[0].strip()
            for edge in parents[member["object_id"]]:
                proofs = doc.entries(edge, "evidence_ids")
                if any(p["start"] <= left and right <= p["end"] for p in proofs):
                    by_citation[(left, right)][edge["target_id"]][label][member["object_id"]] = {
                        "id": member["object_id"], "label": label, "status": "supported",
                        "alias_names": sorted({rendered, named} - {label}), "evidence": bindings,
                        "relation_id": edge["relation_id"]}
    groups = []
    for obs in doc.observations:
        group, category = obs.get("entity"), obs.get("value")
        if (not isinstance(category, str) or not group or obs.get("target_match") != "supported"
                or obs.get("source_kind") == "table" or obs["record_type"] in doc.identity_fields
                or obs.get("blockers") or obs.get("name_handling") not in {None, "as_reported"}):
            continue
        for proof in spans(doc.source, obs):
            text = proof["quote"].strip().rstrip("。.;；")
            if text.count(group) != 1 or _EXCEPTION.search(text):
                continue
            remainder = text.partition(group)[2]
            link = _LINK.match(remainder)
            if not link or remainder[link.end():] != category:
                continue
            sentence = doc.sentence(proof["start"], proof["end"])
            if not sentence or _EXCEPTION.search(sentence["quote"]):
                continue
            for parent, members in by_citation.get((proof["start"], proof["end"]), {}).items():
                labels = _listed_labels(group, members)
                if labels and all(len(members[label]) == 1 for label in labels):
                    groups.append((group, category, proof, parent,
                                   [next(iter(members[label].values())) for label in labels]))
    return groups


def apply_shared_settings(doc, resolved, spans, model_rows, allows_settings, table_reference):
    groups = _member_groups(doc, spans, model_rows)
    if not groups:
        return
    inventories = table_type_lists(doc.source, doc.observations)
    attributed = defaultdict(set)
    for other in doc.observations:
        attributed[other["record_type"]].update((other.get("subject_id"), other.get("entity")))
    for item in doc.observations:
        entry = resolved[item["observation_id"]]
        path = entry.get("table_context") or {}
        if (not path.get("owner") or path.get("gap") or path.get("qualifier_gap") or path.get("scope") != "individual"
                or "table_start" not in path or entry["direct"]["status"] != "supported"
                or entry["fit"]["status"] != "supported" or entry.get("row_relation_conflict")
                or item.get("row_relations") or item.get("blockers") or item.get("target_match") != "supported"
                or entry["statistic_kind"] != "value" or entry["measurement_use"] not in {"unspecified", "model_input"}
                or any(_OTHER_TABLE_USE.search(h["quote"]) for h in path.get("headers", []))
                or not allows_settings(doc.analysis["task"]["fields"][item["record_type"]])):
            continue
        owner = path["owner"]["quote"].strip()
        if owner != item["entity"]:
            continue
        table = path["owner"]["ref"].rsplit("-r", 1)[0]
        inventory = inventories.get((table, owner))
        matches = {}
        for group, category, proof, parent, members in groups:
            # When the category is qualified, its leading material must also be
            # explicitly enumerated as a parameter-table type in this paragraph.
            if owner != category and not (inventory and category.startswith(owner)
                    and "\n" not in doc.source[proof["start"]:inventory["start"]]
                    and proof["end"] <= inventory["start"] and not _SEP.search(category)
                    and not re.search(r"-free\b|without|or\b|除外|或|不足|缺失", category, re.I)):
                continue
            references = table_reference(doc.source, path["table_start"], proof, _SETTING_TABLE)
            if not references or _OTHER_TABLE_USE.search(references[-1]["quote"]):
                continue
            reference = doc.sentence(references[0]["start"], references[0]["end"])
            if not reference:
                continue
            # A member exception can follow the table reference, including after
            # a Markdown soft line break. Stop at the next paragraph or heading.
            following = doc.source[proof["end"]:references[-1]["start"]]
            paragraph = re.split(r"\r?\n[ \t]*(?:\r?\n|#{1,6}[ \t])", following, maxsplit=1)[0]
            if _EXCEPTION.search(paragraph):
                continue
            # An individually attributed value may override this common setting.
            ids = {m["id"] for m in members}
            labels = {name for m in members for name in (m["label"], *m["alias_names"])}
            if (attributed[item["record_type"]].intersection(ids | labels)
                    or any(re.search(re.escape(label) + r"\s*(?:use[sd]?\b|was\b|is\b|采用|使用|设为|为)", paragraph, re.I)
                           for label in labels)):
                continue
            evidence = [proof, *references, reference, path["owner"], *path["headers"], path["value"]]
            if owner != category:
                evidence.append(inventory)
            key = doc.local_id("shared-setting-members", [parent, sorted(ids)])
            use = "model_input" if _MODEL_CAPTION.search(references[-1]["quote"]) else "process_setting"
            decision = {"status": "supported", "id": key, "label": group,
                        "scope": doc.objects[parent]["scope"], "basis": "source_member_assignment",
                        "membership_basis": "source_identity", "reason": "shared_setting_members",
                        "members": members, "evidence": evidence}
            # Multiple extractions of the same assignment are one piece of
            # support, not distinct interpretations of the table cell.
            if key in matches:
                prior = matches[key][0]["evidence"]
                prior.extend(e for e in evidence if e not in prior)
            else:
                matches[key] = (decision, use)
        if len(matches) == 1:
            target, use = next(iter(matches.values()))
            entry.update(target=target, associated_row=True, purpose=use, measurement_use=use)
