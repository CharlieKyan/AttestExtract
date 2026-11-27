"""Apply an explicitly supplied, source-bound rereading without rewriting the frozen answers."""

from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .dataset_citation import check_reading
from .dataset_filing import _endpoint
from .dataset_field_fit import field_policy_blockers
from .execution import digest
from .dataset_statistics import value_statistic
from .models import ContractModel, ReportedUse, StatementBasis
from .storage import _reject_constant, _unique_object


class Rereading(ContractModel):
    observation_id: str | None = None
    field: str
    entity: str = Field(min_length=1)
    value: str | int | float | bool
    unit: str | None = None
    target: str | None = None
    row_applicability: Literal["as_proposed", "outside", "uncertain"] = "as_proposed"
    field_applicability: Literal["included", "excluded", "uncertain"] | None = None
    statement_basis: StatementBasis
    reported_use: ReportedUse = "unspecified"
    reason: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class ReadingReview(ContractModel):
    source_sha256: str
    reviewed_by: str = Field(min_length=1)
    readings: list[Rereading]


def apply_reading_review(source, details, review, fields, policies):
    if isinstance(review, (str, Path)):
        review = json.loads(Path(review).read_bytes(), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    if isinstance(review, list):
        for revision in review:
            apply_reading_review(source, details, revision, fields, policies)
        return
    review = ReadingReview.model_validate(review)
    if review.source_sha256 != sha256(source.encode()).hexdigest():
        raise ValueError("Rereading belongs to a different document.")
    existing = {o["observation_id"]: o for o in details}
    seen = set()
    for entry in review.readings:
        if entry.field not in fields or not 0 <= entry.start < entry.end <= len(source) or source[entry.start:entry.end] != entry.quote:
            raise ValueError("Rereading field or original source position is invalid.")
        evidence = {"quote": entry.quote, "start": entry.start, "end": entry.end}
        ref = {"fields": ["value", "entity", "unit"], "quote": entry.quote, "location_status": "matched",
               "span": {"start": entry.start, "end": entry.end}}
        if entry.observation_id is None:
            same = [o for o in details if o["field"] == entry.field and o.get("entity") == entry.entity
                    and o["value"] == entry.value and o.get("unit") == entry.unit and
                    any((s.get("span") or {}).get("start", len(source)) < entry.end and
                        (s.get("span") or {}).get("end", 0) > entry.start for s in o["source_evidence"])]
            if same:
                raise ValueError("Rereading duplicates a frozen reading; supply its observation_id.")
            oid = "reread-" + digest([review.source_sha256, entry.field, entry.entity, entry.value,
                                      entry.unit, entry.start, entry.end])[:24]
            direct = _endpoint("direct-" + oid, entry.entity, [evidence])
            obs = {"observation_id": oid, "source_kind": "prose", "source_record": oid, "candidate_id": oid,
                   "field": entry.field, "record_type": entry.field, "entity": entry.entity, "value": entry.value,
                   "unit": entry.unit, "direct": direct, "target": direct, "source_evidence": [ref],
                   "evidence": [evidence], "evidence_ids": [], "binding_evidence_ids": [], "binding_status": "as_extracted",
                   "direct_subject_status": "model_proposed", "row_ids": [], "fit": {"status": "supported",
                   "reason": "reread_field", "evidence": [evidence]}, "fit_blockers": [], "blockers": [],
                   "related": [], "candidates": [], "eligible": False, "delivery": "detail",
                   "conditions": None, "statistic": None, "purpose": "unspecified", "statistic_parts": {},
                   "statistic_kind": None, "filing_subjects": [direct], "value_located": False}
            details.append(obs)
        else:
            oid = entry.observation_id
            obs = existing.get(oid)
            if obs is None or type(obs["value"]) is not type(entry.value) or (
                    obs["field"], obs["value"], obs.get("unit"), obs.get("entity")) != (
                    entry.field, entry.value, entry.unit, entry.entity):
                raise ValueError("Rereading must identify the original unmodified fact.")
        if oid in seen:
            raise ValueError("Duplicate rereading of the same observation.")
        seen.add(oid)
        obs["reading_review"] = {"reviewed_by": review.reviewed_by, "reason": entry.reason, "evidence": evidence,
                                 "previous_basis": obs.get("statement_basis", "unknown"),
                                 "previous_target": obs.get("target"), "previous_use": obs.get("measurement_use"),
                                 "suggested_target": entry.target, "row_applicability": entry.row_applicability}
        if entry.field_applicability is not None:
            obs["reading_review"]["field_applicability"] = entry.field_applicability
        obs.setdefault("reading_reviews", []).append(obs["reading_review"])
        obs["statement_basis"] = entry.statement_basis
        if entry.reported_use != "unspecified" or not obs.get("measurement_use"):
            obs["measurement_use"] = entry.reported_use
        if entry.observation_id is None:
            obs["statistic_kind"], obs["statistic_parts"] = value_statistic(source, obs, None)
            obs["purpose"] = {"avg": "reported_mean", "range": "reported_range"}.get(
                obs["statistic_kind"], obs["measurement_use"])
            policy_conflicts = field_policy_blockers(obs, policies.get(entry.field, {}))
            if policy_conflicts:
                obs["filing_suppressed"] = True
                obs["fit_blockers"].extend(policy_conflicts)
        if entry.row_applicability == "outside":
            obs["filing_disagreement"] = {"previous_target": obs["target"], "reason": entry.reason}
            obs["eligible"] = False
            if not obs.get("associated_row"):
                obs.update(filing_subjects=[], associated_row=False,
                           target={**obs["target"], "id": None, "label": None, "status": "unknown",
                                   "reason": "reread_row_outside"})
        elif entry.target:
            current = obs["target"]
            if entry.target != current.get("label") and entry.target not in current.get("alias_names", []):
                endpoint = _endpoint("filing-" + digest([review.source_sha256, oid, entry.target])[:24],
                                     entry.target, [evidence])
                if current.get("label") and obs.get("associated_row") and obs.get("filing_subjects"):
                    obs["filing_disagreement"] = {"previous_target": current, "reread_suggestion": endpoint,
                                                   "reason": entry.reason}
                    obs["eligible"] = False
                else:
                    obs.update(target=endpoint, filing_subjects=[endpoint], eligible=False,
                               associated_row=entry.target != obs["direct"]["label"])
        if entry.row_applicability == "uncertain":
            obs.setdefault("filing_disagreement", {"suggested_target": entry.target, "reason": entry.reason})
            obs["eligible"] = False
        receipt = check_reading(source, {**obs, "source_evidence": [ref]}, allow_search=False)
        if receipt["citation_status"] == "located" or obs.get("citation_status") != "located":
            obs.update(receipt)
        obs["value_located"] = obs["citation_status"] == "located"
