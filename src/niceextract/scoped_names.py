"""Observation-scoped semantic name decisions over immutable original text."""

from collections import defaultdict
import re

from .dataset_identity import literal_name
from .measurement_syntax import numeric_tokens
from .value_location import locate_value


_DENIAL = re.compile(r"\b(?:not|never|neither|denied|incorrectly)\b|并非|不是|否认|从未|未曾|没有|不称|误称", re.I)


def validate_name_receipt(obs, objects, evidence):
    """Validate the frozen decision's references before a view can consume it."""
    receipt = obs.get("name_resolution")
    if receipt is None:
        return
    if not isinstance(receipt, dict):
        raise ValueError("Invalid name resolution receipt.")
    def references(key, registry, limit, required=False):
        ids = receipt.get(key)
        return (isinstance(ids, list) and len(ids) <= limit and (bool(ids) or not required)
                and all(isinstance(v, str) and v in registry for v in ids) and len(set(ids)) == len(ids))

    relation = receipt.get("relation")
    target, owner = receipt.get("target_id"), receipt.get("source_id")
    if (receipt.get("observation_id") != obs["observation_id"] or owner != obs.get("subject_id")
            or (owner is not None and (not isinstance(owner, str) or owner not in objects))
            or not isinstance(target, str) or target not in objects or not isinstance(relation, str)
            or relation not in {"local_identity", "reusable_alias", "related", "different", "uncertain"}
            or not isinstance(receipt.get("reason"), str) or not receipt["reason"].strip()
            or receipt.get("semantic_status") != "model_proposed"
            or not references("competitor_ids", objects, 12) or target in receipt["competitor_ids"]
            or (relation in {"related", "different"} and owner == target)
            or not references("evidence_ids", evidence, 8, True)
            or not references("scope_evidence_ids", evidence, 6, relation == "reusable_alias")
            or not all(isinstance(receipt.get(k), str) and receipt[k] in evidence
                       for k in ("mention_id", "target_mention_id"))):
        raise ValueError("Invalid name resolution endpoints, evidence, scope or origin.")


def _contains(outer, inner):
    return outer["start"] <= inner["start"] and inner["end"] <= outer["end"]


def _exact_evidence(doc, ids):
    if not ids or any(key not in doc.evidence for key in ids):
        return []
    return [doc.evidence[key] for key in ids]


def _qualifier(name, member):
    """Split a proposed display label at a cited member, preserving every other word."""
    if name.count(member) != 1:
        return ""
    if name.endswith(member):
        return name[:-len(member)].strip()
    if name.startswith(member):
        return name[len(member):].strip()
    return ""


def _local(doc, item, mention, value_spans, path, extracted_owner_spans=()):
    """A table's owner path or a binding statement must connect this mention to this value."""
    if path is not None:
        owner = path.get("owner")
        return bool(owner and not path.get("gap") and _contains(owner, mention)
                    and owner["quote"].strip() == mention["quote"].strip())
    refs = [locate_value(doc.source, item.get("value"), s["start"], s["end"]) for s in value_spans]
    values = {(q["start"], q["end"]): q for q in refs if q}
    if len(values) != 1:
        return False
    value = next(iter(values.values()))
    sentence = doc.sentence(mention["start"], mention["end"])
    if not sentence or not _contains(sentence, value):
        return False
    citations = doc.entries(item, "binding_evidence_ids")
    if extracted_owner_spans and any(_contains(s, mention) for s in extracted_owner_spans):
        citations = [*extracted_owner_spans, sentence]
    prop = item.get("property_name") or item.get("record_type")
    for cite in citations:
        if not (_contains(cite, value) and _contains(cite, mention)):
            continue
        # A shorter binding may isolate one assignment in a multi-value sentence.
        # A fragment which just bridges an adjacent value and the next name cannot.
        if (cite["start"] > sentence["start"] or cite["end"] < sentence["end"]) and (
                not prop or prop.casefold() not in cite["quote"].casefold()):
            cite = sentence
        offset = max(cite["start"], sentence["start"])
        region = doc.source[offset:min(cite["end"], sentence["end"])]
        other_numbers = any(not any(q["start"] <= offset + a < offset + b <= q["end"]
                                    for q in (mention, value)) for a, b in numeric_tokens(region))
        if not other_numbers and (not prop or region.casefold().count(prop.casefold()) <= 1):
            return True
    return False


