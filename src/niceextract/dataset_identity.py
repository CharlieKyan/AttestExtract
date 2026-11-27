"""Local evidence for names and explicitly competing record owners."""

from collections import defaultdict
import re

from .value_location import locate_value

def literal_name(name):
    """Match a source name without splitting a Latin identifier or modifying Unicode."""
    left = r"(?<![a-zA-Z0-9_])" if re.match(r"[a-zA-Z0-9_]", name) else ""
    right = r"(?![a-zA-Z0-9_])" if re.search(r"[a-zA-Z0-9_]$", name) else ""
    return left + re.escape(name) + right


def _contains(outer, inner):
    return outer["start"] <= inner["start"] and inner["end"] <= outer["end"]


def _span(source, left, right):
    return {"start": left, "end": right, "quote": source[left:right]}


class RegisteredDesignations:
    """Join source-supported mentions to an identity field's checked name/code definition."""

    def __init__(self, source, snapshot, label_field):
        self.source = source
        self.by_id = {}
        self.by_name = defaultdict(set)
        objects = {o["object_id"]: o for o in snapshot["objects"]}
        evidence = {e["evidence_id"]: e for e in snapshot["evidence"]}
        for item in snapshot["observations"]:
            obj = objects.get(item.get("subject_id"))
            if (not obj or not label_field or item.get("record_type") != label_field
                    or not obj["is_table_subject"] or item.get("binding_status") != "supported"
                    or item.get("target_match") != "supported" or item.get("blockers")):
                continue
            label, code = item.get("entity"), item.get("value")
            if not isinstance(label, str) or not isinstance(code, str) or not label or not code or code == label:
                continue
            # A parenthesis alone is not an alias. The checked identity observation
            # must supply the code and cite the actual registered designation.
            pattern = re.escape(label) + r"\s*(?:\(" + re.escape(code) + r"\)|（" + re.escape(code) + r"）)"
            if not re.fullmatch(pattern, obj["name"]):
                continue
            bound = set(obj["mention_evidence_ids"]) & set(item.get("binding_evidence_ids", []))
            for entry in item["source_evidence"]:
                span = entry.get("span")
                if not span or not {"entity", "value"}.issubset(entry["fields"]):
                    continue
                text = source[span["start"]:span["end"]]
                hits = list(re.finditer(literal_name(obj["name"]), text))
                if len(hits) != 1:
                    continue
                start = span["start"] + hits[0].start()
                proof = _span(source, start, start + len(hits[0][0]))
                if not any(_contains(evidence[e], proof) for e in bound):
                    continue
                key = obj["object_id"]
                self.by_id[key] = (label, code, obj, proof)
                self.by_name[label].add(key)
                self.by_name[code].add(key)
        # A second registry identity with the same local spelling must stay in
        # contention even if it has no successful code assignment of its own.
        for key, obj in objects.items():
            short = re.split(r"\s*[（(]", obj["name"], maxsplit=1)[0].strip()
            if short in self.by_name:
                self.by_name[short].add(key)
            if not short or not self.by_id:
                continue
            # A code explicitly attached to a different named object in the source
            # defeats uniqueness even when that object has no accepted identity fact.
            declaration = literal_name(short) + r"[ \t]*(?:\(([^()（）\n]+)\)|（([^()（）\n]+)）)"
            for ref in obj["mention_evidence_ids"]:
                cite = evidence[ref]
                for match in re.finditer(declaration, source[cite["start"]:cite["end"]]):
                    code = (match[1] or match[2]).strip()
                    if code in self.by_name:
                        self.by_name[code].add(key)

    def _identity_evidence(self, end, obs, record, identity_mentions):
        label, code, obj, anchor = record
        for ref in end["evidence"]:
            start, stop = ref["start"], ref["end"]
            if _contains(ref, anchor):
                return [anchor]
            # Within the defining paragraph a subsequent unqualified use of the
            # registered name can continue that definition.
            if anchor["end"] <= start and re.match(literal_name(end["label"]), self.source[start:stop]):
                continuation = self.source[anchor["end"]:stop]
                definitions = re.findall(literal_name(label) + r"\s*[（(]([^()（）\n]+)[)）]", continuation)
                if "\n" not in continuation and all(value == code for value in definitions):
                    return [anchor]
            # A remote observation can independently spell the same scope: require
            # its own identity mention and a contiguous qualified designation in
            # both the citation and the checked registry's scope.
            value = str(obs["value"]).strip()
            if (end["label"] != label or not any(c.isalpha() for c in value) or obs.get("unit") or not any(
                    _contains(cite, ref) or _contains(ref, cite) for cite in identity_mentions.get(label, []))):
                continue
            scope = obj.get("scope") or ""
            # The scope supplied by the verifier is a proposal. Require a second
            # source occurrence of this qualifier beside the identity definition.
            paragraphs = list(re.finditer(r"\r?\n\s*\r?\n", self.source[:anchor["start"]]))
            nearby_start = paragraphs[-2].end() if len(paragraphs) >= 2 else 0
            near = self.source[nearby_start:anchor["start"]]
            for joint in (value + label, value + " " + label, label + " " + value):
                if joint in scope and joint in self.source[start:stop]:
                    for match in re.finditer(literal_name(value), near):
                        left, right = nearby_start + match.start(), nearby_start + match.end()
                        if left >= stop or right <= start:
                            return [anchor, _span(self.source, left, right)]
        return []

    def apply(self, obs, identity_mentions):
        for end in (obs["direct"], obs["target"]):
            key = end.get("id")
            if end["status"] != "supported" or not key:
                continue
            if key in self.by_id:
                proof = [self.by_id[key][3]]
            else:
                match = self.match(end, obs, identity_mentions) if key.startswith("mention-") else None
                if not match:
                    continue
                key, proof = match
            label, code, obj, anchor = self.by_id[key]
            end.update(id=key, scope=obj.get("scope"), identity_evidence=proof,
                       designation_span={"start": anchor["start"], "end": anchor["end"]})
            end["alias_names"] = sorted({*end.get("alias_names", []), label, code, obj["name"]})

    def match(self, endpoint, obs, identity_mentions):
        possible = self.by_name.get(endpoint["label"], ())
        if len(possible) == 1:
            key = next(iter(possible))
            if key in self.by_id and (proof := self._identity_evidence(endpoint, obs, self.by_id[key], identity_mentions)):
                return key, proof
        return None

