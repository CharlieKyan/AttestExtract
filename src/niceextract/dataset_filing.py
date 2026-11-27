"""Carry model-proposed owners and filing relationships to the reader's annotated view."""

from bisect import bisect_right
from collections import defaultdict
import re

from .dataset_citation import TableCitations, check_reading
from .dataset_identity import literal_name
from .evidence import is_missing
from .execution import digest
from .filing_evidence import filing_citation_problem


def _endpoint(key, label, evidence=(), scope=None, aliases=()):
    return {"id": key, "label": label, "status": "supported", "scope": scope,
            "evidence": list(evidence), "reason": "model_filing", "basis": "model_proposed",
            "alias_names": list(dict.fromkeys(name for name in aliases if name and name != label))}


class FilingContext:
    """Only a model-declared relationship can file a reading under a different object."""

    def __init__(self, source, snapshot, sentence_ends, tables=(), *, statement_protocol=None):
        self.source = source
        self.current = statement_protocol == "source-v2"
        self.row_subject = snapshot.get("table_subject")
        self.table_citations = TableCitations(source, tables)
        self.sentence_ends = sentence_ends
        self.objects = {o["object_id"]: o for o in snapshot["objects"]}
        self.owner_names = tuple({o["name"] for o in self.objects.values()} |
                                 {o["entity"] for o in snapshot["observations"] if o.get("entity")})
        self.citations = {e["evidence_id"]: e for e in snapshot["evidence"]}
        self.edges = defaultdict(list)
        self.names = defaultdict(list)
        self.local_bindings = defaultdict(list)
        for relation in snapshot["relations"]:
            if relation["predicate"] in {"part_of", "sample_of", "member_of"}:
                self.edges[relation["source_id"]].append(relation)
        for item in snapshot["observations"]:
            bound, name = item.get("subject_id"), item.get("entity")
            if bound not in self.objects or item["binding_status"] != "supported" or not name:
                continue
            if re.search(literal_name(name), self.objects[bound]["name"]):
                self.names[bound].append(name)
            self.local_bindings[name].append((bound, item))

    def object_endpoint(self, key, label=None):
        obj = self.objects[key]
        names = list(dict.fromkeys(self.names[key]))
        display = label or (names[0] if len(names) == 1 else obj["name"])
        return _endpoint(key, display, [self.citations[k] for k in obj["mention_evidence_ids"]],
                         obj.get("scope"), [obj["name"], *names])

    def _context_bound(self, obs):
        """Reuse a bound reading with the same local owner in the same original sentence."""
        spans = [e["span"] for e in obs["source_evidence"] if e.get("span") and "entity" in e["fields"]]
        matches = set()
        for key, other in self.local_bindings[obs["entity"]]:
            for ref in other["source_evidence"]:
                other_span = ref.get("span")
                if not other_span or "entity" not in ref["fields"]:
                    continue
                for span in spans:
                    start = min(span["start"], other_span["start"])
                    end = max(span["end"], other_span["end"])
                    boundary = bisect_right(self.sentence_ends, start)
                    if boundary >= len(self.sentence_ends) or end > self.sentence_ends[boundary]:
                        continue
                    sentence = self.source[self.sentence_ends[boundary - 1]:self.sentence_ends[boundary]]
                    if (len(re.findall(literal_name(obs["entity"]), sentence)) == 1
                            and not any(c in self.source[start:end - 1] for c in "；;\n")):
                        matches.add(key)
        return next(iter(matches)) if len(matches) == 1 else None

    def _receipt_subjects(self, obs, receipt):
        label = receipt["target"]
        proposed = [k for k, obj in self.objects.items() if label == obj["name"] or label in self.names[k]]
        spans = [e for e in receipt.get("evidence", []) if isinstance(e.get("start"), int)
                 and isinstance(e.get("end"), int)]
        matches = []
        for key in proposed:
            registered = self.object_endpoint(key, label)
            if any(s["start"] < other["end"] and other["start"] < s["end"]
                   for s in spans for other in registered["evidence"]):
                matches.append(registered)
        if len(matches) != 1:
            if len(proposed) > 1:
                matches = [self.object_endpoint(k, label) for k in proposed]
                for item in matches:
                    item["status"] = "unknown"
            else:
                local_id = "row-" + digest([obs["observation_id"], label])[:24]
                matches = [_endpoint(local_id, label, spans)]
        for item in matches:
            if "explanation" in receipt:
                item["explanation"] = receipt["explanation"]
                item["target_contains_direct"] = receipt.get("target_contains_direct")
                if receipt.get("target_source_name"):
                    item["target_source_name"] = receipt["target_source_name"]
                problem = filing_citation_problem(self.source, receipt, obs,
                    registered_mentions=item["evidence"] if item["id"] in self.objects else ())
                if problem:
                    item.update(status="unknown", filing_citation_reason=problem, filing_citation_status="unverified")
            item.update(reason=receipt.get("explanation") or receipt.get("predicate"), field_applicability=receipt["field_applicability"],
                        relation_evidence=spans, statement_basis=receipt.get("target_basis") or "unknown")
        return matches

    def apply(self, obs, same_task, policies):
        if self.current:
            obs["filing_protocol"] = "explanation-v1"
        obs.update(check_reading(self.source, obs, owners=self.owner_names, table_citations=self.table_citations))
        if (obs["citation_status"] == "unverified" and obs.get("binding_status") == "supported"
                and obs.get("binding_evidence_ids") and obs["source_kind"] == "prose"):
            refs = []
            for key in obs["binding_evidence_ids"]:
                span = self.citations[key]
                refs.append({"fields": ["value"], "span": span, "quote": span["quote"]})
            repair = check_reading(self.source, {**obs, "source_evidence": refs},
                                   owners=self.owner_names, allow_search=False)
            if repair["citation_status"] == "located":
                obs["original_citation_check"] = {k: obs[k] for k in repair}
                obs.update(repair, citation_reason="引文已找回（模型查证所引原文）")
        obs["statement_basis"] = obs.get("statement_basis") or "unknown"
        if obs.get("reported_use") not in (None, "unspecified"):
            obs["measurement_use"] = obs["reported_use"]
        obs["filing_subjects"] = []
        if self.current:
            if any(b.get("code") == "value_only_in_context" for b in obs.get("blockers", [])):
                obs.update(filing_suppressed=True, eligible=False, projection_reason="value_only_in_context")
                return
        if (not same_task or obs.get("corroborates") or not obs.get("entity")
                or is_missing(obs.get("value")) or obs["field"] in policies and
                any(reason in policies[obs["field"]] for reason in obs.get("fit_blockers", []))):
            obs["filing_suppressed"] = True
            return
        name = obs["entity"]
        bound = obs.get("subject_id") if obs.get("binding_status") == "supported" else None
        if not bound and self.edges and obs["source_kind"] == "prose":
            bound = self._context_bound(obs)
        direct = obs["direct"]
        if bound in self.objects:
            direct = self.object_endpoint(bound, name)
        elif direct.get("status") != "supported" or not direct.get("id"):
            direct = _endpoint("mention-" + obs["observation_id"], name,
                               [e for e in obs.get("evidence", []) if name in e["quote"]])
        old_target = obs["target"]
        receipts = obs.get("row_relations") or []
        choices = []
        if self.current and any(b.get("code") == "value_owner_conflict" for b in obs.get("blockers", [])):
            proof = [self.citations[k] for k in obs.get("binding_evidence_ids", [])]
            checked = check_reading(self.source, {**obs, "source_evidence": [
                {"fields": ["value"], "span": s, "quote": s["quote"]} for s in proof]}, allow_search=False)
            verified = self.object_endpoint(bound) if bound in self.objects else {}
            material = {"target": verified.get("label"), "status": "located", "evidence": proof}
            if (not verified or checked["citation_status"] != "located" or
                    filing_citation_problem(self.source, material, {**obs, "entity": verified.get("label")})):
                reason = "数值引文点名了另一直接对象，归属待定"
                obs.update(direct={**direct, "status": "unknown", "reason": "value_owner_conflict"},
                           target={**old_target, "id": None, "label": None, "status": "unknown", "reason": "value_owner_conflict"},
                           eligible=False, associated_row=False, filing_disagreement={"reason": reason},
                           filing_subjects=[], direct_subject_status="conflicting_evidence")
                return
            direct = verified
            obs["reviewed_direct_name"] = direct["label"]
        if self.current and obs.get("reviewed_row_relations"):
            for rel in obs["reviewed_row_relations"]:
                target = self.object_endpoint(rel["target_id"])
                proof = [self.citations[k] for k in rel["evidence_ids"]]
                for receipt in receipts:
                    if (not receipt.get("alternative") and receipt.get("target") in (target["label"], *target["alias_names"])
                            and receipt.get("field_applicability") == rel["field_applicability"]):
                        for span in receipt.get("evidence", []):
                            if isinstance(span.get("start"), int) and span not in proof:
                                proof.append(span)
                target.update(explanation=rel["explanation"], reason=rel["explanation"],
                              relation_evidence=proof,
                              statement_basis=rel["target_basis"], field_applicability=rel["field_applicability"],
                              target_contains_direct=rel.get("target_contains_direct"))
                problem = filing_citation_problem(self.source, {"target": target["label"], "status": "located",
                    "evidence": target["relation_evidence"]}, obs, registered_mentions=target["evidence"])
                if problem:
                    target.update(status="unknown", filing_citation_reason=problem, filing_citation_status="unverified")
                choices.append(target)
            if receipts and {r["target"] for r in receipts} != {c["label"] for c in choices}:
                obs["filing_disagreement"] = {"first_pass": receipts, "review": obs["reviewed_row_relations"]}
        elif receipts:
            for r in receipts:
                choices.extend(self._receipt_subjects(obs, r))
        elif self.current and obs.get("row_relation_issues"):
            obs["filing_disagreement"] = {"reason": "归属说明未能读取", "original": obs["row_relation_issues"]}
        elif self.edges.get(bound):
            for rel in self.edges[bound]:
                target = self.object_endpoint(rel["target_id"])
                target.update(reason=rel["predicate"], relation_id=rel["relation_id"],
                              relation_evidence=[self.citations[k] for k in rel["evidence_ids"]])
                choices.append(target)
        elif bound in self.objects and self.objects[bound]["is_table_subject"]:
            choices.append(self.object_endpoint(bound))
        elif self.current and (obs.get("name_handling") == "as_reported"
                               or not self.row_subject and not obs.get("filing_review_reason")):
            choices.append(direct)
        elif self.current and (obs.get("filing_review_reason") or obs.get("name_handling") in {"related", "uncertain"}):
            pass  # The model has not identified which row the directly measured object belongs to.
        elif old_target.get("status") == "supported" and old_target.get("label"):
            choices.append(old_target)
        elif bound in self.objects:
            # A direct owner and a filing row are separate model judgments.
            if obs["source_kind"] == "prose" and name in self.names[bound]:
                choices.append(direct)
        elif obs.get("name_handling") == "as_reported" and obs["source_kind"] == "prose":
            choices.append(direct)
        obs["direct"] = direct
        obs["filing_subjects"] = list({c["id"]: c for c in choices}.values())
        qualifications = {c.get("field_applicability") for c in choices}
        reviewed_direct_row = (obs.get("binding_status") == "supported" and obs.get("target_match") == "supported"
                               and obs.get("binding_evidence_ids") and bound in self.objects
                               and self.objects[bound]["is_table_subject"] and len(choices) == 1
                               and choices[0]["id"] == bound and choices[0]["status"] == "supported"
                               and not qualifications.intersection({"uncertain", "excluded"}))
        if (self.current and self.row_subject and obs.get("name_handling") in {"related", "uncertain"}
                and choices and all(c["label"] == name for c in choices)
                and ("uncertain" in qualifications or obs.get("filing_review_reason"))
                and not reviewed_direct_row):
            obs["filing_target_missing"] = True
            for c in choices:
                c.update(status="unknown", filing_citation_status="unverified",
                         filing_citation_reason="直接对象和拟议归档对象同名，需要确认任务要求的上级对象")
        if self.current and qualifications == {"excluded"}:
            obs.update(field_excluded=True, eligible=False, projection_reason="model_excluded_field")
        if qualifications & {"excluded", "uncertain"}:
            obs["filing_disagreement"] = {"reason": "模型标明该字段的适用范围被排除或尚待确定",
                                           "field_applicability": sorted(q for q in qualifications if q)}
        if len(obs["filing_subjects"]) == 1:
            chosen = obs["filing_subjects"][0]
            conflict = ((old_target.get("status") == "supported" and old_target.get("id") not in
                         (direct["id"], chosen["id"]) and old_target.get("label") != chosen["label"]
                         and old_target.get("label") not in chosen.get("alias_names", []))
                        or chosen["id"] in self.objects and not self.objects[chosen["id"]]["is_table_subject"])
            if conflict:
                obs["filing_disagreement"] = {"previous_target": old_target,
                    "registered_object": self.objects.get(chosen["id"]), "selected_target": chosen["label"]}
            obs["target"] = chosen
            obs["associated_row"] = direct["id"] != chosen["id"]
        elif choices:
            obs["target"] = {**old_target, "status": "unknown", "label": None, "id": None,
                             "reason": "competing_model_targets"}
        elif self.current and self.row_subject and not obs.get("filing_disagreement"):
            if bound in self.objects and not self.objects[bound]["is_table_subject"]:
                obs["filing_pending_reason"] = "直接对象已核对，尚缺与任务要求的行主体相连的出处"
            elif obs.get("name_handling") in {"compare_names", "uncertain", "related"}:
                obs["filing_pending_reason"] = "已保留原文直接对象，仍需确认其对应的具体行主体"
        # The old confirmed view is still useful for grouping verified rows. Other model
        # proposals are projected individually with their uncertainty and original opinions.
        if (obs["citation_status"] != "located" or obs.get("filing_disagreement") or len(choices) != 1
                or choices[0].get("status") != "supported"
                or choices[0].get("field_applicability") in {"uncertain", "excluded"}):
            obs["eligible"] = False


