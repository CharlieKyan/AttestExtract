"""Opt-in local run artifacts. Never record HTTP headers or endpoint URLs."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .errors import AttestExtractError, BackendError, ConfigurationError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_text(value: str, secrets: Iterable[str]) -> str:
    """Mask raw and JSON-escaped secrets in diagnostic strings."""
    for secret in sorted({item for item in secrets if isinstance(item, str) and item}, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
        escaped = json.dumps(secret, ensure_ascii=False)[1:-1]
        if escaped != secret:
            value = value.replace(escaped, "[REDACTED]")
    return value


class RunTrace:
    """Write one run to an empty directory, masking explicitly supplied secrets.

    Artifacts include source text and model output. Keep them outside a public
    repository. A trace is a diagnostic record, not an acceptance decision.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        secrets: Iterable[str] = (),
        progress: Callable[[str], None] | None = None,
    ) -> None:
        # Resolve lazily: the public package exports RunTrace during import.
        from . import __version__

        self.directory = Path(directory).resolve()
        self._secrets: set[str] = set()
        self.add_secrets(*secrets)
        self._progress = progress
        self.stage = "configuration"
        self._manifest: dict[str, Any] = {
            "trace_schema_version": "0.2", "status": "running",
            "package_version": __version__,
            "started_at": _now(), "artifacts": [], "artifact_roles": {},
            "notice": "Contains source text and model output; not fact verification.",
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        if any(self.directory.iterdir()):
            raise ConfigurationError("Log directory must be empty; choose a new directory for each run.")
        # Exclusive creation also prevents two concurrent runs sharing a trace.
        try:
            with (self.directory / "manifest.json").open("x", encoding="utf-8") as stream:
                json.dump(self._manifest, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
        except FileExistsError:
            raise ConfigurationError("Log directory is already in use; choose a new directory.") from None

    def add_secrets(self, *secrets: str) -> None:
        self._secrets.update(secret for secret in secrets if isinstance(secret, str) and secret)

    def describe_artifact(self, relative: str, role: str) -> None:
        self._manifest["artifact_roles"][relative] = role

    def _mask_text(self, value: str) -> str:
        return redact_text(value, self._secrets)

    def _mask(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._mask_text(value)
        if isinstance(value, dict):
            return {self._mask_text(str(key)): self._mask(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._mask(item) for item in value]
        return value

    def _target(self, relative: str) -> Path:
        path = Path(relative)
        target = (self.directory / path).resolve()
        if path.is_absolute() or not target.is_relative_to(self.directory) or target == self.directory:
            raise ValueError("Trace artifact path must stay inside the log directory.")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def write_text(self, relative: str, text: str) -> None:
        self._target(relative).write_text(self._mask_text(text), encoding="utf-8", newline="")
        if relative not in self._manifest["artifacts"]:
            self._manifest["artifacts"].append(relative)

    def write_json(self, relative: str, payload: Any) -> None:
        text = json.dumps(self._mask(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        # Mask values before serialization only. A second text replacement
        # could corrupt JSON literals when a local key is e.g. "null" or "1".
        self._target(relative).write_text(text, encoding="utf-8", newline="")
        if relative not in self._manifest["artifacts"]:
            self._manifest["artifacts"].append(relative)

    def event(self, stage: str, event: str, **details: Any) -> None:
        self.stage = stage
        payload = self._mask({"timestamp": _now(), "stage": stage, "event": event, **details})
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n")
        if self._progress is not None and event in {"started", "completed", "failed", "retry", "succeeded", "rejected"}:
            suffix = " ".join(
                f"{key}={payload[key]}" for key in ("chunk_id", "attempt", "status_code", "candidate_count", "review_count", "error_count", "path")
                if key in payload
            )
            self._progress(f"[{stage}] {event}" + (f" {suffix}" if suffix else ""))

    def finish(self, result_payload: dict[str, Any]) -> None:
        self.write_json("result.json", result_payload)
        self._manifest.update(
            status="succeeded", finished_at=_now(),
            package_version=result_payload.get("package_version"), run=result_payload.get("run"),
        )
        self.event("complete", "succeeded")
        self.write_json("manifest.json", self._manifest)

    def fail(self, exc: Exception, stage: str | None = None) -> None:
        if self._manifest["status"] == "failed":
            return
        failure_stage = stage or self.stage
        error: dict[str, Any] = {"stage": failure_stage, "error_type": type(exc).__name__}
        if isinstance(exc, AttestExtractError):
            error["message"] = str(exc)
        elif isinstance(exc, OSError):
            error.update(message=exc.strerror or "Could not read or write a local file.", errno=exc.errno)
        else:
            error["message"] = "Operation failed; inspect the error type and preceding diagnostic artifacts."
        if isinstance(exc, BackendError):
            error.update(status_code=exc.status_code, attempts=exc.attempts)
            if exc.http_diagnostics is not None:
                error.update(http_diagnostics=exc.http_diagnostics, http_details=exc.http_details)
        diagnostic = getattr(exc, "validation_details", None)
        if diagnostic is not None:
            error.update(validation_phase=diagnostic["phase"], error_count=diagnostic["error_count"],
                         validation_details=getattr(exc, "validation_artifact", None),
                         first_error=diagnostic["errors"][0] if diagnostic["errors"] else None)
        self.write_json("error.json", error)
        self._manifest.update(status="failed", finished_at=_now(), error=error)
        self.event(failure_stage, "failed", error_type=type(exc).__name__)
        self.write_json("manifest.json", self._manifest)
