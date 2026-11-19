"""Document-local subject resolution over frozen extraction and verification records."""

from bisect import bisect_right
from collections import defaultdict
import re

from .chunking import _protected_positions, _sentence_ends
from .dataset_field_fit import checked_field_fit
from .dataset_identity import competing_targets, record_names, source_bound_name
from .dataset_resolution_table import TablePaths
from .execution import digest
from .normalization import normalized_view
from .structure import build_structure


_UNCERTAIN = re.compile(
    r"\b(?:not|no|neither|never|unless|except|if|instead|respectively|former|latter|might|possibly|according to|"
    r"cited|reported by|versus|compared with)\b|并非|不是|不为|未测|未达到|前者|后者|分别|据报道|假设|[?？“”\"]", re.I)
_PURPOSES = (
    ("classification_threshold", re.compile(r"\b(?:classification thresholds?|cutoffs?|class limits?)\b|分类阈值|分类界限|分类标准", re.I)),
    ("model_input", re.compile(r"\b(?:model input|assumed value|input parameter)\b|模型输入|设定值|公式取值", re.I)),
    ("aggregate", re.compile(r"\b(?:grand total|subtotal|pooled mean)\b|小计|合计|总体均值", re.I)),
    ("reported_mean", re.compile(r"\b(?:average|mean)\b|平均|均值", re.I)),
)


def _decision(status="unknown", *, key=None, label=None, evidence=(), reason="", scope=None, basis=None):
    return {"status": status, "id": key, "label": label, "evidence": list(evidence),
            "reason": reason, "scope": scope, "basis": basis}


def _unique(entries):
    return list({(e["start"], e["end"]): e for e in entries}.values())


def _spans(source, observation):
    values = []
    for entry in observation.get("source_evidence", []):
        if "value" not in entry.get("fields", []) or not isinstance(entry.get("span"), dict):
            continue
        if len(entry.get("matches", [])) > 1 or len(entry.get("source_matches", [])) > 1:
            continue
        left, right = entry["span"].get("start"), entry["span"].get("end")
        if type(left) is int and type(right) is int and 0 <= left < right <= len(source):
            original = source[left:right]
            if (original == entry.get("quote") or
                    any(m.get("source_quote") == original and m.get("span") == entry["span"]
                        for m in entry.get("source_matches", []))):
                values.append({"quote": original, "start": left, "end": right})
    return _unique(values)


def _literal(name):
    # Latin identifiers must be whole tokens. Keep Chinese adjacency and scientific notation literal.
    left = r"(?<![a-zA-Z0-9_])" if re.match(r"[a-zA-Z0-9_]", name) else ""
    right = r"(?![a-zA-Z0-9_])" if re.search(r"[a-zA-Z0-9_]$", name) else ""
    return left + re.escape(name) + right


