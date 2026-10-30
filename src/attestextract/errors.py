"""Public exceptions raised by AttestExtract."""

from typing import Any


_BACKEND_HINTS = {
    "authentication_error": "Check the selected provider's API key and endpoint.",
    "billing_error": "Check the provider account balance, billing and quota.",
    "permission_denied": "Check whether this key has access to the selected model and region.",
    "not_found": "Check the model ID and API endpoint; the model or route was not found.",
    "invalid_request": "Check the model's supported parameters and output mode; no parameter fallback was attempted.",
}


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
        code: str = "backend_error",
        retryable: bool = False,
    ) -> None:
        self.hint = _BACKEND_HINTS.get(code)
        super().__init__(message + (" " + self.hint if self.hint else ""))
        self.status_code = status_code
        self.attempts = attempts
        self.http_diagnostics = http_diagnostics
        self.http_details = http_details
        self.code = code
        # Describes the cause, not permission to make another paid request.
        self.retryable = retryable


class InvalidResponseError(BackendError):
    """A backend response is malformed, incomplete, or unsupported."""

    def __init__(
        self, message: str, *, status_code: int | None = None, attempts: int = 1,
        validation_details: dict[str, Any] | None = None,
        validation_artifact: str | None = None,
        code: str = "invalid_response",
    ) -> None:
        super().__init__(message, status_code=status_code, attempts=attempts, code=code)
        self.validation_details = validation_details
        self.validation_artifact = validation_artifact


class TaskValidationError(AttestExtractError):
    """A task definition or extracted record does not satisfy its schema."""