def _continuous_members(source, definition, members, values):
    ordered = sorted(members, key=lambda m: m["start"])
    if not re.fullmatch(r"[\s，,：:；;]*", source[definition["end"]:ordered[0]["start"]]):
        return False
    for previous, following in zip(ordered, ordered[1:]):
        text = source[previous["end"]:following["start"]]
        # A new clause may add an attribute of the preceding subject. Require
        # its own value evidence; unaccounted prose cannot transport scope.
        for clause in list(re.finditer(r"[^,，;；]+", text))[1:]:
            quote = clause[0].strip()
            if not quote:
                continue
            start = previous["end"] + clause.start() + clause[0].index(quote)
            end = start + len(quote)
            if not any(left <= start and end <= right and (
                    quote in value or (start == left and end == right) or locate_value(source, value, start, end))
                    for left, right, value in values[previous["quote"]]):
                return False
        if re.search(r"(?:另[一个一]?|其他|不同)(?:批|组|对象)|(?:转向|转至|切换|改用|另指)|"
                     r"(?:in contrast|switch(?:ed)? to|instead of|another (?:group|batch))\b", text, re.I):
            return False
    return True


def _division(doc, label, definition, values):
    """A named whole divided into explicit members, followed by their prefixed shorthand.

    A proposed display string in parentheses is never parsed as evidence. Reconstruct
    the expansion from the partition and both coordinated mentions in this statement.
    """
    match = re.fullmatch(r"[^，。；\n]*?[将把](?P<parent>[\w-]{2,48}?)(?:划分为|分为|分成)"
                         r"(?P<members>[\w、-]+)", definition["quote"])
    if not match:
        return None
    before = max(doc.source.rfind(c, 0, definition["start"]) for c in "。！？；，\n") + 1
    if re.search(r"并非|不是|未将|反对|否认|没有|假如|假设|假定|例如|是否|[“\"]",
                 doc.source[before:definition["end"]]):
        return None
    parent = match["parent"]
    members = re.split(r"[和与及、]", match["members"])
    if len(members) < 2 or len(set(members)) != len(members) or not all(members):
        return None
    statement = re.match(r"[^。！？\n]{1,1000}", doc.source[definition["end"]:])
    if not statement:
        return None
    tail = statement[0]
    for member in members:
        prefix = label.removesuffix(member)
        if prefix == label or not prefix or not parent.startswith(prefix):
            continue
        names = [prefix + role for role in members]
        # Each participant introduces its own clause. A mere list of words elsewhere
        # in the paragraph cannot establish a scoped abbreviation.
        positions = [list(re.finditer(r"(?:^|[，,；;])\s*(" + re.escape(name) + r")", tail)) for name in names]
        if any(len(p) != 1 for p in positions):
            continue
        named = [_span(doc.source, definition["end"] + p[0].start(1), definition["end"] + p[0].end(1))
                 for p in positions]
        if _continuous_members(doc.source, definition, named, values):
            return {n: parent + role for n, role in zip(names, members)}, [definition, *named], parent
    return None


