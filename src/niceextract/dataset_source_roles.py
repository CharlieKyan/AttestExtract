"""Reconcile independently cited context, model assignments and parameter cells."""

from collections import defaultdict
import re

from .dataset_identity import literal_name
from .dataset_resolution_table import reconcile_table_unit
from .dataset_shared_settings import apply_shared_settings
from .value_location import locate_value


_MODEL = re.compile(r"\b(?:model|simulation|simulated)\b|模型|仿真", re.I)
_ASSIGN = re.compile(r"(?:设[定置]?为|\s+(?:was |is )?(?:set|configured) (?:at|to))\s*$", re.I)
_NEGATIVE = re.compile(r"并非|不(?:将|应|是|为|设)|未(?:曾)?设|\b(?:not|never|rather than)\b", re.I)
_CAPTION = re.compile(r"^\s*(?:table|表)\s*(\d+)\b", re.I | re.M)


def _context_fields(doc, spans):
    """A cited value in a task-defined context field supplies a role, not a new row."""
    roles = defaultdict(list)
    for obs in doc.observations:
        value = obs.get("value")
        field = obs["record_type"]
        if not isinstance(value, str) or not value or field in doc.identity_fields or obs.get("blockers"):
            continue
        definition = doc.analysis["task"]["fields"].get(field, "")
        if not re.search(r"所属的?.{0,20}名|\b(?:location|site|region|facility) (?:name|of)\b", definition, re.I):
            continue
        for span in spans(doc.source, obs):
            if value in span["quote"] and (obs.get("entity") == value or obs.get("binding_status") == "supported"):
                roles[value].append(span)
    return roles


def _context_at_occurrence(doc, name, citations, roles):
    if not name or not citations or name not in roles:
        return []
    scopes = {doc.scope(c["start"]) for c in citations}
    if any(doc.scope(c["start"]) in scopes for c in doc.identities.get(name, [])):
        return []
    for role in roles[name]:
        if any(c["start"] < role["end"] and role["start"] < c["end"] for c in citations):
            return [role]
        # A location mentioned elsewhere has no authority over this occurrence.
        # A second, qualified object appearing in BOTH the context-field evidence
        # and this local section can flag competing roles; it cannot merge IDs.
        qualified = [label for label in doc.labels if label.startswith(name)
                     and len(label.strip()) >= len(name) + 2 and label in role["quote"]]
        for label in sorted(qualified, key=len, reverse=True):
            for m in re.finditer(literal_name(label), doc.source):
                if doc.scope(m.start()) in scopes:
                    return [role, {"start": m.start(), "end": m.end(), "quote": m[0]}]
    return []


def _model_assignment(doc, obs, spans, definition):
    """Match a locally named, explicitly assigned value against its registered occurrence."""
    name = obs.get("entity")
    if not name or obs.get("blockers") or obs.get("target_match") == "unsupported" or obs.get("row_relations"):
        return None
    matches = {}
    for citation in spans(doc.source, obs):
        text = citation["quote"]
        value = locate_value(doc.source, obs["value"], citation["start"], citation["end"])
        if not value or _NEGATIVE.search(text) or len(re.findall(literal_name(name), text)) != 1:
            continue
        prefix = doc.source[citation["start"]:value["start"]]
        marker = _ASSIGN.search(prefix)
        if not marker or name not in prefix[:marker.start()]:
            continue
        # In a shared-subject list, the next clause must start at a task property.
        # A new name between the separator and the property changes the owner.
        conditions = re.split(r"[,，]", prefix[:marker.start()].split(name, 1)[1])
        candidate = conditions[-1].strip().removeprefix("的")
        candidate = re.sub(r"^['’]s\s+", "", candidate, flags=re.I).strip()
        if (len(conditions) > 2 or not candidate or candidate.casefold() not in definition.casefold()
                or re.search(r"[。!?！？；;\n]", prefix)):
            continue
        for key, obj in doc.objects.items():
            if (name not in obj["name"] or not obj["is_table_subject"] or key in doc.disputed
                    or obs.get("subject_id") not in (None, key)):
                continue
            for mention in doc.entries(obj, "mention_evidence_ids"):
                if (mention["start"] <= citation["start"] and citation["end"] <= mention["end"]
                        and _MODEL.search(text[:text.index(name)]) and _MODEL.search(obj["name"])):
                    matches[key] = obj, mention, candidate, citation
    return next(iter(matches.values())) if len(matches) == 1 else None