class _Document:
    def __init__(self, source, result, analysis, identity_fields):
        self.source = source
        self.analysis = analysis
        structure = build_structure(source)
        self.source_hash = structure.text_sha256
        self.headings = [h.span.start for h in structure.headings]
        protected, blocked = _protected_positions(source)
        self.boundaries = sorted({0, *_sentence_ends(source, 0, len(source), protected, blocked),
                                  *(m.end() for m in re.finditer(r"\n", source) if not protected[m.start()])})
        self.evidence = {e["evidence_id"]: e for e in analysis.get("evidence", [])
                         if type(e.get("start")) is int and type(e.get("end")) is int
                         and 0 <= e["start"] < e["end"] <= len(source) and source[e["start"]:e["end"]] == e["quote"]}
        self.objects = {o["object_id"]: o for o in analysis.get("objects", [])
                        if any(e in self.evidence for e in o.get("mention_evidence_ids", []))}
        self.names = defaultdict(list)
        for obj in self.objects.values():
            self.names[obj["name"]].append(obj)
        self.search_names = {name: normalized_view(name).text.casefold() for name in self.names}
        self.observations = analysis.get("observations", [])
        self.identity_fields = identity_fields
        self.identities = defaultdict(list)
        self.labels = set(self.names)
        for o in self.observations:
            if isinstance(o.get("entity"), str) and o["entity"].strip():
                self.labels.add(o["entity"].strip())
            if o.get("record_type") in identity_fields and isinstance(o.get("value"), str) and not o.get("blockers"):
                label = o["value"].strip()
                matches = [e for e in _spans(source, o) if label and re.search(_literal(label), e["quote"])]
                if matches and o.get("target_match") != "unsupported":
                    self.identities[label].extend(matches)
                    self.labels.add(label)
        self.aliases = self._definitions()
        self.conflicts = {frozenset((e["source_id"], e["target_id"])) for e in analysis.get("relations", [])
                          if e["predicate"] == "different_from"}
        for edge in analysis.get("relations", []):
            if edge["predicate"] in {"part_of", "member_of", "sample_of"} and _qualified_relation(self, edge):
                self.conflicts.add(frozenset((edge["source_id"], edge["target_id"])))
        self.canonical, self.disputed = self._equivalence()
        self.name_records = record_names(self, _spans)
        self._table_paths(result, TablePaths(source, structure))

    def scope(self, position):
        index = bisect_right(self.headings, position)
        return f"section@{self.headings[index - 1]}" if index else "document"

    def sentence(self, start, end):
        idx = bisect_right(self.boundaries, start) - 1
        left = self.boundaries[idx]
        right = self.boundaries[min(idx + 1, len(self.boundaries) - 1)]
        if end <= right:
            return {"start": left, "end": right, "quote": self.source[left:right]}
        return None

    def _definitions(self):
        known = sorted((label for label in self.labels if label and len(label) <= 500), key=lambda x: (-len(x), x))
        lookup = defaultdict(list)
        if not known:
            return lookup
        names = "(?:" + "|".join(_literal(label) for label in known) + ")"
        marker = r"(?i:hereafter(?: called)?|also called|abbreviated as|identifier|编号(?:为)?|简称(?:为)?|以下简称|下称)"
        expression = re.compile(f"(?P<full>{names})[ \\t]*[(（,，][ \\t]*{marker}[ \\t:：]*(?P<short>{names})"
                                r"(?=[ \t]*[)）,，.。;；\n]|$)")
        for match in expression.finditer(self.source):
            full, short = match["full"], match["short"]
            sentence = self.sentence(*match.span())
            if full == short or not sentence or _UNCERTAIN.search(sentence["quote"]):
                continue
            evidence = {"quote": match[0], "start": match.start(), "end": match.end()}
            scope = self.scope(match.start())
            lookup[(scope, full)].append((full, evidence))
            lookup[(scope, short)].append((full, evidence))
        return lookup

    def _table_paths(self, result, tables):
        pointers = {}
        for i, table in enumerate(result.get("table_results", [])):
            for j, raw in enumerate(table.get("candidates", [])):
                pointers[f"/table_results/{i}/candidates/{j}"] = (raw, table["table_id"])
        paths, identity_cells = {}, defaultdict(set)
        self.identity_roles = defaultdict(list)
        for o in self.observations:
            pointer = o.get("source_record")
            if pointer not in pointers:
                continue
            raw, table_id = pointers[pointer]
            path = tables.path(o, raw, table_id)
            paths[o["observation_id"]] = path
            if (path.get("axis") == "identity" and o["record_type"] in self.identity_fields
                    and str(o["value"]).strip() == path["owner"]["quote"].strip()
                    and not o.get("blockers") and o.get("target_match") != "unsupported"):
                identity_cells[table_id].add(path["owner"]["ref"])
                role = path.get("owner_role")
                if role and role["quote"].strip():
                    self.identity_roles[role["quote"].strip()].extend([role, path["owner"]])
        self.anchor_owners = defaultdict(set)
        self.anchor_membership = defaultdict(set)
        self.anchor_proofs = defaultdict(list)
        self.target_cells = set().union(*identity_cells.values())
        for o in self.observations:
            path = paths.get(o["observation_id"])
            if path is None:
                continue
            if path.get("gap") == "owner_cell_unresolved" and not o.get("entity"):
                raw, table_id = pointers[o["source_record"]]
                path = tables.path(o, raw, table_id, identity_cells[table_id])
                paths[o["observation_id"]] = path
            obj = self.objects.get(o.get("subject_id"))
            if "owner" in path and obj and o.get("binding_status") == "supported" and not o.get("blockers"):
                owner = path["owner"]
                citations = self.entries(o, "binding_evidence_ids")
                if citations and obj["name"].strip() == owner["quote"].strip():
                    self.anchor_owners[owner["ref"]].add(obj["object_id"])
                if any(b["start"] < owner["end"] and owner["start"] < b["end"]
                       and owner["quote"].strip() in b["quote"] for b in citations):
                    self.anchor_membership[owner["ref"]].add(obj["object_id"])
                    self.anchor_proofs[owner["ref"]].extend(citations + self.entries(obj, "mention_evidence_ids"))
        self.paths = paths

    def entries(self, item, key):
        return [self.evidence[e] for e in item.get(key, []) if e in self.evidence]

    def local_id(self, namespace, anchor):
        return "subject-" + digest([self.source_hash, namespace, anchor])[:24]

    def same_section_objects(self, name, scope):
        return [obj for obj in self.names.get(name, [])
                if any(self.scope(e["start"]) == scope for e in self.entries(obj, "mention_evidence_ids"))]

    def _equivalence(self):
        # Compute the complete component before admitting any edge. A different_from
        # anywhere inside suspends the whole component, regardless of proposal order.
        graph = defaultdict(list)
        for edge in self.analysis.get("relations", []):
            if edge["predicate"] == "same_as" and (proof := _qualified_relation(self, edge)):
                graph[edge["source_id"]].append((edge["target_id"], proof))
                graph[edge["target_id"]].append((edge["source_id"], proof))
        visited, disputed, lookup = set(), set(), {}
        for key in graph:
            if key in visited:
                continue
            stack, members, evidence = [key], set(), []
            while stack:
                node = stack.pop()
                if node in members:
                    continue
                members.add(node)
                for other, quotes in graph[node]:
                    evidence.extend(quotes)
                    stack.append(other)
            visited.update(members)
            scopes = {self.scope(e["start"]) for e in evidence}
            if len(scopes) != 1 or any(pair <= members for pair in self.conflicts):
                disputed.update(members)
                continue
            primary = min(members, key=lambda k: (not self.objects[k].get("is_table_subject"), k))
            for member in members:
                lookup[member] = primary, _unique(evidence), next(iter(scopes))
        return lookup, disputed