def partition_lookups(doc, spans):
    """Additional lookup labels for the exact original members of a reported partition."""
    identities, cited = defaultdict(list), defaultdict(list)
    values = defaultdict(list)
    for obs in doc.observations:
        name = obs.get("entity")
        if not isinstance(name, str) or not name or obs.get("name_handling") != "as_reported":
            continue
        owners = spans(doc.source, obs, "entity")
        mentions = []
        for owner in owners:
            matches = list(re.finditer(literal_name(name), owner["quote"]))
            if len(matches) == 1:
                mentions.append((owner["start"] + matches[0].start(), name))
        if not mentions:
            continue
        evidence = spans(doc.source, obs)
        if obs["record_type"] in doc.identity_fields:
            if obs["value"] == name and obs.get("binding_status") == "as_extracted" and not obs.get("blockers"):
                for pos, label in mentions:
                    identities[(pos, label)].extend(s for s in evidence if s["start"] <= pos < s["end"])
            continue
        cites = [(s["start"], s["end"], str(obs["value"])) for s in evidence]
        if not cites:
            # Archived extractors could miss a Markdown escape in an intervening clause.
            # Match the entire quoted clause inside the sentence of its attested owner.
            # This verifies continuity only; it never changes that measurement's checks.
            for owner in owners:
                sentence = doc.sentence(owner["start"], owner["end"])
                if not sentence:
                    continue
                for ref in obs.get("source_evidence", []):
                    if ref.get("location_status") == "unmatched" and "value" in ref.get("fields", []):
                        located = locate_value(doc.source, ref.get("quote"), sentence["start"], sentence["end"])
                        if located:
                            cites.append((located["start"], located["end"], str(obs["value"])))
        values[name].extend(cites)
        if obs.get("binding_status") == "as_extracted" and not obs.get("blockers"):
            for key in mentions:
                member = _span(doc.source, key[0], key[0] + len(name))
                for s in evidence:
                    # Only the assignment tied to this specific occurrence gains a lookup name.
                    if not any(_contains(o, member) and (_contains(o, s) or _contains(s, member)) for o in owners):
                        continue
                    value = locate_value(doc.source, obs["value"], s["start"], s["end"])
                    if value:
                        cited[key].append((obs["observation_id"], value))
    definitions = set()
    for (pos, name), sources in identities.items():
        for source in sources:
            for clause in re.finditer(r"[^，,。；;\n]+", source["quote"][:pos - source["start"]]):
                definitions.add((name, source["start"] + clause.start(), source["start"] + clause.end()))
    # Verification may have quoted the partition even when the extractor did
    # not emit a separate name field for its members. The citation can identify
    # the local partition; the model's proposed direction does not prove it.
    for edge in doc.analysis.get("relations", []):
        if edge["predicate"] not in {"part_of", "member_of"}:
            continue
        for span in doc.entries(edge, "evidence_ids"):
            sentence = doc.sentence(span["start"], span["end"])
            if sentence:
                definitions.update((name, span["start"], span["end"]) for pos, name in cited
                                   if span["end"] <= pos < sentence["end"])
    findings = defaultdict(dict)
    for name, start, end in sorted(definitions):
        group = _division(doc, name, _span(doc.source, start, end), values)
        if not group:
            continue
        labels, proof, parent = group
        if re.search(r"[和与及、]", parent):
            continue
        member_keys = [(s["start"], s["quote"]) for s in proof[1:]]
        if not all(identities.get(k) or cited.get(k) for k in member_keys):
            continue
        positions = sorted(k[0] for k in member_keys)
        for key in member_keys:
            statement = doc.sentence(key[0], key[0] + len(key[1]))
            if not statement:
                continue
            end = min([statement["end"], *(p for p in positions if p > key[0])])
            for oid, value in cited[key]:
                if key[0] + len(key[1]) <= value["start"] and value["end"] <= end:
                    # Preserve the rest of the original clause, including qualifiers after the value.
                    tail = re.match(r"[^，,；;。！？\n]*", doc.source[value["end"]:end])
                    claim = _span(doc.source, key[0], value["end"] + tail.end())
                    findings[oid][labels[key[1]]] = {"mention": key[1], "label": labels[key[1]],
                                                     "evidence": proof, "claim": claim}
    return {oid: next(iter(options.values())) for oid, options in findings.items() if len(options) == 1}


