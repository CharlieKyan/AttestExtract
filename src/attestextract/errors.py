"""Public exceptions raised by AttestExtract."""

from typing import Any


class AttestExtractError(Exception):
    """Base class for expected, user-facing failures."""


class ConfigurationError(AttestExtractError):
    """The task or model connection is not configured correctly."""


class BackendError(AttestExtractError):
    """A model request failed without producing a usable response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        attempts: int = 1,
        http_diagnostics: dict[str, Any] | None = None,
        http_details: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.attempts = attempts
        self.http_diagnostics = http_diagnostics
        self.http_details = http_details


class InvalidResponseError(BackendError):
    """A backend response is malformed, incomplete, or unsupported."""

    def __init__(
        self, message: str, *, status_code: int | None = None, attempts: int = 1,
        validation_details: dict[str, Any] | None = None,
        validation_artifact: str | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, attempts=attempts)
        self.validation_details = validation_details
        self.validation_artifact = validation_artifact


class TaskValidationError(AttestExtractError):
    """A task definition or extracted record does not satisfy its schema."""