def _local_assertion(doc, item, sentence):
    """Recognize a complete one-owner assignment. Arbitrary intervening text is never a connector."""
    if not sentence or _UNCERTAIN.search(sentence["quote"]):
        return None
    value = item.get("value")
    if value is None:
        return None
    prop = item.get("property_name") or item["record_type"]
    entity = item.get("entity")
    names = [entity.strip()] if isinstance(entity, str) and entity.strip() else sorted(doc.labels)
    unit = str(item.get("unit") or "")
    suffix = r"\s*" + re.escape(unit) + r"\s*[.。]?\s*"
    for name in names:
        label = _literal(name)
        fact = (rf"(?:\s*的\s*|\s*[:：]\s*|\s*['’]s\s+|\s+)({re.escape(prop)})"
                r"\s*(?:=|:|：|为|是|is|was|measured at)\s*")
        if re.fullmatch(r"\s*" + label + fact + re.escape(str(value)) + suffix, sentence["quote"]):
            return name
        if (item["record_type"] in doc.identity_fields and item.get("entity") == value == name
                and sentence["quote"].strip().rstrip(".。") == name):
            return name
    return None


def _coordinated_assertion(doc, item, spans):
    """Two enumerated attributes of an explicit subject, with a separately located value clause."""
    name = item.get("entity")
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()
    labels = [s for s in (item.get("conditions"), item.get("property_name")) if isinstance(s, str) and s.strip()]
    if not labels:
        return None
    attribute = "(?:" + "|".join(re.escape(s.strip()) for s in labels) + ")"
    terminal = attribute + r"\s*(?:=|:|：|为|is|was)?\s*" + re.escape(str(item.get("value"))) + r"\s*" + re.escape(str(item.get("unit") or ""))
    # A compact first attribute with a numeric or delimited mathematical quantity;
    # an arbitrary preceding clause could introduce a new subject.
    first = r"[\w -]{1,32}?\s*(?:=|:|：|为)?\s*(?:\$[^$\r\n]+\$|[-+]?\d+(?:\.\d+)?\s*[%a-zA-Z]{0,8})"
    for ref in item.get("source_evidence", []):
        if "entity" not in ref.get("fields", []) or len(ref.get("matches", [])) > 1:
            continue
        bounds = ref.get("span", {})
        l, r = bounds.get("start"), bounds.get("end")
        if type(l) is not int or type(r) is not int or not 0 <= l < r <= len(doc.source):
            continue
        quote = doc.source[l:r]
        if quote != ref.get("quote") or not re.fullmatch(_literal(name) + first + r"\s*[,，]\s*" + terminal, quote):
            continue
        sentence = doc.sentence(l, r)
        if (not sentence or _UNCERTAIN.search(sentence["quote"])
                or sentence["quote"][:l - sentence["start"]].strip(" \t\r\n,，。:：;；")
                or re.search(r"的|属于|包含|内含|关联|关联于|与|和|\b(?:and|has|with|contains|of)\b", quote[len(name):], re.I)):
            continue
        if any(n != name and n not in name and re.search(_literal(n), quote[len(name):]) for n in doc.labels):
            continue
        if any(l <= e["start"] and e["end"] == r and re.fullmatch(terminal, e["quote"]) for e in spans):
            return _decision("supported", key=doc.local_id("statement", [l, name]), label=name,
                             basis="local_statement", evidence=[{"start": l, "end": r, "quote": quote}],
                             scope=doc.scope(l), reason="scoped_attribute_list")
    return None