def record_names(doc, value_spans):
    """Index a task identity field only when its literal value was linked to a proposed object."""
    records = defaultdict(list)
    values = defaultdict(list)
    for obs in doc.observations:
        if (isinstance(obs.get("entity"), str) and obs.get("value") is not None
                and obs["record_type"] not in doc.identity_fields and obs.get("binding_status") == "supported"):
            values[obs["entity"]].extend((e["start"], e["end"], str(obs["value"])) for e in value_spans(doc.source, obs))
    for obs in doc.observations:
        obj = doc.objects.get(obs.get("subject_id"))
        name = obs.get("value")
        if (obs.get("record_type") not in doc.identity_fields or not obj or not obj.get("is_table_subject")
                or obs.get("binding_status") != "supported" or obs.get("target_match") != "supported"
                or obs.get("blockers") or not isinstance(name, str) or name != obs.get("entity")
                or not name or len(name) > 500):
            continue
        for evidence in value_spans(doc.source, obs):
            if name not in evidence["quote"]:
                continue
            mentions = doc.entries(obj, "mention_evidence_ids")
            if not any(name in m["quote"] for m in mentions):
                continue
            bindings = doc.entries(obs, "binding_evidence_ids")
            entries = [evidence, *mentions, *bindings]
            division = None
            for binding in bindings:
                division = _division(doc, name, binding, values)
                if division and any(_contains(evidence, n) and n["quote"] == name for n in division[1][1:]):
                    break
                division = None
            if division:
                labels, identity_proof, parent = division
                display = labels[name]
            elif obj["name"].endswith(name) and any(obj["name"] in e["quote"] for e in entries):
                # Use the short name for this bound observation. A substring of
                # a model label alone cannot establish a generally valid alias.
                display, identity_proof, parent = name, entries, None
            else:
                # An opaque model label cannot authorize a normalized display
                # name. The exact identity cell/clause still supports a local
                # name at this occurrence, even before any alias is resolved.
                if not evidence["quote"].startswith(name) or not any(
                        e["start"] == evidence["start"] and e["quote"].startswith(name) for e in mentions):
                    continue
                display, identity_proof, parent = None, [evidence], None
            key = (obj["object_id"], name)
            records[key].append((display, identity_proof, parent))
    return records


def _row_type_suffix(subject, name, row_subject):
    """A stated target class may extend an attested identity ('Pin A specimen')."""
    if not subject.startswith(name):
        return False
    extra = subject[len(name):].strip()
    if not extra or not re.fullmatch(r"[\w-]{2,32}", extra):
        return False
    positive = re.split(r"[;；。.!?\n]|\bexcept\b|\bexcluding\b|不含|排除", row_subject, maxsplit=1, flags=re.I)[0]
    return bool(re.search(r"(?:具体|明确命名的|individual |named |each )" + re.escape(extra)
                          + r"(?=$|[，、或及， ,]|s\b)", positive, re.I))


