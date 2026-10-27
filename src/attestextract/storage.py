"""Strict JSON persistence with source and review-link validation on reload."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import TypeVar

from pydantic import BaseModel

from .archive import ExtractionArchive
from .documents import Document


T = TypeVar("T", bound=BaseModel)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys are not allowed")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON numbers are not allowed")


def _load(path: str | Path, model: type[T]) -> T:
    payload = json.loads(
        Path(path).read_bytes().decode("utf-8-sig"),
        object_pairs_hook=_unique_object, parse_constant=_reject_constant,
    )
    return model.model_validate(payload)


def _save(value: T, path: str | Path, model: type[T]) -> None:
    # Validate a fresh copy even if a caller mutated nested Pydantic objects.
    checked = model.model_validate(value.model_dump())
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=target.parent, prefix=".attestextract-", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(checked.model_dump_json(indent=2) + "\n")
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def save_document(document: Document, path: str | Path) -> None:
    _save(document, path, Document)


def load_document(path: str | Path) -> Document:
    return _load(path, Document)


def save_archive(archive: ExtractionArchive, path: str | Path) -> None:
    _save(archive, path, ExtractionArchive)


def load_archive(path: str | Path) -> ExtractionArchive:
    return _load(path, ExtractionArchive)