def _subjects(doc, item, spans, path):
    obj = doc.objects.get(item.get("subject_id"))
    bindings = doc.entries(item, "binding_evidence_ids")
    unknown = _decision(reason="direct_owner_unresolved")
    if not spans:
        return unknown
    if path is not None:
        if "owner" not in path:
            return _decision(reason=path["gap"], evidence=[path["value"]] if "value" in path else [])
        owner = path["owner"]
        key = owner["ref"]
        ids = doc.anchor_owners[key]
        return _decision("supported", key=next(iter(ids)) if len(ids) == 1 else doc.local_id("cell", key),
                         label=owner["quote"].strip(), scope=key, basis="table_anchor",
                         evidence=_unique([owner, path["value"]]), reason="verified_local_cell_path")
    sentences = [doc.sentence(e["start"], e["end"]) for e in spans]
    if any(s and _UNCERTAIN.search(s["quote"]) for s in sentences):
        return _decision(reason="qualified_or_competing_statement", evidence=spans)
    if name := source_bound_name(doc, item, spans, obj, bindings, _UNCERTAIN):
        return name
    local = [(name, sent) for sent in sentences if (name := _local_assertion(doc, item, sent))]
    scope = doc.scope(spans[0]["start"])
    if local:
        name, sentence = local[0]
        if any((n, s["start"]) != (name, sentence["start"]) for n, s in local):
            return _decision(reason="multiple_source_statements", evidence=spans)
        definitions = doc.aliases[(scope, name)]
        names = {full for full, _ in definitions}
        if len(names) > 1:
            return _decision(reason="conflicting_scoped_definitions", scope=scope,
                             evidence=[e for _, e in definitions])
        canonical = next(iter(names)) if names else name
        explicit = [e for _, e in definitions]
        possible = doc.same_section_objects(canonical, scope)
        if definitions:
            possible = [o for o in possible if any(m["start"] < e["end"] and e["start"] < m["end"]
                        for m in doc.entries(o, "mention_evidence_ids") for e in explicit)]
            alternative_ids = {o["object_id"] for n in {canonical, name} for o in doc.same_section_objects(n, scope)}
            if len(possible) > 1 or any(pair <= alternative_ids for pair in doc.conflicts):
                return _decision(reason="conflicting_scoped_definitions", scope=scope, evidence=explicit)
            key = possible[0]["object_id"] if possible else doc.local_id("definition", [scope, canonical])
        elif (obj and obj["name"] == canonical and item.get("binding_status") == "supported"
              and obj in possible and any(b["start"] < sentence["end"] and sentence["start"] < b["end"]
                                          for b in bindings)):
            key = obj["object_id"]
        else:
            # Without an explicit cross-mention link the identity remains local to this occurrence.
            key = doc.local_id("statement", [sentence["start"], canonical])
        if obj and bindings and obj["name"] not in {name, canonical}:
            return _decision(reason="competing_bound_owner", scope=scope, evidence=_unique(bindings + [sentence]))
        return _decision("supported", key=key, label=canonical, scope=scope, evidence=_unique([sentence] + explicit),
                         basis="explicit_definition" if explicit else "local_statement", reason="explicit_subject_assignment")
    coordinated = _coordinated_assertion(doc, item, spans)
    if coordinated:
        return coordinated
    if item.get("binding_status") == "supported" and obj and bindings:
        mentions = doc.entries(obj, "mention_evidence_ids")
        entity = item.get("entity")
        # A proposed label and value anchored in different clauses cannot complete one another.
        for sentence in sentences:
            if not sentence or not re.search(_literal(obj["name"]), sentence["quote"]):
                continue
            if entity and entity.strip() != obj["name"] or len(doc.same_section_objects(obj["name"], scope)) != 1:
                continue
            if any(other != obj["name"] and other not in obj["name"] and re.search(_literal(other), sentence["quote"])
                   for other in doc.names):
                continue
            local = [b for b in bindings if sentence["start"] <= b["start"] and b["end"] <= sentence["end"]
                     and any(b["start"] < e["end"] and e["start"] < b["end"] for e in spans)]
            if local and obj in doc.same_section_objects(obj["name"], scope):
                return _decision("supported", key=obj["object_id"], label=obj["name"], basis="legacy_model",
                                 scope=obj.get("scope"), reason="source_linked_model_binding",
                                 evidence=_unique([sentence] + local + mentions))
    return unknown


