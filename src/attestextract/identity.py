"""Content identity shared by extraction and reviewed archive validation."""

import hashlib
import json

from .models import FieldValue, MeasurementEvidence
from .evidence import is_missing


def can_ground_fields(fields: dict[str, FieldValue]) -> bool:
    """Require at least one located observation; absent optional values add no location."""
    return any(not is_missing(field.value) for field in fields.values()) and all(
        is_missing(field.value) or field.evidence.status == "matched" for field in fields.values()
    )


def grounded_candidate_id(
    source_sha256: str, record_type: str, fields: dict[str, FieldValue],
    measurements: dict[str, MeasurementEvidence] | None = None,
) -> str:
    """Identify a candidate whose every field has one exact original span."""
    identity = {
        "source": source_sha256, "record_type": record_type,
        "fields": {name: value.model_dump() for name, value in sorted(fields.items())},
    }
    if measurements:
        identity["measurements"] = {name: group.model_dump() for name, group in sorted(measurements.items())}
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return "candidate-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
