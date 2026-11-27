"""Follow only evidenced containment between identified objects when browsing a dataset."""

from collections import defaultdict, deque
import re

from .dataset_annotations import filing_evidence
from .dataset_identity import literal_name


def _matches(endpoint, subject, match):
    names = [s for s in (endpoint.get("label"), *endpoint.get("alias_names", [])) if s]
    if match == "exact":
        return endpoint.get("id") == subject or subject in names
    return any(subject in name for name in names)


class SubjectHierarchy:
    def __init__(self, dataset, subject, match):
        self.subject, self.match = subject, match
        graph = dataset["lineage"]
        citations = {s["evidence_id"]: s for s in graph.get("evidence", [])}
        objects = {o["object_id"]: {"id": o["object_id"], "label": o["name"], "scope": o.get("scope")}
                   for o in graph.get("objects", [])}
        parents = defaultdict(list)
        occurrences = defaultdict(set)
        endpoints = dict(objects)

        def register(endpoint, proof):
            key, label = endpoint.get("id"), endpoint.get("label")
            if not key or not label:
                return
            endpoints[key] = endpoint
            for span in proof:
                # A shared exact occurrence can join local IDs across observations.
                # Repeated spellings elsewhere in a document are different evidence.
                hits = list(re.finditer(literal_name(label), span["quote"]))
                if len(hits) == 1:
                    occurrences[label, span["start"] + hits[0].start()].add(key)

        def connect(child, parent, why, proof):
            if child.get("id") and parent.get("id") and child["id"] != parent["id"] and proof:
                parents[parent["id"]].append((child["id"], {"child": child["label"], "parent": parent["label"],
                                                             "explanation": why, "evidence": proof}))

        for edge in graph.get("relations", []):
            if edge.get("predicate") in {"part_of", "member_of"}:
                proof = [citations[k] for k in edge.get("evidence_ids", []) if k in citations]
                child, parent = objects.get(edge["source_id"]), objects.get(edge["target_id"])
                if child and parent:
                    connect(child, parent, edge["predicate"], proof)
        for obs in dataset["observations"]:
            direct = obs["direct"]
            register(direct, filing_evidence(direct, citations))
            choices = obs.get("filing_subjects", [])
            if (len(choices) != 1 or obs.get("filing_disagreement") or obs.get("row_relation_conflict")
                    or obs.get("row_relation_issues")):
                continue
            target = choices[0]
            if target.get("status") != "supported":
                continue
            register(target, filing_evidence(target, citations))
            proof = filing_evidence({"relation_evidence": target.get("relation_evidence", [])}, citations)
            if target.get("target_contains_direct") is True and proof:
                register(direct, proof)
                connect(direct, target, target["explanation"], proof)

        # Two distinct registry objects can overlap in the original. Such an
        # overlap cannot justify joining their local mentions through this index.
        aliases = defaultdict(set)
        for keys in occurrences.values():
            if len(keys & objects.keys()) > 1:
                continue
            scopes = {endpoints[k].get("scope") for k in keys if endpoints[k].get("scope")}
            if len(scopes) <= 1:
                for key in keys:
                    aliases[key].update(keys - {key})
        self.paths = {}
        queue = deque((key, []) for key, ep in endpoints.items() if _matches(ep, subject, match))
        while queue:
            key, path = queue.popleft()
            if key in self.paths:
                continue
            self.paths[key] = path
            queue.extendleft((alias, path) for alias in aliases[key] if alias not in self.paths)
            queue.extend((child, [*path, step]) for child, step in parents[key] if child not in self.paths)

    def classify(self, obs):
        direct = obs["direct"]
        if _matches(direct, self.subject, self.match):
            if direct.get("reason") == "value_owner_conflict":
                return "pending", []
            return "self", []
        path = self.paths.get(direct.get("id"))
        if path:
            return "descendant", path
        if path == []:
            return "self", []
        choices = obs.get("filing_subjects", [obs["target"]])
        for ep in choices:
            if _matches(ep, self.subject, self.match):
                pending = (len(choices) != 1 or obs.get("filing_disagreement") or ep.get("status") == "unknown"
                           or ep.get("explanation") and not ep.get("relation_evidence"))
                status = "pending" if pending else "associated"
                return status, [{"child": direct.get("label"), "parent": ep.get("label"),
                                 "explanation": ep.get("explanation") or ep.get("reason"),
                                 "evidence": ep.get("relation_evidence", [])}]
        return None, []