_LINKS = {
    "part_of": r"(?:is (?:a )?(?:component|part) of|为以下对象的组成部分[:：])",
    "sample_of": r"(?:was sampled from|is a sample (?:from|of)|采自|取自)",
    "located_in": r"(?:is located in|位于)",
    "member_of": r"(?:is a member of|为其成员，隶属|隶属)",
    "instance_of": r"(?:is an instance of|是下列类型的实例[:：])",
    "same_as": r"(?:is the same (?:object|instance|entity) as|is also called|亦称|也称)",
}


def _qualified_relation(doc, edge):
    a, b = doc.objects.get(edge.get("source_id")), doc.objects.get(edge.get("target_id"))
    cited = doc.entries(edge, "evidence_ids")
    connector = _LINKS.get(edge.get("predicate"))
    if not a or not b or not cited or not connector:
        return []
    if edge.get("semantic_status") == "human_approved":
        return cited
    # An explicit complete sentence is a sufficient certificate; disconnected mentions are only leads.
    pattern = _literal(a["name"]) + r"\s*" + connector + r"\s*" + _literal(b["name"])
    for e in cited:
        sent = doc.sentence(e["start"], e["end"])
        if not sent:
            continue
        if (not _UNCERTAIN.search(sent["quote"]) and re.fullmatch(r"\s*" + pattern + r"[.。]?\s*", sent["quote"], re.I)
                and re.search(pattern, e["quote"], re.I)
                and all(o in doc.same_section_objects(o["name"], doc.scope(e["start"])) for o in (a, b))
                and all(len(doc.same_section_objects(o["name"], doc.scope(e["start"]))) == 1 for o in (a, b))):
            return [sent]
    return []


