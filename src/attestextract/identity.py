"""Content identity shared by extraction and reviewed archive validation."""

import hashlib
import json

from .models import FieldValue


def grounded_candidate_id(
    source_sha256: str, record_type: str, fields: dict[str, FieldValue],
) -> str:
    """Identify a candidate whose every field has one exact original span."""
    identity = {
        "source": source_sha256, "record_type": record_type,
        "fields": {name: value.model_dump() for name, value in sorted(fields.items())},
    }
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return "candidate-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
