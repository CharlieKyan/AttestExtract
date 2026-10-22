"""Public exceptions raised by AttestExtract."""


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
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.attempts = attempts


class InvalidResponseError(BackendError):
    """A backend response is malformed, incomplete, or unsupported."""


class TaskValidationError(AttestExtractError):
    """A task definition or extracted record does not satisfy its schema."""