def _relations(doc, direct, same_task, span_scopes):
    key = direct["id"]
    formal, candidates, parents = [], [], []
    if not key:
        return formal, candidates, parents
    edges = doc.analysis.get("relations", [])
    keys = {key} | {alias for alias, (primary, _, _) in doc.canonical.items() if primary == key}
    for edge in edges:
        predicate = edge["predicate"]
        outgoing = edge["source_id"] in keys
        if not outgoing and not (edge["target_id"] in keys and predicate in {"same_as", "member_of"}):
            continue
        other = doc.objects.get(edge["target_id"] if outgoing else edge["source_id"])
        if not other or predicate in {"different_from", "other"}:
            continue
        evidence = _qualified_relation(doc, edge)
        if evidence and {doc.scope(e["start"]) for e in evidence} != span_scopes:
            evidence = []
        if predicate == "same_as" and (doc.disputed.intersection({edge["source_id"], edge["target_id"]})
                                      or frozenset({edge["source_id"], edge["target_id"]}) in doc.conflicts):
            evidence = []
        decision = _decision("supported" if evidence else "unknown", key=other["object_id"], label=other["name"],
                             evidence=evidence or doc.entries(edge, "evidence_ids"), scope=other["scope"],
                             basis="relation" if evidence else "legacy_model", reason=predicate)
        decision.update(relation_id=edge.get("relation_id"), source_id=edge["source_id"], target_id=edge["target_id"])
        if evidence:
            formal.append(decision)
            if (outgoing or predicate == "same_as") and other.get("is_table_subject") and same_task:
                parents.append(decision)
        else:
            candidates.append(decision)
    return formal, candidates, parents


def _target(doc, direct, path, same_task, parents):
    if direct["status"] != "supported":
        return _decision(reason="direct_owner_unresolved")
    obj = doc.objects.get(direct["id"])
    anchor = path.get("owner", {}).get("ref") if path else None
    owners = doc.anchor_membership.get(anchor, set())
    common_target = (owners and same_task and all(doc.objects[k].get("is_table_subject") for k in owners)
                     and not any(pair <= owners for pair in doc.conflicts))
    identity_spans = doc.identities.get(direct["label"], [])
    identity_found = any(i["start"] < e["end"] and e["start"] < i["end"] for e in direct["evidence"] for i in identity_spans)
    if identity_found or anchor in doc.target_cells:
        return {**direct, "reason": "identity_field_occurrence", "membership_basis": "source_identity"}
    if owners and (any(pair <= owners for pair in doc.conflicts)
                   or len({doc.objects[k].get("is_table_subject") for k in owners}) != 1):
        return _decision(reason="conflicting_cell_membership", scope=anchor,
                         evidence=_unique(doc.anchor_proofs[anchor]))
    role = path.get("owner_role") if path else None
    role_proof = doc.identity_roles.get(role["quote"].strip()) if role else None
    attested_role = same_task and role_proof and (not owners or common_target)
    if attested_role:
        return {**direct, "reason": "source_identity_header", "membership_basis": "source_identity",
                "membership_sources": sorted(owners),
                "evidence": _unique(direct["evidence"] + [role] + role_proof + doc.anchor_proofs[anchor])}
    if (not path or not doc.identity_fields) and (common_target or (same_task and obj and obj.get("is_table_subject"))):
        models = sorted(owners) if common_target else [obj["object_id"]]
        proof = doc.anchor_proofs[anchor] if common_target else doc.entries(obj, "mention_evidence_ids")
        return {**direct, "reason": "legacy_target_membership", "membership_basis": "legacy_model",
                "membership_sources": models, "evidence": _unique(direct["evidence"] + proof)}
    if len({p["id"] for p in parents}) == 1:
        return parents[0]
    if path and doc.identity_fields:
        return _decision(reason="target_identity_role_unconfirmed", scope=anchor,
                         evidence=_unique(direct["evidence"] + ([role] if role else [])))
    return _decision(reason="multiple_related_targets" if parents else "target_membership_unresolved")