def _label_and_decision(obj, name, proof):
    match = re.search(r"(?:模型(?:中(?:的)?)?|仿真(?:的)?|(?:model|simulated)\s+)" + re.escape(name),
                      proof["quote"], re.I)
    label = match[0] if match else name
    alternate = obj["name"].split("（", 1)[0].split("(", 1)[0].strip()
    return {"status": "supported", "id": obj["object_id"], "label": label, "scope": obj["scope"],
            "basis": "cited_model_assignment", "reason": "registered_model_parameter",
            "evidence": [proof], "alias_names": list(dict.fromkeys((name, label, alternate)))}


def _table_reference(source, start, mention, caption_pattern=_MODEL):
    """Require the parameter paragraph to refer to this table, with a matching local caption."""
    before = source[:start].rstrip()
    lines = before.splitlines()
    captions = []
    for line in reversed(lines[-3:]):
        if not line.strip():
            if captions:
                break
            continue
        if not _CAPTION.match(line):
            break
        captions.append(line)
    numbers = {m[1] for line in captions for m in _CAPTION.finditer(line)}
    matching = next((line for line in captions if caption_pattern.search(line)), None)
    if len(numbers) != 1 or matching is None or mention["end"] >= start:
        return None
    end = source.find("\n", mention["end"])
    end = min(start, end if end >= 0 else start)
    text = source[mention["end"]:end]
    pattern = r"(?:\btable|表)\s*" + numbers.pop() + r"(?!\d)"
    match = re.search(pattern, text, re.I)
    if not match:
        return None
    left = before.rfind(matching)
    return [
        {"start": mention["end"] + match.start(), "end": mention["end"] + match.end(), "quote": match[0]},
        {"start": left, "end": left + len(matching), "quote": matching},
    ]


def _allows_settings(definition):
    if re.search(r"(?:不含|排除|不收|不接受|不允许)(?:任何)?(?:模型|仿真|模拟|设定|设定值|设置值)|"
                 r"\b(?:exclude[sd]?|excluding|no|not|without)\s+(?:any |all )?(?:model|simulat|settings?)", definition, re.I):
        return False
    if re.search(r"\b(?:measured|observed)(?: values?)? only\b|仅(?:限)?(?:实测|现场)|实测专用", definition, re.I):
        return False
    return (not re.search(r"\b(?:measured|in[ -]situ|observed)\b|实测|现场(?:测量|观测)", definition, re.I)
            or bool(_MODEL.search(definition)))


def _record_reference_parameter(doc, obs, entry, spans):
    # A boundary, component or fixture used for a setting need not be a task row.
    for span in spans(doc.source, obs):
        label = re.search(r"(?:将|把)([^，。；;\n]{1,60}?)设[置定]为"
                          r"|,\s*([^,.;\n]{1,60}?) (?:was|is) set to\b", span["quote"], re.I)
        if label:
            entry["setting_parameter"] = (label[1] or label[2]).strip()
            entry["setting_evidence"] = span
            return


def apply_source_roles(doc, resolved, spans, row_subject):
    roles = _context_fields(doc, spans) if doc.identity_fields else {}
    model_rows = _MODEL.search(row_subject) and _allows_settings(row_subject)
    assignments = []
    for obs in doc.observations:
        entry = resolved[obs["observation_id"]]
        name = obs.get("entity")
        if entry["purpose"] == "derived_setting":
            _record_reference_parameter(doc, obs, entry, spans)
        if not obs.get("subject_id") and entry["target"].get("membership_basis") != "source_identity":
            context = _context_at_occurrence(doc, name, spans(doc.source, obs), roles)
            if context:
                entry["context_role_evidence"] = context
        if obs.get("source_kind") == "table":
            continue
        definition = doc.analysis["task"]["fields"].get(obs["record_type"], "")
        if entry["fit"]["status"] == "contradicted":
            continue
        setting = _model_assignment(doc, obs, spans, definition)
        if setting:
            obj, mention, prop, citation = setting
            entry.update(purpose="model_input", measurement_use="model_input")
            if not _allows_settings(definition):
                entry["fit"] = {**entry["fit"], "status": "contradicted", "reason": "model_setting_for_measured_field",
                                "evidence": [citation]}
                continue
            if not model_rows or entry["fit"]["status"] != "supported":
                continue
            decision = _label_and_decision(obj, name, mention)
            entry.update(direct={**decision, "label": name}, target=decision)
            assignments.append((obs, obj, mention, prop))

    if model_rows:
        _reconcile_model_table(doc, resolved, assignments)
    if doc.identity_fields and _allows_settings(row_subject):
        apply_shared_settings(doc, resolved, spans, bool(model_rows), _allows_settings, _table_reference)


