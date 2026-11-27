"""Keep repeated readings as separately traceable sources for one displayed observation."""

import re

from .value_location import _math_spelling


def _reading_spans(obs):
    return [(s["span"]["start"], s["span"]["end"]) for s in obs["source_evidence"]
            if s.get("span") and "value" in s["fields"]]


def _consistent(left, right):
    if any(o.get("field_excluded") or o.get("filing_suppressed") or o.get("filing_disagreement")
           or len(o.get("filing_subjects", [])) != 1 for o in (left, right)):
        return False
    if any(left.get(k) != right.get(k) for k in
           ("field", "unit", "conditions", "statistic_kind", "statement_basis", "measurement_use",
            "value_role", "field_type_status")):
        return False
    for axis in ("direct", "target"):
        ids = [o[axis].get("id") or "" for o in (left, right)]
        if all(k.startswith("object-") for k in ids) and ids[0] != ids[1]:
            return False
    values = [str(o["value"]).strip() for o in (left, right)]
    if any(re.search(r"\d", value) for value in values):
        # Keep direction, bounds, exponents and range operators when checking a proposed duplicate.
        return (_math_spelling(values[0]) or values[0]) == (_math_spelling(values[1]) or values[1])
    return True


def collect_repeats(observations, evidence):
    by_id = {o["observation_id"]: o for o in observations}
    seen = {}
    for obs in observations:
        if obs.get("corroborates") or obs.get("filing_suppressed"):
            continue
        spans = _reading_spans(obs)
        duplicates = obs.get("reviewed_repeats") or []
        primary = None
        if len(duplicates) == 1:
            decision = duplicates[0]
            other = by_id.get(decision["target_id"])
            proof = [evidence[k] for k in decision["evidence_ids"]]
            if other and _consistent(obs, other):
                def cited_positions(record_spans):
                    return {(p["start"], p["end"]) for p in proof for start, end in record_spans
                            if p["start"] < end and start < p["end"]}
                if any(a != b for a in cited_positions(spans) for b in cited_positions(_reading_spans(other))):
                    if not other.get("corroborates") and not other.get("reviewed_repeats"):
                        primary = other
        elif not duplicates and len(set(spans)) == 1 and obs.get("citation_status") == "located":
            # Separate extracted IDs can point to precisely the same original
            # occurrence. Never merge a different location simply because its number repeats.
            key = (spans[0], obs["field"], obs["entity"], str(obs["value"]), obs.get("unit"),
                   tuple((e["label"], e.get("scope"), e.get("field_applicability")) for e in obs.get("filing_subjects", [])))
            other = seen.get(key)
            if other is None:
                seen[key] = obs
            elif _consistent(obs, other):
                primary = other
        if primary is not None:
            obs.update(corroborates=primary["observation_id"], eligible=False, projection_reason="same_observation")
            primary.setdefault("corroborating_observations", []).append(obs["observation_id"])