def proposed_row(obs, schema, label_field):
    if ("filing_subjects" not in obs or is_missing(obs.get("value")) or not obs.get("entity") or obs.get("field_excluded")
            or obs.get("corroborates") or obs.get("filing_suppressed")):
        return None
    choices = obs["filing_subjects"]
    labels = list(dict.fromkeys(choice["label"] for choice in choices))
    reasons = []
    if obs.get("field_type_status") == "review":
        reasons.append("字段类型待核：" + obs["field_type_reason"])
    if len(choices) != 1:
        reasons.append("归属待定")
        if obs.get("filing_pending_reason"):
            reasons.append(obs["filing_pending_reason"])
        if labels and len(labels) != len(choices):
            labels = [f'{c["label"]}（{c.get("scope") or c["id"]}）' for c in choices]
    for choice in choices:
        if choice.get("filing_citation_reason"):
            reasons.extend(["归属待定", choice["filing_citation_reason"]])
    if obs.get("filing_disagreement"):
        reasons.append("归属有分歧")
        if obs["filing_disagreement"].get("reason"):
            reasons.append(obs["filing_disagreement"]["reason"])
    if any(c.get("field_applicability") == "excluded" for c in choices):
        reasons.append("模型判定字段不适用")
    if any(c.get("field_applicability") == "uncertain" for c in choices):
        reasons.append("字段适用范围待定")
    if obs.get("citation_status") == "unverified":
        reasons.append("引文未核实")
    if obs["fit"].get("status") == "contradicted" or obs.get("target_match") == "unsupported":
        reasons.append("字段用途有分歧")
    if not reasons:
        reasons.append("模型归档")
    oid = obs["observation_id"]
    row = dict.fromkeys(schema)
    label = " 或 ".join(labels) if labels else "归属待定"
    if obs.get("filing_target_missing"):
        label = "归属待定（拟议：" + label + "）"
    row[obs["field"]] = obs["value"]
    cells = {obs["field"]: [oid]}
    if label_field and label_field != obs["field"]:
        row[label_field] = label
        cells[label_field] = [oid]
    target = choices[0] if len(choices) == 1 else {}
    return row, {"subject_id": target.get("id"), "subject_label": label, "scope": target.get("scope"),
                 "cells": cells, "observations": [oid], "delivery": "provisional", "status": "｜".join(reasons),
                 "context": [], "subject_candidates": choices}, reasons
