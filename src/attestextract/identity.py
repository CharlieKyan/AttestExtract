"""Content identity shared by extraction and reviewed archive validation."""

import hashlib
import json

from .models import FieldValue, MeasurementEvidence
from .evidence import is_missing


def _legacy_evidence(value: object) -> object:
    """Strip only 0.4 evidence additions before computing historic identities."""
    if isinstance(value, list):
        return [_legacy_evidence(item) for item in value]
    if isinstance(value, dict):
        evidence = {"quote", "status", "matches"}.issubset(value)
        return {key: _legacy_evidence(item) for key, item in value.items()
                if not evidence or key not in {"source_matches", "table_refs"}}
    return value


def can_ground_fields(fields: dict[str, FieldValue]) -> bool:
    """Require at least one located observation; absent optional values add no location."""
    return any(not is_missing(field.value) for field in fields.values()) and all(
        is_missing(field.value) or field.evidence.status == "matched" for field in fields.values()
    )


def grounded_candidate_id(
    source_sha256: str, record_type: str, fields: dict[str, FieldValue],
    measurements: dict[str, MeasurementEvidence] | None = None,
    *, schema_version: str = "0.4",
) -> str:
    """Identify a candidate whose every field has one exact original span."""
    identity = {
        "source": source_sha256, "record_type": record_type,
        "fields": {name: value.model_dump() for name, value in sorted(fields.items())},
    }
    if measurements:
        excluded = {"range_binding"} if schema_version in {"0.1", "0.2"} else set()
        identity["measurements"] = {name: group.model_dump(exclude=excluded)
                                    for name, group in sorted(measurements.items())}
    if schema_version != "0.4":
        identity = _legacy_evidence(identity)
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return "candidate-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
