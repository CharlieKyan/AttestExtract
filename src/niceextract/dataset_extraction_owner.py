"""Carry an extracted owner into a view, keeping row identity and field scope separate."""

from bisect import bisect_right
from collections import defaultdict
import re

from .chunking import _protected_positions, _sentence_ends
from .dataset_identity import RegisteredDesignations, _row_type_suffix
from .dataset_field_fit import _requested_property
from .dataset_resolution_table import table_type_lists
from .dataset_resolution import _literal, _UNCERTAIN
from .value_location import locate_value


def _display_name(obj, citations):
    """Strip a verifier's scope note only when the remaining label occurs in its own source."""
    name = obj["name"]
    for label in dict.fromkeys([name, re.split(r"\s*[（(]", name, maxsplit=1)[0].strip()]):
        if label and any(re.search(_literal(label), q["quote"]) for q in citations):
            return label
    return None


_CLAUSE_BREAK = re.compile(r"[，；;]|,(?!\d{3}\b)")
_SHARED = re.compile(r"\b(?:jointly|together|combined|collectively|respectively|including|except|"
                     r"in (?:total|combination)|shared (?:by|with))\b|合计|总计|共同|共计|包括|分别", re.I)


def _contextual_membership(quote, local, predicate):
    """Check the archived relation in its stated direction, with a local parent referent."""
    text = quote.strip().rstrip("。.;；").strip()
    if re.search(r"(?:不|未|可能|假如|若|拟|建议)(?:再|曾|明确)?(?:包含|包括|由|为|组成|构成)", text):
        return False
    child = _literal(local)
    parent = r"(?:this|that|the above) (?:[\w-]+ ){0,3}[\w-]+"
    child_ref = rf"(?:(?:the|a|an) )?{child}"
    if predicate == "sample_of":
        return bool(re.fullmatch(rf"{child_ref} (?:was sampled|was taken|originates) from {parent}", text, re.I)
                    or re.fullmatch(rf"{child}\s*(?:取自|采自)(?:上述|该|本)[\u3400-\u9fff]{{1,12}}", text))
    if predicate not in {"part_of", "member_of"}:
        return False
    if re.fullmatch(rf"{parent} (?:contains|includes|comprises|consists of) {child_ref}", text, re.I):
        return True
    noun = "part|component" if predicate == "part_of" else "member"
    if re.fullmatch(rf"{child_ref} is (?:a |an )?(?:{noun}) of {parent}", text, re.I):
        return True
    # The source may enumerate two components in a copular description.
    # Match the whole statement so an action involving both names cannot
    # masquerade as membership. A repeated type comes from the endpoints.
    subject = r"(?:此外|其中)?(?:上述|该|本)(?P<head>[\u3400-\u9fff]{1,12}?)"
    if re.fullmatch(subject + rf"(?:包含|包括){child}", text):
        return True
    other = r"[\u3400-\u9fff]{1,20}"
    sequence = rf"(?:{child}(?:和|与|及|、){other}|{other}(?:和|与|及|、){child})"
    if re.fullmatch(subject + rf"(?:由|包含|包括){sequence}(?:组成|构成)", text):
        return True
    pair = re.fullmatch(subject + rf"为(?P<a>{other})(?:与|和)(?P<b>{other})互(?P<type>[\u3400-\u9fff]{{1,3}})", text)
    return bool(pair and local in (pair["a"], pair["b"]) and
                all(pair[k].endswith(pair["type"]) for k in ("head", "a", "b")))


def _allowed_roles(definition):
    """Read literal categories from an affirmative alternative in the task's field definition."""
    clauses = re.split(r"[，,。.;；\n]", definition)
    if any(re.search(r"(?:\b(?:not|exclude\w*|except)\b|不(?:含|包括|适用)|排除).*?(?:\brelated\b|\bassociated\b|相关|关联)",
                     c, re.I) for c in clauses):
        return []
    for clause in clauses:
        if re.search(r"\b(?:not|except|exclude\w*|only)\b|不|排除|仅限", clause, re.I):
            continue
        match = re.search(r"\b(?:or|including|and)\s+(?:(?:its|their)\s+)?(?:related|associated)\s+(.+)", clause, re.I)
        if match:
            labels = re.split(r"\s+(?:of|with|for|under|in)\s+", match[1], maxsplit=1, flags=re.I)[0]
            return [s.strip() for s in re.split(r"\s+(?:or|and)\s+", labels, flags=re.I) if s.strip()]
        match = re.search(r"(?:或(?:者)?|包括|及其)(?:其|与之)?(?:相关|关联)([^的]+?)(?:的|$)", clause)
        if match:
            return [s.strip() for s in re.split(r"[、及和或]", match[1].removesuffix("等")) if s.strip()]
    return []