class ScopedNames:
    def __init__(self, doc, value_spans, structure, uncertain):
        self.records = {}
        self.aliases = defaultdict(list)
        self.spans = value_spans
        self.uncertain = uncertain
        self.headings = {h.span.start: (h.span.end, doc.scope(h.span.start)) for h in structure.headings}
        for item in doc.observations:
            name = item.get("name_resolution")
            if not name:
                continue
            oid = item["observation_id"]
            mention = doc.evidence.get(name.get("mention_id"))
            endpoint = doc.evidence.get(name.get("target_mention_id"))
            obj = doc.objects.get(name.get("target_id"))
            proof = _exact_evidence(doc, name.get("evidence_ids"))
            scopes = _exact_evidence(doc, name.get("scope_evidence_ids"))
            if (name.get("observation_id") != oid or name.get("source_id") != item.get("subject_id")
                    or not mention or not endpoint or not obj or not proof
                    or any(k not in doc.objects for k in name.get("competitor_ids", []))
                    or not any(_contains(s, endpoint) for s in doc.entries(obj, "mention_evidence_ids"))
                    or not any(_contains(s, endpoint) for s in proof)
                    or not any(_contains(s, mention) for s in proof)
                    or not any(s["quote"].strip() not in {mention["quote"].strip(), endpoint["quote"].strip()}
                               for s in proof)
                    or not _local(doc, item, mention, value_spans(doc.source, item), doc.paths.get(oid))):
                self.records[oid] = {"reason": "name_evidence_unresolved"}
                continue
            relation = name.get("relation")
            if relation in {"local_identity", "reusable_alias", "related"} and any(
                    _DENIAL.search((doc.sentence(s["start"], s["end"]) or s)["quote"]) for s in proof):
                self.records[oid] = {"reason": "name_evidence_unresolved"}
                continue
            if relation == "reusable_alias" and not scopes:
                self.records[oid] = {"reason": "name_evidence_unresolved"}
                continue
            record = {"source": item.get("subject_id"), "target": obj, "mention": mention, "endpoint": endpoint,
                      "proof": proof, "scopes": scopes, "relation": relation, "origin": oid,
                      "reason": name.get("reason"), "competitors": name.get("competitor_ids", [])}
            if relation == "reusable_alias" and not self._in_scope(doc, record, mention, doc.paths.get(oid)):
                self.records[oid] = {"reason": "name_evidence_unresolved"}
                continue
            self.records[oid] = record
            if scopes and relation in {"reusable_alias", "different", "related", "uncertain"}:
                self.aliases[mention["quote"]].append(record)

    def _in_scope(self, doc, record, mention, path):
        for span in record["scopes"]:
            if _contains(span, mention):
                return True
            if path and path.get("owner") and _contains(path["owner"], span) and _contains(path["owner"], mention):
                return True
            heading = self.headings.get(span["start"])
            if heading and not doc.source[span["end"]:heading[0]].strip() and span["end"] <= heading[0]:
                if doc.scope(mention["start"]) == heading[1]:
                    return True
        return False

    def _composed_name(self, doc, record):
        """Check a model-proposed composite against the actual cited division and its scope."""
        endpoint = record["endpoint"]
        part = endpoint["quote"]
        obj = record["target"]
        qualifier = _qualifier(obj["name"], part)
        if not qualifier:
            return [], set()
        # An original passage must connect the member to the literal qualifier. The
        # order in which the author discusses the members is immaterial.
        joint = [e for e in record["proof"] if _contains(e, endpoint)
                 and doc.scope(e["start"]) == doc.scope(e["end"] - 1)]
        for span in joint:
            match = re.search(literal_name(qualifier), span["quote"])
            if match:
                start = span["start"] + match.start()
                break
        else:
            return [], {obj["object_id"]}
        parent = {"start": start, "end": start + len(qualifier), "quote": qualifier}
        if not (parent["end"] <= endpoint["start"] or endpoint["end"] <= parent["start"]):
            return [], {obj["object_id"]}
        sentence = doc.sentence(endpoint["start"], endpoint["end"])
        context = [e["quote"] for e in joint] + ([sentence["quote"]] if sentence else [])
        rivals = {}
        for key, other in doc.objects.items():
            alternative = _qualifier(other["name"], part)
            if (other["is_table_subject"] and alternative and alternative != qualifier
                    and any(re.search(literal_name(alternative), c) for c in context)):
                rivals[key] = alternative
        if rivals:
            for e in record["scopes"]:
                if (e["start"] in self.headings and self._in_scope(doc, {"scopes": [e]}, record["mention"], None)
                        and re.search(literal_name(qualifier), e["quote"])
                        and not any(re.search(literal_name(alt), e["quote"]) for alt in rivals.values())):
                    return [parent, endpoint, e], set()
        return [parent, endpoint], set(rivals)

    def _isolated_assignment(self, doc, item, spans, record):
        """A parallel sentence can contain a self-contained, explicitly cited assignment."""
        if not record or record.get("relation") not in {"local_identity", "reusable_alias"}:
            return False
        mention = record["mention"]
        sentence = doc.sentence(mention["start"], mention["end"])
        prop = item.get("property_name")
        if not sentence or not prop or any(m[0].casefold() not in {"respectively", "分别"}
                for m in self.uncertain.finditer(sentence["quote"])):
            return False
        # A citation beginning halfway through a list of names can hide a competing owner.
        # Accept only the leading, fully written assignment and retain the other clauses for review.
        if doc.source[sentence["start"]:mention["start"]].strip():
            return False
        for cite in doc.entries(item, "binding_evidence_ids"):
            if not (_contains(sentence, cite) and _contains(cite, mention) and cite["end"] < sentence["end"]):
                continue
            value = locate_value(doc.source, item.get("value"), cite["start"], cite["end"])
            if not value or value["start"] < mention["end"] or not any(_contains(s, value) for s in spans):
                continue
            # All words between the two source anchors must belong to this one property.
            # A second named property or a negated predicate makes the shortened citation inconclusive.
            between = doc.source[mention["end"]:value["start"]].strip()
            if between.casefold().startswith(prop.casefold()) and between[len(prop):].strip() in {"", "=", ":", "："}:
                return True
        return False

    def resolve(self, doc, item, spans, path, direct):
        oid = item["observation_id"]
        record = self.records.get(oid)
        if (direct["reason"] in {"conflicting_scoped_definitions", "multiple_source_statements"}
                or (direct["reason"] == "qualified_or_competing_statement" and
                    not self._isolated_assignment(doc, item, spans, record))):
            # Resolving a name cannot validate a negated or contested measurement.
            return direct, []
        if record and "relation" not in record:
            return {**direct, "reason": record["reason"], "basis": "scoped_name", "name_status": "unresolved"}, []
        mention = record["mention"] if record else None
        matches = []
        ordinary = item.get("binding_status") == "as_extracted" and item.get("name_handling") == "as_reported"
        if mention:
            matches = [r for r in self.aliases[mention["quote"]] if self._in_scope(doc, r, mention, path)]
        elif ((item.get("binding_status") == "supported" or ordinary) and isinstance(item.get("entity"), str)
              and item["entity"] in self.aliases):
            # Reuse requires an independently bound occurrence. Locate the exact owner in its own
            # original statement or owner cell; the registry is never searched for a nearby value.
            alias = item["entity"]
            extracted = self.spans(doc.source, item, "entity") if ordinary else []
            regions = ([path["owner"]] if path and path.get("owner") else
                       extracted or doc.entries(item, "binding_evidence_ids"))
            disputed_assignment = False
            for region in regions:
                occurrences = list(re.finditer(literal_name(alias), region["quote"]))
                if len(occurrences) != 1:
                    continue
                start = region["start"] + occurrences[0].start()
                ref = {"start": start, "end": start + len(alias), "quote": alias}
                applicable = [r for r in self.aliases[alias] if self._in_scope(doc, r, ref, path)]
                if not applicable:
                    continue
                if _local(doc, item, ref, spans, path, extracted):
                    mention = ref
                    matches = applicable
                    break
                disputed_assignment = True
            if not matches and disputed_assignment:
                return {**direct, "reason": "name_evidence_unresolved", "basis": "scoped_name",
                        "name_status": "unresolved"}, []
        aliases = [r for r in matches if r["relation"] == "reusable_alias"]
        if not record and not aliases:
            return direct, []
        chosen = record or aliases[0]
        relation = chosen["relation"]
        target_id = chosen["target"]["object_id"]
        target_ids = {r["target"]["object_id"] for r in aliases}
        veto = any(r["relation"] in {"different", "related", "uncertain"} and
                   r["target"]["object_id"] == target_id for r in matches)
        source_id = item.get("subject_id")
        contradiction = (target_id in doc.disputed or source_id in doc.disputed or
                         (source_id != target_id and frozenset((source_id, target_id)) in doc.conflicts))
        ranked = [chosen, *matches]
        proof = [mention, *spans, *chosen["proof"], *chosen["scopes"]]
        obj = chosen["target"]
        label = obj["name"] if any(obj["name"] in q["quote"] for q in proof) else chosen["endpoint"]["quote"]
        components, other_targets = [], set()
        if relation in {"local_identity", "reusable_alias"} and label != obj["name"]:
            components, other_targets = self._composed_name(doc, chosen)
            if components:
                label = obj["name"]
        conflict = (other_targets or any(r["competitors"] for r in ranked)
                    or (relation in {"local_identity", "reusable_alias"} and
                        (contradiction or veto or bool(target_ids - {target_id}))))
        decision = {"id": target_id, "label": label, "scope": doc.scope(mention["start"]),
                    "origin_observation_id": chosen["origin"], "reason": relation, "basis": "scoped_name",
                    "local_mention": mention, "evidence": proof, "status": "supported",
                    "semantic_status": "model_proposed"}
        if conflict or relation == "uncertain":
            # The assignment to this original occurrence can survive a disagreement over its
            # registry identity. Keep competing endpoints out of the confirmed subject and rows.
            local = (item.get("binding_status") == "supported" or ordinary or direct["status"] == "supported")
            alternatives = other_targets | {r["target"]["object_id"] for r in ranked}
            alternatives.update(key for r in ranked for key in r["competitors"])
            return {**decision, "id": doc.local_id("name_occurrence", [mention["start"], mention["end"]]) if local else None,
                    "label": mention["quote"], "status": "supported" if local else "unknown",
                    "name_status": "unresolved", "reason": "competing_name_interpretations",
                    "name_candidates": [{"status": "unknown", "id": k, "label": doc.objects[k]["name"],
                                         "scope": doc.objects[k]["scope"], "reason": "competing_name_interpretations",
                                         "basis": "scoped_name", "evidence": doc.entries(doc.objects[k], "mention_evidence_ids")}
                                        for k in sorted(alternatives)]}, []
        if relation in {"different", "related"}:
            if direct["status"] != "supported" and source_id and item.get("binding_status") == "supported":
                src = doc.objects.get(source_id)
                if src and any(_contains(s, mention) for s in doc.entries(src, "mention_evidence_ids")):
                    direct = {**decision, "id": source_id, "label": mention["quote"], "reason": "local_direct_mention"}
            return direct, [{**decision, "source_id": source_id, "target_id": target_id,
                             "status": "contradicted" if relation == "different" else "supported"}]
        if relation not in {"local_identity", "reusable_alias"} or not (item.get("binding_status") == "supported" or ordinary):
            return direct, []
        if not record and source_id not in {chosen["source"], target_id} and source_id is not None:
            return direct, []
        decision["alias_names"] = [mention["quote"]] if mention["quote"] != label else []
        if components:
            decision["identity_evidence"] = components
        decision["original_subject_id"] = source_id
        return decision, []