def source_bound_name(doc, item, spans, obj, bindings, uncertain):
    raw = item.get("entity")
    if (not isinstance(raw, str) or not raw or not obj or item.get("binding_status") != "supported"
            or not bindings or obj["object_id"] in doc.disputed):
        return None
    scope = doc.scope(spans[0]["start"])
    candidates = []
    for (key, name), records in doc.name_records.items():
        if not (raw == name or _row_type_suffix(raw, name, doc.analysis.get("table_subject") or "")):
            continue
        for display, proof, parent in records:
            if not any(doc.scope(e["start"]) == scope for e in proof):
                continue
            if parent and not any(n["quote"] == name and n["start"] == b["start"]
                                  for n in proof[1:] for b in bindings):
                continue
            candidates.append((key, name, display, proof, parent))
    if {c[0] for c in candidates} != {obj["object_id"]}:
        return None
    _, name, display, proof, parent = max(candidates, key=lambda x: (len(x[1]), x[2] is not None))
    displays = {c[2] for c in candidates if c[1] == name and c[2] is not None}
    if len(displays) > 1:
        return None
    competing = [o for label in {name, display} if label for o in doc.same_section_objects(label, scope)]
    if any(o["object_id"] != obj["object_id"] for o in competing):
        return None
    for b in bindings:
        assertion = b["quote"].strip()
        if not assertion.startswith(raw) or uncertain.search(assertion):
            continue
        if not any(_contains(b, s) for s in spans):
            continue
        if display is None:
            local = [e for _, n, shown, anchors, _ in candidates if n == name and shown is None
                     for e in anchors if e["start"] == b["start"] and e["quote"].startswith(name)]
            if not local:
                continue
            proof = local
        # A leading name inside a longer possessive/compound subject may not take
        # its component's measurement. Require the source to state the extracted
        # attribute or the literal descriptive value immediately after this owner.
        continuation = assertion[len(raw):]
        if re.match(r"[a-zA-Z0-9_]$", raw[-1]) and re.match(r"[a-zA-Z0-9_]", continuation):
            continue
        rest = continuation.lstrip()
        prop = (item.get("property_name") or "").removeprefix(raw)
        value = "" if item.get("value") is None else str(item["value"])
        if item.get("record_type") not in doc.identity_fields:
            text = rest.removeprefix("的").strip()
            # Some claims retain the complete source predicate as conditions
            # while using a shortened display label for the property.
            condition = item.get("conditions") or ""
            described = re.match(r"(?i:is\b|was\b|has\b|shows?\b)|呈|为|以|表现|主要|含有", value)
            if not ((prop and text.startswith(prop)) or (described and rest.startswith(value))
                    or (raw != name and len(prop) >= 2 and value and value in condition
                        and prop[-2:] in condition.split(value, 1)[0] and text.startswith(condition))):
                continue
        after = b["end"]
        # Check the whole clause so a truncated binding cannot hide negation.
        left = max(doc.source.rfind(c, 0, b["start"]) for c in "，,；;。!?！？\n") + 1
        clause = re.match(r"[^，,；;。!?！？\n]*", doc.source[after:])
        if doc.source[left:b["start"]].strip() or uncertain.search(doc.source[left:after] + clause[0]):
            continue
        if any(n != raw and n != name and n not in raw and n in rest
               for n in doc.names if n and n != obj["name"]):
            continue
        key = obj["object_id"] if display else doc.local_id("named_occurrence", [b["start"], name])
        result = {"status": "supported", "id": key, "label": name, "scope": scope,
                  "basis": "source_record_identity", "reason": "source_bound_name",
                  "display_name": display or name, "alias_names": [display] if display and display != name else [],
                  "identity_evidence": proof, "evidence": [b, *proof]}
        if parent:
            # Preserve the parent in this particular subdivision, even when
            # several registered parent objects share its unqualified name.
            parent_id = doc.local_id("partition", [proof[0]["start"], parent])
            result["parent"] = {"status": "supported", "id": parent_id, "label": parent, "scope": scope,
                                "basis": "source_division", "reason": "part_of", "source_id": key,
                                "target_id": parent_id, "evidence": proof}
        return result
    return None