def _fit(doc, item, direct, spans, path, purpose, same_task):
    field = item["record_type"]
    definition = doc.analysis.get("task", {}).get("fields", {}).get(field)
    evidence = _unique(spans + (path.get("headers", []) + path.get("context", []) if path else []))
    status = item.get("target_match")
    gap = (path or {}).get("gap") or (path or {}).get("qualifier_gap")
    if item.get("blockers") or gap:
        return _decision(reason=gap or "extraction_checks_unresolved", evidence=evidence)
    if status == "unsupported":
        return _decision("contradicted", reason="legacy_field_mismatch", evidence=evidence, basis="legacy_model")
    if not spans or definition is None:
        return _decision(reason="source_or_field_missing")
    if field in doc.identity_fields and item.get("value") != direct.get("label"):
        alias = str(item.get("value") or "")
        section = doc.scope(spans[0]["start"])
        mappings = doc.aliases.get((section, alias), [])
        proved = direct.get("status") == "supported" and (
            alias in direct.get("alias_names", [])
            or bool(mappings) and {name for name, _ in mappings} == {direct.get("label")})
        if not proved:
            return _decision(reason="identity_value_not_proven_alias", evidence=evidence)
    if direct["reason"] in {"qualified_or_competing_statement", "competing_bound_owner", "conflicting_scoped_definitions"}:
        return _decision(reason=direct["reason"], evidence=evidence)
    if purpose in {"classification_threshold", "aggregate", "model_input"}:
        pattern = dict(_PURPOSES)[purpose]
        if not pattern.fullmatch(str(definition).strip()):
            return _decision(reason="field_use_requires_review", evidence=evidence)
    if status == "supported" and same_task:
        return _decision("supported", key=field, label=field, reason="source_linked_field_proposal",
                         basis="legacy_model", evidence=_unique(evidence + doc.entries(item, "binding_evidence_ids")))
    # Offline field semantics only cover an exact task-defined property label. Free-text
    # definitions can impose additional constraints and remain available for semantic review.
    if direct["status"] == "supported" and isinstance(definition, str):
        label = definition.strip()
        if (path and any(e["quote"].strip() == label for e in path.get("headers", []))
                or direct["basis"] in {"explicit_definition", "local_statement", "source_record_identity"}
                and label == item.get("property_name")):
            return _decision("supported", key=field, label=label, reason="explicit_task_label",
                             basis="source_field_label", evidence=evidence)
    return _decision(reason="field_definition_unresolved", evidence=evidence)


