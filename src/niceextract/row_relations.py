"""Source location for optional first-pass row associations, without interpreting their meaning."""

from typing import get_args
from pydantic import TypeAdapter, ValidationError

from .models import (ExplainedRelationReceipt, ExplainedRowRelation, ReportedUse, RowRelation,
                     RowRelationAlternatives, RowRelationIssue, RowRelationProposal, RowRelationReceipt, StatementBasis)


_PROPOSAL = TypeAdapter(RowRelationProposal)


INSTRUCTIONS = (
    "Prioritize observations relevant to task.row_subject and the requested field. Keep entity as "
    "the locally described owner; keep its measurement scope, usage, and conditions. Distinguish "
    "the object actually measured from the document's overall study subject. "
    "For a distinct, source-linked target row, use name_handling=related and set row_relation to "
    "{target, predicate, field_applicability, evidence}; predicate runs FROM entity TO target. "
    "Use included only when the USER'S field definition allows that direct object's reported property, "
    "statistic and use to be filed under the target; excluded for a different use, uncertain when unclear. "
    "This does not transfer the property to the target. Use the source's own target "
    "label and cite the text that links this specific observation's owner to it. Evidence entries "
    "have an exact quote and ref (null for prose, the original source entry ID for tables). In a "
    "table cite the direct owner cell and the applicable target heading. For a mapping column or "
    "row, cite both the mapping cell and the heading that gives its role. "
    "If the source explicitly gives multiple possible targets, keep one observation, choose "
    "name_handling=uncertain and return {alternatives:[...]} with each possible target's directed "
    "relation, field applicability and citation. Cite how each option pertains to this owner; "
    "adjacent labels or shared values alone do not establish alternatives. If the entity itself "
    "is the row, or the source supplies no target, use row_relation=null. A study title alone "
    "does not link every observation to its subject. A relationship never changes the reported use.\n"
)


STATEMENT_INSTRUCTIONS = (
    "Keep the original entity, raw value and unit, conditions, and the original wording in the field evidence. "
    "For each observation set statement_basis to reported if the source explicitly reports this property for "
    "this direct owner with this meaning (a faithful synonym is fine); inferred if assigning the selected "
    "field, owner or meaning requires an inference; unknown if undecidable. A general range does not "
    "explicitly report the start/end of a specific interval or process. A source reporting a computed or "
    "configured value explicitly still counts as reported, with its particular use. Set reported_use to "
    "measured, model_input, process_setting, derived_setting, boundary_condition, classification_threshold, "
    "sensitivity_input_range or unspecified. Classify what this observation represents in its own source; "
    "For source_property copy the author's original property phrase exactly, using the applicable header in "
    "a table. Cite this wording in the observation's evidence. Use null when it is unnamed. "
    "preserve the words for criteria, bounds, settings and conditions. Do not turn a setting or threshold "
    "into a measurement. For qualitative observations without a specific use choose unspecified.\n"
)

SOURCE_RELATION_INSTRUCTIONS = (
    "For each proposed row link, target_basis classifies the assignment of THIS observation to THAT target: "
    "reported if the source states the link, inferred if the link requires an interpretation, "
    "unknown if undecidable. A target name appearing in the text does not itself state the link. "
    "Judge it independently from statement_basis for the original property. Retain a proposed target with "
    "uncertain applicability and empty evidence if the link cannot be cited; never invent a source reference.\n"
)

OPEN_RELATION_INSTRUCTIONS = (
    "First report the direct observation with the source's owner, property, value, unit and conditions. "
    "Compare the direct owner to the requested row subject for every record. Use as_reported and omit "
    "row_relation only if the direct owner itself qualifies as that requested row with this scope. A clear "
    "local owner name alone does not qualify it. If the direct owner is different and you can name a "
    "source-linked row, use related and give its target and explanation. If the row cannot be determined, "
    "retain the original observation, use uncertain (or compare_names for a specific unresolved reference), "
    "and omit the link. Do not manufacture a target. "
    "When the source connects that owner to a distinct requested row, choose name_handling=related and "
    "add row_relation with target (the row's name), explanation (a plain-language sentence describing "
    "how THIS direct owner connects to THAT row), field_applicability (included, excluded, uncertain), "
    "target_basis (reported, inferred, unknown), and evidence (verbatim quotes with ref=null in prose "
    "or the original entry ID in a table). Use the source language where helpful. Explain each step "
    "and cite its source, including a table's owner cell, corresponding heading or mapping cell where needed. "
    "Optionally set target_contains_direct=true only when the direct object is a constituent or subordinate "
    "of the target according to the cited source. Comparison, use, a shared instrument, and filing eligibility "
    "alone do not establish containment; use false for a known noncontainment relation, or null if unclear. "
    "When a target's display name is normalized or translated, give target_source_name as the exact "
    "locally cited source label for that target; otherwise use null. "
    "Choose included only if the user's field accepts this specific observation under that row. Keep "
    "scope, statistic, and use tied to the direct owner. A study title or shared value alone cannot establish "
    "the link. If the source supports distinct competing rows, use name_handling=uncertain and a single "
    "row_relation containing alternatives (an array of two or more complete options). An uncertain link "
    "may carry empty evidence; never invent quotations. Example unrelated to this source: for 'Core C was "
    "taken from site S', a measurement on C may carry target 'site S', explanation 'Core C was sampled "
    "at site S', and the exact sentence as evidence.\n"
)