def competing_targets(doc, item, spans, uncertain):
    """Recognize explicit alternatives in a single quantity assertion, or an ambiguous bare name."""
    prop, value = item.get("property_name"), item.get("value")
    if not prop or value is None or not spans:
        return []
    owners = defaultdict(dict)
    for obj in doc.objects.values():
        if obj.get("is_table_subject"):
            names = [obj["name"]]
            origin = str(item.get("entity") or "").strip()
            qualifier = str(obj.get("scope") or "").strip()
            mentions = doc.entries(obj, "mention_evidence_ids")
            if (origin and qualifier and obj["name"] in {qualifier + origin, qualifier + " " + origin}
                    and any(obj["name"] in e["quote"] for e in mentions)):
                names.append(origin)
            for name in names:
                owners[name][obj["object_id"]] = (qualifier, mentions)
    for (key, name) in doc.name_records:
        obj = doc.objects[key]
        owners[name][key] = (obj.get("scope"), doc.entries(obj, "mention_evidence_ids"))
    scope = doc.scope(spans[0]["start"])
    for name, mentions in doc.identities.items():
        citations = [e for e in mentions if doc.scope(e["start"]) == scope]
        if citations and name not in owners:
            owners[name][doc.local_id("candidate_name", [scope, name])] = (scope, citations)
    for span in spans:
        sentence = doc.sentence(span["start"], span["end"])
        if not sentence or uncertain.search(sentence["quote"]):
            continue
        quote = sentence["quote"].strip().rstrip(".。")
        # Keep the predicate and its reported value together. An unrelated
        # comparison in the same sentence does not license either endpoint.
        predicate = re.search(re.escape(prop) + r"\s*(?:is|was|of|=|:|：|为|是)?\s*"
                              + re.escape(str(value)) + r"(?!\d)", quote, re.I)
        if not predicate:
            continue
        pre = quote[:predicate.start()].strip()
        names = [name for name in owners if name and name in pre]
        names = [name for name in names if not any(name != longer and name in longer for longer in names)]
        if len(names) == 1 and pre in {names[0], names[0] + "的", names[0] + "'s"} and len(owners[names[0]]) > 1:
            selected = item.get("subject_id")
            if scope != "document" and selected in owners[names[0]] and item.get("binding_status") == "supported":
                heading_start = int(scope.split("@", 1)[1])
                heading = doc.source[heading_start:].split("\n", 1)[0].strip().strip("#").strip()
                scoped_keys = {key for key, (qualifier, refs) in owners[names[0]].items()
                               if qualifier and heading.casefold() == qualifier.strip().casefold()
                               and any(doc.scope(e["start"]) == scope for e in refs)}
                if scoped_keys == {selected} and any(_contains(sentence, e) and _contains(e, span)
                                                     for e in doc.entries(item, "binding_evidence_ids")):
                    continue
            basis = "source_scope_candidates"
        elif len(names) == 2:
            first, second = sorted(names, key=pre.index)
            a, b = re.escape(first), re.escape(second)
            if not re.fullmatch(rf"(?:(?:either\s+)?{a}\s+or\s+{b}(?:'s)?"
                                rf"|(?:one of\s+){a}\s+and\s+{b}"
                                rf"|{a}\s*(?:或|或者)\s*{b}(?:中的一个|之一)?的?"
                                rf"|{a}\s*(?:和|与)\s*{b}(?:中的一\w{{0,4}}|之一|[^。；，,]{{0,20}}[，,]\s*其中一\w{{0,4}})的?)",
                                pre, re.I):
                continue
            basis = "source_alternatives"
        else:
            continue
        return [{"status": "unknown", "id": key, "label": name, "basis": basis,
                 "scope": qualifier, "reason": "competing_source_subjects",
                 "evidence": [sentence, *citations]}
                for name in sorted(names) for key, (qualifier, citations) in sorted(owners[name].items())]
    return []
