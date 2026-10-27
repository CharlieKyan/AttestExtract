"""A document's exact extraction text, representation, and source metadata."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from .models import ContractModel


class DocumentMetadata(ContractModel):
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=1, le=9999)
    doi: str | None = None
    source_uri: str | None = None


def _document_id(text_hash: str, representation: str, version: str) -> str:
    key = json.dumps([text_hash, representation, version], ensure_ascii=False)
    return "document-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


class Document(ContractModel):
    """The text is embedded so archived evidence can always be checked.

    Offsets refer to this exact decoded text, including CRLF and emoji. A
    Markdown representation of a PDF is a separate representation; these are
    never presented as PDF byte, page, or bounding-box coordinates.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    document_id: str
    text: str
    source_name: str | None = None
    representation: Literal["text/plain", "text/markdown"] = "text/plain"
    representation_version: str = Field(default="1", min_length=1)
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    character_count: int = Field(ge=0)

    @model_validator(mode="after")
    def verify_identity(self) -> Document:
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.text_sha256 != digest or self.character_count != len(self.text):
            raise ValueError("Document text, hash, and character count must agree")
        if self.document_id != _document_id(digest, self.representation, self.representation_version):
            raise ValueError("Document ID must identify its exact text representation")
        return self

    @classmethod
    def from_text(
        cls, text: str, *, source_name: str | None = None,
        representation: Literal["text/plain", "text/markdown"] = "text/plain",
        representation_version: str = "1",
        metadata: DocumentMetadata | dict | None = None,
    ) -> Document:
        if not isinstance(text, str):
            raise TypeError("Document text must be a string")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return cls(
            document_id=_document_id(digest, representation, representation_version),
            text=text, source_name=source_name, representation=representation,
            representation_version=representation_version,
            metadata=DocumentMetadata.model_validate(metadata or {}),
            text_sha256=digest, character_count=len(text),
        )