def _reconcile_model_table(doc, resolved, assignments):
    parameter_cells = defaultdict(set)
    for record in resolved.values():
        path = record.get("table_context") or {}
        if cell := path.get("combined_parameter"):
            parameter_cells[(cell["table_start"], cell["subject"], cell["property"])].add(path["value"]["ref"])
    for obs in doc.observations:
        entry = resolved[obs["observation_id"]]
        path = entry.get("table_context") or {}
        cell = path.get("combined_parameter")
        if not cell or path.get("gap") or path.get("qualifier_gap") or obs.get("target_match") == "unsupported":
            continue
        if len(parameter_cells[(cell["table_start"], cell["subject"], cell["property"])]) != 1:
            continue
        if obs.get("conditions") and obs["conditions"] != cell["property"]:
            continue
        if reconcile_table_unit(obs, path).get("blockers"):
            continue
        definition = doc.analysis["task"]["fields"].get(obs["record_type"], "")
        if not _allows_settings(definition) or entry["fit"]["status"] == "contradicted":
            continue
        # The same field/value in two experiments is insufficient. Require the object's
        # paragraph to refer to this table and the cell to name its subject and property.
        peers = [(prose, proof) for prose, obj, proof, prop in assignments
                 if (prose["record_type"] == obs["record_type"] and prose["value"] == obs["value"]
                     and prose.get("unit") == obs.get("unit") and re.search(literal_name(cell["subject"]), prose["entity"])
                     and cell["property"] == prop and obs.get("subject_id") in (None, obj["object_id"]))]
        if len(peers) == 1:
            prose, proof = peers[0]
            ref = _table_reference(doc.source, cell["table_start"], proof)
            if ref:
                entry["corroborates"] = prose["observation_id"]
                primary = resolved[prose["observation_id"]]
                primary.setdefault("corroborating_observations", []).append(obs["observation_id"])
                primary["fit"] = {**primary["fit"], "evidence": [*primary["fit"]["evidence"],
                                  *ref, cell["parameter"], path["value"]]}
                entry.update(direct=primary["direct"], target=primary["target"], purpose="model_input",
                             measurement_use="model_input")
                continue
        obj = doc.objects.get(obs.get("subject_id"))
        if (not obj or not obj["is_table_subject"] or obs.get("binding_status") != "supported"
                or obs.get("target_match") != "supported" or cell["subject"] not in obj["name"]):
            continue
        bindings = doc.entries(obs, "binding_evidence_ids")
        if not any(b["start"] == cell["parameter"]["start"] and b["end"] == cell["parameter"]["end"] for b in bindings):
            continue
        for proof in doc.entries(obj, "mention_evidence_ids"):
            ref = _table_reference(doc.source, cell["table_start"], proof)
            if (ref and _MODEL.search(obj["name"]) and any(proof["start"] <= b["start"] < b["end"] <= proof["end"]
                    and cell["property"] in b["quote"] for b in bindings)):
                label = obj["name"].split("（", 1)[0].split("(", 1)[0].strip()
                source_names = [name for name in doc.labels if isinstance(name, str) and cell["subject"] in name
                                and name in label and name in proof["quote"]]
                name = max(source_names, key=len, default=cell["subject"])
                decision = _label_and_decision(obj, name, proof)
                decision["evidence"].extend([*ref, cell["parameter"], path["value"]])
                entry.update(direct={**decision, "label": cell["subject"]}, target=decision, purpose="model_input",
                             measurement_use="model_input")
                break