def _role_spellings(role):
    spellings = {role}
    if re.fullmatch(r"[A-Za-z -]+s", role) and not role.endswith("ss"):
        spellings.add(role[:-1])
    return spellings


def _role_in(role, label):
    return any(re.search(_literal(word), label, re.I) for word in _role_spellings(role))


def _source_role(roles, name, citations):
    if any(_role_in(role, name) for role in roles):
        return True
    for role in roles:
        for spelling in _role_spellings(role):
            statement = (_literal(name) + r"\s*(?:is (?:an? )?|was (?:an? )?|属于|是一种|是)" +
                         _literal(spelling) + r"\s*[.。]?")
            if any(re.fullmatch(statement, e["quote"].strip(), re.I) for e in citations):
                return True
    return False


class ExtractionOwners:
    """Resolve a recorded assignment using its own source citations and existing verification."""

    def __init__(self, source, snapshot, same_task, label_field=None):
        self.source = source
        self.same_task = same_task
        self.row_subject = snapshot.get("table_subject") or ""
        self.definitions = snapshot.get("task", {}).get("fields", {})
        self.identity_definition = self.definitions.get(label_field, "")
        self.designations = RegisteredDesignations(source, snapshot, label_field) if same_task else None
        self.table_types = table_type_lists(source, snapshot["observations"])
        self.evidence = {e["evidence_id"]: e for e in snapshot["evidence"]}
        self.objects = {o["object_id"]: o for o in snapshot["objects"]}
        self.mentions = {k: [self.evidence[e] for e in o["mention_evidence_ids"]]
                         for k, o in self.objects.items()}
        self.names = {k: _display_name(o, self.mentions[k]) for k, o in self.objects.items()}
        self.identity_mentions = defaultdict(list)
        self.literal_identity_spans = defaultdict(list)
        self.study_targets = []
        for item in snapshot["observations"]:
            label = item.get("entity")
            if (item.get("record_type") != label_field or not label or item.get("value") != label
                    or item.get("target_match") == "unsupported" or item.get("blockers")):
                continue
            for entry in item["source_evidence"]:
                span = entry.get("span")
                if not span or not re.search(_literal(label), source[span["start"]:span["end"]]):
                    continue
                quote = source[span["start"]:span["end"]]
                proof = {**span, "quote": quote}
                if (quote == label and {"entity", "value"}.issubset(entry["fields"])
                        and len(entry.get("matches", [])) <= 1 and len(entry.get("source_matches", [])) <= 1
                        and item.get("name_handling") == "as_reported"):
                    self.literal_identity_spans[label].append(proof)
                if item.get("target_match") != "supported":
                    continue
                self.identity_mentions[label].append(proof)
                designation = (r"(?:研究(?:区|对象)?(?:的)?(?:目的|目标)[\u3400-\u9fff]{0,4}|"
                               r"(?:study (?:target|subject|focus)|target of (?:this|the) study)"
                               r"\s*(?:(?:is|was|:)\s*)?)" + _literal(label))
                if re.search(designation, quote, re.I) and not _UNCERTAIN.search(quote):
                    self.study_targets.append((label, "mention-" + item["observation_id"], proof))
        # Registries sometimes qualify a local name with a location only used elsewhere.
        # Reuse the name from a bound identity observation at the registry's own occurrence.
        identity_names = defaultdict(set)
        for item in snapshot["observations"]:
            key, label = item.get("subject_id"), item.get("entity")
            if (not label_field or item.get("record_type") != label_field or not label
                    or item.get("binding_status") != "supported" or key not in self.objects
                    or not self.objects[key]["is_table_subject"]
                    or not re.search(_literal(label), self.objects[key]["name"])):
                continue
            mentions = self.mentions[key]
            if any(re.search(_literal(label), m["quote"]) and m["evidence_id"] in item["evidence_ids"] for m in mentions):
                identity_names[key].add(label)
        self.row_names = {key: next(iter(names)) for key, names in identity_names.items() if len(names) == 1}
        self.subjects = {n for n in self.names.values() if n}
        self.subjects.update(o["entity"].strip() for o in snapshot["observations"]
                             if isinstance(o.get("entity"), str) and o["entity"].strip())
        self.edges = defaultdict(list)
        for e in snapshot["relations"]:
            self.edges[e["source_id"]].append(e)
        protected, blocked = _protected_positions(source)
        self.ends = sorted({0, len(source), *_sentence_ends(source, 0, len(source), protected, blocked),
                            *(m.end() for m in re.finditer(r"\n", source) if not protected[m.start()])})

    def _local(self, obs, name):
        owners, values = [], []
        for entry in obs["source_evidence"]:
            span = entry.get("span")
            if not span or len(entry.get("matches", [])) > 1 or len(entry.get("source_matches", [])) > 1:
                continue
            start, end = span["start"], span["end"]
            if "entity" in entry["fields"]:
                owners.extend((start + m.start(), start + m.end())
                              for m in re.finditer(_literal(name), self.source[start:end]))
            if "value" in entry["fields"] and (v := locate_value(self.source, obs["value"], start, end)):
                values.append(v)
        if not owners or len({(v["start"], v["end"]) for v in values}) != 1:
            return None
        value = values[0]
        i = bisect_right(self.ends, value["start"])
        start, end = self.ends[i - 1], self.ends[i]
        if value["end"] > end or not any(start <= l < r <= end for l, r in owners):
            return None
        quote = self.source[start:end]
        if _UNCERTAIN.search(quote):
            return None
        # A different registered object in the same clause may be the actual owner.
        if self._competes(name, quote, start):
            return None
        return {"start": start, "end": end, "quote": quote}

    def apply_identity(self, obs):
        if self.designations and not obs.get("row_relation_conflict"):
            self.designations.apply(obs, self.identity_mentions)

    def _identity_at(self, name, start, end):
        """An identity observation and its registered name refer to this original occurrence."""
        matches = [key for key, short in self.row_names.items() if short == name and
                   any(m["start"] <= start and end <= m["end"] for m in self.mentions[key])]
        return matches[0] if len(matches) == 1 else None

    def _bound_occurrence(self, obs, bound, name):
        """Join a checked binding to the very same reading in the object's own mention."""
        obj = self.objects.get(bound)
        prop = obs.get("property_name")
        if (not obj or not self.same_task or not prop or obs.get("target_match") != "supported"
                or obs.get("name_handling") not in {None, "as_reported"}
                or obs["direct"]["reason"] != "direct_owner_unresolved"):
            return None
        values = [e["span"] for e in obs["source_evidence"] if "value" in e["fields"] and e.get("span")
                  and len(e.get("source_matches", [])) <= 1 and len(e.get("matches", [])) <= 1]
        readings = {tuple(v[k] for k in ("start", "end")) for s in values
                    if (v := locate_value(self.source, obs["value"], s["start"], s["end"]))}
        if len(readings) != 1:
            return None
        start, end = readings.pop()
        citations = [self.evidence[k] for k in obs["binding_evidence_ids"]]
        if not any(c["start"] <= start < end <= c["end"] and prop in c["quote"] for c in citations):
            return None
        options = {}
        for mention in self.mentions[bound]:
            if not mention["start"] <= start < end <= mention["end"]:
                continue
            before = self.source[mention["start"]:start]
            if prop not in before or _UNCERTAIN.search(before) or re.search(r"[。;；!?！？\n]|(?<!\d)\.(?=\s)", before):
                continue
            parts = re.split(r"[，,](?!\d{3}(?:\D|$))", before)
            if len(parts) > 1 and not re.match(r"\s*(?:(?:and|its|with)\s+|其|及其)?" + _literal(prop),
                                               parts[-1], re.I):
                # Another named owner (even a previously unseen one) before the second
                # predicate invalidates the proposed inheritance of the shared subject.
                continue
            local = name
            if not re.match(_literal(name), before):
                # An extraction can prepend contextual qualifiers that the author supplied elsewhere.
                local = before.split(prop, 1)[0].strip()
                if (not local or not name.endswith(local) or not obj["name"].endswith(local)
                        or not obj["name"].startswith(name.removesuffix(local))):
                    continue
            lead = parts[0][len(local):]
            if re.search(r"\b(?:and|or|together|combined|with another)\b|以及|及其|和|与|及|或|合计", lead, re.I):
                continue
            if len(parts) == 1 and not re.match(r"\s*(?:(?:had|has|reported|showed)\s+(?:a\s+)?|的)?"
                                                + _literal(prop), lead, re.I):
                continue
            # A source-grounded short name cannot identify a more specific child just
            # because it is a prefix of its registry label. Resolve scope additions only
            # when that scope is also present in this very paragraph.
            registered = re.split(r"\s*[（(]", obj["name"], maxsplit=1)[0].strip()
            if not registered.endswith(local) or self._competes(local, before, mention["start"], property_name=prop):
                continue
            scope_label = registered.removesuffix(local).strip(" -")
            if scope_label:
                paragraph = re.split(r"\r?\n\s*\r?\n", self.source[:mention["start"]])[-1]
                if not re.search(_literal(scope_label), paragraph, re.I):
                    continue
            if any(k != bound and local in self.objects[k]["name"]
                   and any(m["start"] == mention["start"] and end <= m["end"] for m in other)
                   for k, other in self.mentions.items()):
                continue
            options[local] = {**mention, "quote": self.source[mention["start"]:mention["end"]]}
        return next(iter(options.items())) if len(options) == 1 else None

    def _referenced_parent(self, key, local, proof):
        """A directed contextual name can be grounded by both endpoints in one paragraph."""
        found = {}
        for edge in self.edges[key]:
            parent = edge["target_id"]
            if edge["predicate"] not in {"part_of", "member_of", "sample_of"} or not self.objects[parent]["is_table_subject"]:
                continue
            label = self.row_names.get(parent) or self.names[parent]
            if not label:
                continue
            label = re.split(r"\s*[（(]", label, maxsplit=1)[0].strip()
            qualified = re.sub(r"[\s-]+", "", self.objects[key]["name"]).casefold()
            if label == local or qualified != re.sub(r"[\s-]+", "", label + local).casefold():
                continue
            if any(e["target_id"] == parent and e["predicate"] in {"same_as", "different_from"} for e in self.edges[key]):
                continue
            for ref in [self.evidence[k] for k in edge["evidence_ids"]]:
                if _UNCERTAIN.search(ref["quote"]) or not _contextual_membership(ref["quote"], local, edge["predicate"]):
                    continue
                for anchor in self.mentions[parent]:
                    if not (anchor["start"] <= proof["start"] < ref["end"] and label in anchor["quote"]):
                        continue
                    passage = self.source[anchor["start"]:max(proof["end"], ref["end"])]
                    if re.search(r"\r?\n\s*\r?\n", passage):
                        continue
                    if any(o["is_table_subject"] and k != parent and any(
                            anchor["end"] < s["start"] < ref["end"] for s in self.mentions[k])
                           for k, o in self.objects.items()):
                        continue
                    found[parent] = {"id": parent, "label": label, "status": "supported",
                                     "scope": self.objects[parent].get("scope"), "basis": "source_bound_relation",
                                     "reason": edge["predicate"], "relation_id": edge["relation_id"],
                                     "evidence": [anchor, ref, proof]}
        return next(iter(found.values())) if len(found) == 1 else None

    def _study_context(self, obs, direct, definition):
        """Archive an explicitly attributed cohort statistic under its introduced study subject.

        The comparison must open the preceding sentence in this subsection. The identity
        observation, cohort evidence and current reading remain independently traceable.
        """
        roles = _allowed_roles(definition)
        if (not roles or not self.study_targets or not self.same_task or obs["source_kind"] == "table"
                or obs.get("subject_id") or obs.get("name_resolution") or obs.get("blockers")
                or obs.get("name_handling") != "as_reported" or obs.get("target_match") != "supported"
                or obs["fit"]["status"] != "supported" or direct["label"] in self.identity_mentions):
            return None
        spans = [e["span"] for e in obs["source_evidence"] if "value" in e["fields"] and e.get("span")]
        if not spans:
            return None
        start = min(s["start"] for s in spans)
        end = max(s["end"] for s in spans)
        section = self.source.rfind("\n#", 0, start)
        if section < 0:
            return None
        body = self.source.find("\n", section + 1, start)
        if body < 0:
            return None
        # Inspect the preceding completed sentence, allowing line wraps in converted text.
        stops = list(re.finditer(r"[。！？!?]|(?<!\d)\.(?=\s)", self.source[body:start]))
        if not stops:
            return None
        left = body + (stops[-2].end() if len(stops) >= 2 else 0)
        right = body + stops[-1].end()
        previous = self.source[left:right].strip()
        left = self.source.index(previous, left, right)
        measurement = self.source[right:end]
        attributed = re.match(r"\s*(?:based on|according to|from (?:the |these )?(?:data|measurements)"
                              r"|根据[^。！？!?\n，,]{1,24}(?:资料|数据|分析|测量))", measurement, re.I)
        if (not any(_role_in(role, previous) for role in roles) or direct["label"] not in previous
                or not (attributed or any(_role_in(role, measurement) for role in roles))):
            return None
        area = self.source[left:end]
        if (_UNCERTAIN.search(area) or re.search(r"\b(?:other|another|control|instead|unlike)\b|另一|其他|对照|转而", area, re.I)
                or "<table" in area or "\n#" in area):
            return None
        targets = [(name, key, evidence) for name, key, evidence in self.study_targets
                   if evidence["end"] <= left and re.match(_literal(name), previous)]
        if len(targets) != 1:
            return None
        label, key, declaration = targets[0]
        provenance = measurement.split(direct["label"], 1)[0]
        if re.search(r"来自|取自|采自|\b(?:data|readings|samples?|specimens?)\s+(?:from|at)\b", provenance, re.I):
            # An explicitly named origin goes through its own source link. A bare
            # 'data from samples' introduces a method, not another named origin.
            qualified = any(re.search(r"\bdata from\s+" + _literal(spelling) + r"\s*,", provenance, re.I)
                            for role in roles for spelling in _role_spellings(role))
            if not qualified and label not in provenance:
                return None
        if len({o.get("scope") for o in self.objects.values() if o["name"] == label}) > 1:
            return None
        if any(re.search(_literal(other), area) for other in self.identity_mentions
               if other not in {label, direct["label"]} and other not in label):
            return None
        context = {"start": left, "end": right, "quote": self.source[left:right]}
        return {"id": key, "label": label, "status": "supported", "basis": "study_cohort",
                "reason": "scoped_sample_report", "scope": None, "evidence": [declaration, context, *direct["evidence"]]}

    def _source_classification(self, obs, parent, definition):
        """The author may explicitly classify the measured material as the task's row kind."""
        name, target = obs["direct"]["label"], parent["label"]
        if (not target or not name.startswith(target) or name == target or parent["reason"] != "part_of"
                or _requested_property(definition).casefold() != (obs.get("property_name") or "").casefold()):
            return None
        # Both the target's reported label and the identity field must actually use this type.
        kinds = [target[i:] for i in range(len(target) - 1)
                 if target[i:] in self.identity_definition and (not target[i:].isascii() or not i or target[i-1].isspace())]
        for e in obs["direct"]["evidence"]:
            quote = e["quote"].strip().rstrip("。.")
            if (not quote.startswith(name) or _UNCERTAIN.search(quote)
                    or not locate_value(self.source, obs["value"], e["start"], e["end"])):
                continue
            last = re.split(r"[，,；;]", quote)[-1].strip()
            if re.match(r"^(?:属于|归类为|归为|classified as\b|classed as\b)", last, re.I) and any(
                    last.endswith(k) and last != target and not last.endswith(target) for k in kinds):
                return e
        return None

    def _competes(self, name, quote, offset, value_span=None, *, property_name=None):
        """Only count distinct owner mentions, outside the proposed value's own span."""
        owner_mentions = list(re.finditer(_literal(name), quote))
        properties = list(re.finditer(_literal(property_name), quote)) if property_name else []
        for other in self.subjects:
            if other == name:
                continue
            for match in re.finditer(_literal(other), quote):
                left, right = offset + match.start(), offset + match.end()
                if any(m.start() <= match.start() and match.end() <= m.end() for m in owner_mentions):
                    continue
                if any(m.start() <= match.start() and match.end() <= m.end() for m in properties):
                    continue
                if value_span and value_span["start"] <= left and right <= value_span["end"]:
                    continue
                if _row_type_suffix(other, name, self.row_subject):
                    continue
                if self.names.get(self._identity_at(name, left, right)) == other:
                    continue
                return True
        return False

    def _named_description(self, obs, name):
        """A whole quoted 'name + description' can be one of several assignments in a sentence."""
        value = obs["value"]
        if (obs.get("unit") or not isinstance(value, str) or not value.strip() or value == name
                or re.search(r"\d", value) or re.match(r"(?:and|or|nor|both)\b|[和与或及、]", value, re.I)):
            return []
        citations = [e for e in obs["source_evidence"] if e.get("span") and len(e.get("matches", [])) <= 1
                     and len(e.get("source_matches", [])) <= 1]
        owner_refs = [e["span"] for e in citations if "entity" in e["fields"]]
        found = {}
        for e in citations:
            if "value" not in e["fields"]:
                continue
            start, end = e["span"]["start"], e["span"]["end"]
            quote = self.source[start:end]
            reading = locate_value(self.source, value, start, end)
            # A separate, cited identity can support the local short name in a qualified description.
            # Require the task's literal property between the actual name and this value.
            if reading and self.identity_mentions.get(name) and quote.count(name) == 1:
                pos = quote.index(name)
                qualifier = self.source[start + pos + len(name):reading["start"]].strip()
                if (len(qualifier) >= 2 and qualifier in self.definitions.get(obs["record_type"], "")
                        and not _UNCERTAIN.search(quote) and start + pos + len(name) <= reading["start"]
                        and reading["end"] == end and not self._competes(name, quote, start, reading,
                                                                         property_name=qualifier)
                        and not re.search(r"[，,。;；\n]", quote[:pos])):
                    found[start, end] = {**e["span"], "quote": quote}
                    continue
            # A separately source-bound identity at this occurrence can head a
            # descriptive clause with intervening qualifiers. Treat material names
            # inside that clause's value as attributes, not as rival subjects.
            attested_identity = self._identity_at(name, start, start + len(name))
            if not attested_identity and not re.fullmatch(_literal(name) + r"[ \t]*" + re.escape(value.strip()), quote):
                continue
            if (not re.match(_literal(name), quote) or not reading or reading["start"] < start + len(name)
                    or bisect_right(self.ends, start) != bisect_right(self.ends, end - 1)
                    or not any(s["start"] <= start < start + len(name) <= s["end"] for s in owner_refs)
                    or self._competes(name, quote, start, reading if attested_identity else None) or _UNCERTAIN.search(quote)
                    or re.search(r"\b(?:combined|together|total)\b|合计|总计|共同", quote, re.I)):
                continue
            before, after = self.source[:start].rstrip(" \t"), self.source[end:].lstrip(" \t")
            breaks = ",，;；。.!?！？\r\n"
            if before and before[-1] not in breaks or after and after[0] not in breaks:
                continue
            if before and before[-1] in ",，":
                previous = re.split(r"[,，;；。.!?！？\r\n]", before[:-1])[-1].strip()
                if previous in self.subjects:
                    continue  # The immediately preceding name may share the predicate.
            found[start, end] = {"start": start, "end": end, "quote": quote}
        return list(found.values()) if len(found) == 1 else []

    def _extracted_proof(self, obs, name):
        if description := self._named_description(obs, name):
            return description
        proof, owners, values = [], set(), set()
        for entry in obs["source_evidence"]:
            span = entry.get("span")
            if not span or len(entry.get("matches", [])) > 1 or len(entry.get("source_matches", [])) > 1:
                continue
            quote = self.source[span["start"]:span["end"]]
            if "entity" in entry["fields"]:
                if self._competes(name, quote, span["start"]):
                    return self._joint_clause(obs, name)
                for m in re.finditer(_literal(name), quote):
                    owners.add(bisect_right(self.ends, span["start"] + m.start()))
                    proof.append({**span, "quote": quote})
            if "value" in entry["fields"] and (v := locate_value(self.source, obs["value"], span["start"], span["end"])):
                i = bisect_right(self.ends, v["start"])
                statement = self.source[self.ends[i - 1]:self.ends[i]]
                if self._competes(name, statement, self.ends[i - 1]):
                    return self._joint_clause(obs, name)
                values.add(i)
        if not proof or len(values) != 1:
            return []
        i = values.pop()
        if i in owners:
            return proof
        # Two cited sentences may be explicitly connected by a pronoun. Keep
        # independent paragraphs or a newly introduced referent for review.
        if i - 1 not in owners:
            return []
        context = self.source[self.ends[i - 2]:self.ends[i]]
        sentence = self.source[self.ends[i - 1]:self.ends[i]].lstrip()
        if (re.search(r"\n\s*\n", context) or not re.match(r"(?:it|its|this|these|they|their)\b|其|该|此", sentence, re.I)
                or re.search(r"\b(?:different|another|other)\b|另一|不同|其他", sentence, re.I)):
            return []
        return proof

    def _joint_clause(self, obs, name):
        """Keep a complete, jointly cited assignment when another clause mentions a different object."""
        readings = set()
        clauses = {}
        for entry in obs["source_evidence"]:
            if "value" not in entry["fields"]:
                continue
            span = entry.get("span")
            if not span or len(entry.get("matches", [])) > 1 or len(entry.get("source_matches", [])) > 1:
                return []
            value = locate_value(self.source, obs["value"], span["start"], span["end"])
            if not value:
                return []
            readings.add((value["start"], value["end"]))
            if "entity" not in entry["fields"]:
                continue
            i = bisect_right(self.ends, value["start"])
            start, end = self.ends[i - 1:i + 1]
            sentence = self.source[start:end]
            if _UNCERTAIN.search(sentence) or _SHARED.search(sentence):
                return []
            previous = ""
            left = start
            for boundary in [*(start + m.start() for m in _CLAUSE_BREAK.finditer(sentence)), end]:
                text = self.source[left:boundary].strip()
                if left <= value["start"] < value["end"] <= boundary:
                    l = left + len(self.source[left:boundary]) - len(self.source[left:boundary].lstrip())
                    r = l + len(text.rstrip("。.!?！？"))
                    # The source evidence must cover the clause, including its left context.
                    # A bare name before the comma can be another owner of a shared predicate.
                    if (span["start"] <= l and r <= span["end"] and previous not in self.subjects
                            and len(list(re.finditer(_literal(name), text))) == 1
                            and not self._competes(name, text, l, value)):
                        clauses[l, r] = {"start": l, "end": r, "quote": self.source[l:r]}
                    break
                previous = text
                left = boundary + 1
        if len(clauses) != 1 or len(readings) != 1:
            return []
        proof = next(iter(clauses.values()))
        # Recover the assignment, but a local clause alone says nothing about
        # eligibility on a named row axis. A separate identity citation must
        # ground the local subject to give this relaxation that authority.
        literal_identity = any(proof["start"] <= c["start"] < c["end"] <= proof["end"]
                               for c in self.literal_identity_spans.get(name, []))
        registered = self.designations and self.designations.match({"label": name, "evidence": [proof]},
                                                                    obs, self.identity_mentions)
        if not literal_identity and not registered:
            obs["local_clause_assignment"] = True
        return [proof]

    def _row_conflict(self, obs, name):
        # A flag for some other occurrence or enclosing name cannot resolve this
        # claim. Keep an independently established local member assignment unless
        # the registry also supplies evidence that it describes a different grain.
        mentions = (obs.get("table_context") or {}).get("owner")
        spans = ([mentions] if mentions else [
            e["span"] for e in obs["source_evidence"] if "entity" in e["fields"] and e.get("span")
        ])
        lookup = obs.get("partition_lookup") or {}
        membership = set()
        for key, obj in self.objects.items():
            if not any(m["start"] <= s["start"] < s["end"] <= m["end"] and name in m["quote"]
                       for s in spans for m in self.mentions[key]) or name not in obj["name"]:
                continue
            if (lookup.get("mention") == name and self.names[key] == name and obj.get("kind") == "instance"
                    and not any(e["predicate"] in {"part_of", "sample_of", "member_of", "different_from"}
                                for e in self.edges.get(key, []))):
                continue
            membership.add(obj["is_table_subject"])
        if False in membership:
            return "source_registered_outside_row" if membership == {False} else "conflicting_row_membership"
        # A positional description can itself be the requested row kind, e.g. grid
        # cells. Other explicitly named-row tasks require support for that link.
        if not re.search(r"\b(?:named|individual)\b|明确命名|具体", self.row_subject, re.I):
            return None
        if mentions and (proof := self.table_types.get((mentions["ref"].rsplit("-r", 1)[0], name))):
            if not re.search(r"\b(?:kinds?|types?|classes|categories)\b|种类|类型|类别", self.row_subject, re.I):
                obs["row_scope_evidence"] = [proof, mentions]
                return "source_type_on_named_row_axis"
        position = re.fullmatch(r"(?:the\s+)?(?:rightmost|leftmost|topmost|bottommost)\s+([a-z ]+)", name, re.I)
        if position and not any(word.casefold() in self.row_subject.casefold()
                                for word in position[1].split() if len(word) > 2):
            return "positional_object_row_unresolved"
        position = re.fullmatch(r"最[左右上下前后]([\u3400-\u9fff]{2,12})", name)
        if position and not any(position[1][i:i+2] in self.row_subject for i in range(len(position[1]) - 1)):
            return "positional_object_row_unresolved"
        return None

    def _compound_parent(self, bound, name, local):
        """Ground a directed archived proposal whose registered labels have display suffixes."""
        if not bound or self.names.get(bound) != name or not local:
            return None
        parents = {}
        for edge in self.edges[bound]:
            key = edge["target_id"]
            other = self.objects[key]
            label = self.names[key] or self.row_names.get(key)
            if (edge["predicate"] not in {"part_of", "sample_of", "member_of"} or not other["is_table_subject"]
                    or not label or name == label or not re.search(_literal(label), name)
                    or any(e["target_id"] in {bound, key} and e["predicate"] in {"same_as", "different_from"}
                           for oid in (key, bound) for e in self.edges[oid])):
                continue
            proof = [self.evidence[e] for e in edge["evidence_ids"]]
            if not any(local["start"] <= e["start"] < e["end"] <= local["end"] and
                       re.search(_literal(name), e["quote"]) for e in proof):
                continue
            if sum((value or self.row_names.get(k)) == label for k, value in self.names.items()) != 1:
                continue
            parents[key] = {"status": "supported", "id": key, "label": label, "scope": other.get("scope"),
                            "reason": edge["predicate"], "relation_id": edge["relation_id"],
                            "basis": "source_bound_relation", "evidence": [*proof, *self.mentions[key]]}
        return next(iter(parents.values())) if len(parents) == 1 else None

    def apply(self, obs, definition):
        direct, target = obs["direct"], obs["target"]
        name = obs.get("entity")
        if not isinstance(name, str) or not name.strip():
            return
        name = name.strip()
        bound = obs.get("subject_id") if obs["binding_status"] == "supported" else None
        local = None
        ordinary = (obs["binding_status"] == "as_extracted" and obs.get("name_handling") == "as_reported"
                    and self.same_task)
        if obs["source_kind"] != "table" and direct["reason"] == "direct_owner_unresolved":
            proof = []
            binding = self._bound_occurrence(obs, bound, name)
            if binding:
                literal, local = binding
                direct = obs["direct"] = {"id": bound, "label": literal, "status": "supported",
                                           "scope": self.objects[bound]["scope"], "basis": "source_occurrence_binding",
                                           "reason": "same_observation_binding", "evidence": [local]}
                prop = obs.get("property_name")
                if (obs.get("blockers") and all(b.get("field") == "entity" and b.get("code") in
                        {"evidence_matched", "evidence_normalized"} for b in obs["blockers"])
                        and obs["fit"]["reason"] == "extraction_checks_unresolved"
                        and _requested_property(definition).casefold() == prop.casefold()):
                    obs["binding_owner_blockers_resolved"] = True
                    obs["fit"] = {**obs["fit"], "status": "supported", "reason": "field_and_reading_in_binding",
                                  "evidence": [local], "id": obs["record_type"], "label": prop}
                if self.objects[bound]["is_table_subject"] and not obs.get("name_resolution"):
                    obs["target"] = {**direct, "reason": "bound_row_at_observation"}
                    return
            elif ordinary:
                # The extraction explicitly assigned this reading, possibly across a sentence
                # boundary. Carry the original field citation even when it uses a pronoun.
                proof = self._extracted_proof(obs, name)
            else:
                local = self._local(obs, name)
                if local:
                    proof = [local]
            if proof:
                obj = self.objects.get(bound)
                matched = obj and self.names.get(bound) == name and self.same_task
                direct = obs["direct"] = {
                    "status": "supported", "label": name,
                    "id": bound if matched else "mention-" + obs["observation_id"],
                    "scope": obj.get("scope") if matched else None, "basis": "extraction_assignment",
                    "reason": "source_linked_extracted_owner", "evidence": proof,
                }
        if ordinary and direct["status"] == "supported" and target.get("membership_basis") != "source_identity":
            if obs.get("local_clause_assignment") and target["status"] != "supported":
                return
            if conflict := self._row_conflict(obs, name):
                obs["target"] = {**target, "status": "unknown", "label": None, "id": None,
                                 "reason": conflict, "basis": "source_scope_check"}
                return
            if parent := self._study_context(obs, direct, definition):
                obs["target"] = parent
                obs["related"].append(parent)
                obs["associated_row"] = True
                # The source cohort, within this parent and block, is shared across readings.
                direct["id"] = f"cohort-{parent['id']}-{parent['evidence'][1]['start']}-{name}"
                return
        if (ordinary and direct["status"] == "supported" and target["reason"] == "target_identity_role_unconfirmed"
                and not (obs.get("table_context") or {}).get("gap")):
            obs["target"] = {**direct, "reason": "extracted_row_subject"}
            return
        if direct["status"] != "supported" or target["reason"] not in {
            "target_membership_unresolved", "direct_owner_unresolved", "legacy_target_membership",
            "part_of", "member_of", "sample_of", "related"
        }:
            return
        if self.same_task:
            parent = target if (target["status"] == "supported" and direct["id"] != target["id"]
                                and target["reason"] in {"part_of", "member_of", "sample_of", "related"}) else None
            receipts = [r for r in obs["related"] if r.get("basis") == "scoped_name" and r["reason"] == "related"
                        and r.get("source_id") == direct["id"] and r["status"] == "supported"
                        and self.objects.get(r["id"], {}).get("is_table_subject")]
            if len(receipts) == 1:
                parent = receipts[0]
            if not parent and bound and direct["id"] == bound:
                if direct["reason"] == "source_linked_model_binding":
                    local = next((s for s in direct["evidence"] if name in s["quote"] and
                                  locate_value(self.source, obs["value"], s["start"], s["end"])), None)
                parent = self._compound_parent(bound, name, local or self._local(obs, name))
                if not parent and direct["reason"] == "same_observation_binding":
                    parent = self._referenced_parent(bound, direct["label"], direct["evidence"][0])
            if parent:
                obs["target"] = parent
                if not any(r["id"] == parent["id"] and r["status"] == "supported" for r in obs["related"]):
                    obs["related"].append(parent)
                roles = _allowed_roles(definition)
                if obs.get("target_match") == "supported":
                    row_type = re.match(r"^该([^，。或的]{1,12})或相关", definition)
                    classification = self._source_classification(obs, parent, definition) if roles else None
                    if (_source_role(roles, name, parent["evidence"] + direct["evidence"])
                            or row_type and name.endswith(row_type[1]) and direct["reason"] == "same_observation_binding"
                            or classification
                            or receipts and definition.strip().casefold() == str(obs.get("property_name") or "").strip().casefold()):
                        obs["associated_row"] = True
                        if classification:
                            obs["association_classification_evidence"] = [classification]
                    elif bound and any(_role_in(role, self.objects[bound]["name"]) or
                                       _role_in(role, self.objects[bound].get("scope") or "") for role in roles):
                        # The registered type is a model proposal. Keep the original
                        # observation visible without certifying that classification.
                        obs["association_scope_pending"] = True
                return
        if target["status"] == "supported":
            return
        if obs.get("name_handling") in {"related", "uncertain", "compare_names"} or obs.get("name_resolution"):
            return
        registered = self.objects.get(bound)
        if registered and (not self.same_task or not registered["is_table_subject"] or
                           self.names.get(bound) != name or registered["object_id"] != direct["id"]):
            return
        if ordinary or registered:
            obs["target"] = {**direct, "reason": "extracted_row_subject" if ordinary else "legacy_target_membership"}
        elif obs["source_kind"] != "table" and self.same_task and not self.objects and obs.get("target_match") == "supported":
            obs["extracted_subject_candidate"] = True
