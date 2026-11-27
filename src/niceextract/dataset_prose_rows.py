"""Identify distinct properties reported together in one verified prose statement."""

from bisect import bisect_right
from collections import defaultdict
import re

from .value_location import locate_value


def _profile_key(source, obs):
    """Collect registry metadata, the defining description, and a typical bound at that definition."""
    direct, target = obs["direct"], obs["target"]
    definition = direct.get("designation_span")
    if (direct["id"] != target["id"] or not definition
            or obs.get("measurement_use") not in {None, "unspecified"}):
        return None
    key = ("profile", target["id"], definition["start"], definition["end"])
    reading = str(obs["value"])
    kind = obs.get("statistic_kind")
    for entry in obs["source_evidence"]:
        span = entry.get("span")
        if not span or not {"entity", "value"}.issubset(entry["fields"]):
            continue
        text = source[span["start"]:span["end"]]
        if not kind and not obs.get("unit"):
            if span["start"] <= definition["start"] and definition["end"] <= span["end"]:
                return key
            if not obs.get("conditions") and reading in (direct.get("scope") or "") and reading in text:
                return key
        if kind == "bound" and obs.get("statistic_parts", {}).get("qualifier"):
            left, right = sorted((definition["start"], span["start"]))
            # Local profile only: another paragraph or a newly introduced scope gets its own row.
            if (not re.search(r"\r?\n[ \t]*\r?\n", source[left:right])
                    and text.startswith(direct["label"])
                    and obs.get("conditions") and text.endswith(obs["conditions"])
                    and not re.search(r"[,，;；]", text)):
                return key
    return None


def prose_record_keys(source, boundaries, observations):
    """Pair values only across a property transition, retaining all per-cell qualifiers."""
    candidates = defaultdict(list)
    profile_bounds = defaultdict(list)
    records = {}
    for obs in observations:
        if not obs["eligible"] or obs["source_kind"] != "prose" or obs.get("record_group"):
            continue
        if profile := _profile_key(source, obs):
            if obs.get("statistic_kind") == "bound":
                profile_bounds[profile].append(obs["observation_id"])
            else:
                records[obs["observation_id"]] = profile
            continue
        if not obs.get("property_name") or obs.get("statistic_kind") not in {"value", "range"}:
            continue
        readings = set()
        for entry in obs["source_evidence"]:
            span = entry.get("span")
            if "value" in entry["fields"] and span:
                value = locate_value(source, obs["value"], span["start"], span["end"])
                if value:
                    readings.add((value["start"], value["end"]))
        if len(readings) != 1:
            continue
        start, end = readings.pop()
        sentence = bisect_right(boundaries, start) - 1
        if end > boundaries[sentence + 1]:
            continue
        key = (obs["target"]["id"], obs["direct"]["id"], sentence, obs["conditions"],
               obs.get("measurement_use"), obs["statistic_kind"], obs.get("purpose"))
        candidates[key].append((start, end, obs))

    # Definition metadata can describe a single reported limit. Two limits
    # remain separate observations even when both cite the same designation.
    for profile, bounds in profile_bounds.items():
        if len(bounds) == 1:
            records[bounds[0]] = profile

    for key, members in candidates.items():
        if len(members) < 2 or len({o["field"] for _, _, o in members}) != len(members):
            continue
        members.sort(key=lambda item: item[0])
        start, end = members[0][0], members[-1][1]
        # Every property must have a bound owner citation spanning the whole coordinated list.
        if not all(any(e["start"] <= start and end <= e["end"] for e in o["direct"]["evidence"])
                   for _, _, o in members):
            continue
        for (_, left, previous), (right, _, following) in zip(members, members[1:]):
            prop = re.escape(following["property_name"])
            unit = re.escape(previous.get("unit") or "")
            condition = re.escape(following.get("conditions") or "")
            transition = (rf"\s*(?:{unit})?\s*[,，、]?\s*(?:(?:and\s+(?:its\s+)?)|及其|且)?"
                          rf"(?:{condition}\s+)?{prop}\s*(?:{condition})?\s*(?:was|is|of|为|[:：=])?\s*")
            if left > right or not re.fullmatch(transition, source[left:right], re.I):
                break
        else:
            for _, _, obs in members:
                records[obs["observation_id"]] = ("prose", *key)
    return records