def statement_properties():
    return {"statement_basis": {"type": "string", "enum": list(get_args(StatementBasis))},
            "reported_use": {"type": "string", "enum": list(get_args(ReportedUse))},
            "source_property": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]}}


def response_property(*, source_statements=False, open_relation=False):
    """Inline the proposal schema so both providers' existing root references remain valid."""
    schema = (ExplainedRowRelation if open_relation else RowRelation).model_json_schema()
    schema["properties"]["evidence"]["items"] = schema.pop("$defs")["RelationCitation"]
    if not source_statements:
        schema["properties"].pop("target_basis")
        # The table request serializes this schema as prompt text; retain the archived byte order.
        evidence = schema["properties"]["evidence"]
        schema["properties"]["evidence"] = {"items": evidence["items"], "minItems": 1, **evidence}
    else:
        schema["properties"]["target_basis"] = {"type": "string", "enum": list(get_args(StatementBasis))}
        schema["required"].append("target_basis")
        if open_relation:
            # Strict structured providers require all declared keys. Plain JSON may omit the judgment.
            for name in ("target_contains_direct", "target_source_name"):
                schema["properties"][name].pop("default", None)
                schema["required"].append(name)
    choices = {"type": "object", "additionalProperties": False, "required": ["alternatives"],
               "properties": {"alternatives": {"type": "array", "items": schema, "minItems": 2}}}
    return {"anyOf": [schema, choices, {"type": "null"}]}


def read_open_relation(raw):
    """Read one optional association independently of the observation's contract."""
    if raw is None:
        return None, []
    alternative = isinstance(raw, dict) and set(raw) == {"alternatives"}
    items = raw["alternatives"] if alternative else [raw]
    try:
        if not isinstance(items, list) or (alternative and len(items) < 2):
            raise ValueError("Expected at least two alternatives.")
        parsed = []
        for item in items:
            if isinstance(item, dict):
                item = dict(item)
                if "predicate" in item and "explanation" not in item:
                    item["explanation"] = item.pop("predicate")
                if isinstance(item.get("evidence"), list):
                    # A bare entry is either a displayed reference or an exact source quote.
                    item["evidence"] = [{"ref": None, "quote": entry} if isinstance(entry, str) else entry
                                        for entry in item["evidence"]]
            parsed.append(ExplainedRowRelation.model_validate(item))
        if len({item.target.strip() for item in parsed}) != len(parsed):
            raise ValueError("Alternative targets must be distinct.")
    except ValidationError as exc:
        error = exc.errors(include_input=False, include_url=False, include_context=False)[0]
        name = error["loc"][0] if error["loc"] else None
        field = name if name in ExplainedRowRelation.model_fields else "row_relation"
        reason = f"Invalid {field} ({error['type']})."
    except ValueError as exc:
        reason = str(exc)
    else:
        return parsed if alternative else parsed[0], []
    return None, [RowRelationIssue(raw=raw, reason=reason)]


def locate_relation(proposal, passages):
    """Preserve all proposals on one observation, including unresolved alternatives."""
    if proposal is None:
        return []
    passages = list(passages)
    if isinstance(proposal, list):
        return [_locate(item, passages, alternative=True) for item in proposal]
    if isinstance(proposal, RowRelationAlternatives):
        return [_locate(item, passages, alternative=True) for item in proposal.alternatives]
    return [_locate(proposal, passages, alternative=False)]


def legacy_relation_mismatch(proposal):
    if proposal is None:
        return False
    choices = proposal.alternatives if isinstance(proposal, RowRelationAlternatives) else [proposal]
    return any(not choice.evidence or "target_basis" in choice.model_fields_set for choice in choices)


def table_relation_receipts(record, table_input):
    raw = record["row_relation"]
    options = raw.get("alternatives", [raw])
    if any("explanation" in item for item in options):
        proposal, issues = read_open_relation(raw)
        if issues:
            raise ValueError("Invalid stored row explanation.")
    else:
        proposal = _PROPOSAL.validate_python(raw)
    entries = ((e["ref"], e["start"], e["quote"]) for e in table_input["entries"])
    return [receipt.model_dump() for receipt in locate_relation(proposal, entries)]


def _locate(proposal, passages, *, alternative):
    """Find each exact quote once within the visible passage(s) identified by its ref."""
    evidence = []
    for citation in proposal.evidence:
        selections = {}
        free = isinstance(proposal, ExplainedRowRelation) and citation.ref is None
        named = [p for p in passages if free and p[0] == citation.quote]
        if named:
            # Only an ID actually displayed in this request can name a whole source entry.
            for ref, base, text in named:
                if text:
                    selections[base, base + len(text)] = {"ref": ref, "quote": text}
        for ref, base, text in passages:
            if named or not free and citation.ref != ref:
                continue
            idx = text.find(citation.quote)
            while idx != -1:
                selections[base + idx, base + idx + len(citation.quote)] = {"ref": ref, "quote": citation.quote}
                idx = text.find(citation.quote, idx + 1)
        if len(selections) == 1:
            (start, end), found = selections.popitem()
            evidence.append({**found, "start": start, "end": end})
        else:
            evidence.append({**citation.model_dump(), "start": None, "end": None})
    status = "located" if evidence and all(e["start"] is not None for e in evidence) else "unresolved"
    receipt = ExplainedRelationReceipt if isinstance(proposal, ExplainedRowRelation) else RowRelationReceipt
    return receipt(**proposal.model_dump(exclude={"evidence"}), alternative=alternative, evidence=evidence, status=status)