def resolve_observations(source: str, result: dict, analysis: dict, *, identity_fields: list[str],
                         row_subject: str) -> dict[str, dict]:
    """Return separate inspectable ownership, association and field decisions for each input ID."""
    doc = _Document(source, result, analysis, identity_fields)
    same_task = (row_subject or "").strip() == (analysis.get("table_subject") or "").strip()
    resolved = {}
    for item in doc.observations:
        spans = _spans(source, item)
        path = doc.paths.get(item["observation_id"])
        if item.get("source_kind") == "table" and path is None:
            path = {"gap": "table_pointer_unavailable"}
        competing = competing_targets(doc, item, spans, _UNCERTAIN) if same_task and path is None else []
        direct = (_decision(reason="competing_source_subjects", evidence=competing[0]["evidence"][:1])
                  if competing else _subjects(doc, item, spans, path))
        same = doc.canonical.get(direct["id"])
        if same and spans and all(doc.scope(e["start"]) == same[2] for e in spans):
            original = direct["id"]
            direct = {**direct, "id": same[0], "label": doc.objects[same[0]]["name"], "original_subject_id": original,
                      "alias_names": sorted({doc.objects[k]["name"] for k, value in doc.canonical.items()
                                             if k != same[0] and value[0] == same[0]}),
                      "basis": "explicit_equivalence", "evidence": _unique(direct["evidence"] + same[1])}
        related, candidates, parents = _relations(doc, direct, same_task, {doc.scope(e["start"]) for e in spans})
        if direct.get("parent"):
            related.append(direct["parent"])
        candidates.extend(competing)
        legacy = doc.objects.get(item.get("subject_id"))
        if legacy and legacy["object_id"] != direct["id"]:
            proposed = _decision(key=legacy["object_id"], label=legacy["name"], reason="original_binding",
                                 scope=legacy["scope"], basis="legacy_model",
                                 evidence=doc.entries(item, "binding_evidence_ids"))
            candidates.append(proposed)
            for edge in analysis.get("relations", []):
                if edge["source_id"] != legacy["object_id"] or edge["predicate"] == "different_from":
                    continue
                parent = doc.objects.get(edge["target_id"])
                if parent:
                    candidates.append({**_decision(key=parent["object_id"], label=parent["name"],
                        scope=parent["scope"], reason=edge["predicate"], basis="proposed_relation_chain",
                        evidence=_unique(proposed["evidence"] + doc.entries(edge, "evidence_ids"))),
                        "via": legacy["object_id"], "relation_id": edge.get("relation_id")})
        target = _target(doc, direct, path, same_task, parents)
        label = item.get("entity")
        if isinstance(label, str) and label.strip():
            # Normalization is for retrieval only. Candidate evidence always contains original text.
            term = normalized_view(label.strip()).text.casefold()
            for name, objects in doc.names.items():
                name_term = doc.search_names[name]
                if term not in name_term and name_term not in term:
                    continue
                for obj in objects:
                    if obj["object_id"] not in {direct["id"], target["id"]}:
                        candidates.append(_decision(key=obj["object_id"], label=name, basis="lexical_candidate",
                                          scope=obj["scope"], reason="name_overlap", evidence=doc.entries(obj, "mention_evidence_ids")))
        context = [str(item.get("conditions") or ""), str(item.get("statistic") or "")]
        if path:
            context.extend(e["quote"] for e in path.get("context", []) + path.get("headers", []))
        else:
            context.extend(e["quote"] for e in spans)
        purpose = next((name for name, pattern in _PURPOSES if any(pattern.search(s) for s in context)), "unspecified")
        fit = _fit(doc, item, direct, spans, path, purpose, same_task)
        definition = analysis.get("task", {}).get("fields", {}).get(item["record_type"], "")
        fit_evidence = spans + (path.get("headers", []) + path.get("context", []) if path else [])
        if path is None:
            # A value-only citation can omit the predicate and its qualification.
            # Use its own statement only when this reported value is unambiguous there.
            value = "" if item.get("value") is None else str(item["value"])
            for span in spans:
                sentence = doc.sentence(span["start"], span["end"])
                if sentence and value and value in span["quote"] and sentence["quote"].count(value) == 1:
                    fit_evidence.append(sentence)
        if (competing and fit["reason"] == "field_definition_unresolved"
                and definition.strip() == item.get("property_name")):
            fit = _decision("supported", key=item["record_type"], label=definition,
                            reason="explicit_task_label", basis="source_field_label", evidence=spans)
        fit = checked_field_fit(definition, item, fit, fit_evidence)
        entry = {"direct": direct, "target": target, "fit": fit, "candidates": candidates, "related": related,
                 "purpose": purpose, "statistic": item.get("statistic")}
        if path:
            entry["table_context"] = path
            if direct["status"] == "supported":
                entry["record_group"] = path["record_group"]
        resolved[item["observation_id"]] = entry
    return resolved
