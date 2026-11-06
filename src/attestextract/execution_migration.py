"""Narrow, offline compatibility bridge for verifiable dev19 v2 runs.

The old fingerprint is opaque. Reconstruct its exact inputs before migrating;
never strip fields from a hash or follow historical diagnostic paths.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .errors import ConfigurationError
from .models import ExtractionRequest
from .prompting import build_messages, build_output_schema
from .schema_diagnostics import schema_request_info


def _read(directory: Path, relative: str):
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError("legacy artifact outside run")
    return json.loads(path.read_bytes().decode("utf-8"))


def _old_transport(metadata: dict) -> dict:
    timeout = metadata["timeout"]
    attempts = metadata["max_attempts"]
    mode = metadata["tls_mode"]
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60
            or type(attempts) is not int or attempts < 1 or mode not in {"auto", "1.2"}):
        raise ValueError("legacy transport metadata")
    return {"timeout": timeout, "max_attempts": attempts, "tls_mode": mode}


class Dev19Migration:
    def __init__(self, backend, task, generation, identity: dict) -> None:
        self.backend, self.task, self.generation = backend, task, generation
        self.identity = identity

    def migrate_identity(self, state, checkpoint: Path, target) -> None:
        from .execution import digest

        directory = checkpoint.parent.parent
        manifest = _read(directory, "manifest.json")
        if not isinstance(manifest, dict):
            raise ValueError("legacy manifest")
        if manifest.get("package_version") != "0.1.0.dev19":
            raise ConfigurationError("Only dev19 v2 checkpoints have a verified migration path. Start a new run for this version.")
        metadata = _read(directory, "backend.json")
        if not isinstance(metadata, dict):
            raise ValueError("legacy backend metadata")
        transport = _old_transport(metadata)
        if metadata.get("backend") != self.backend.name or metadata.get("model") != self.backend.model:
            raise ValueError("legacy backend metadata")
        cli_config = getattr(self.backend, "_legacy_cli_config", None)
        if cli_config is not None:
            keys = {"adapter", "base_url", "model", "timeout", "max_attempts", "structured_output",
                    "tls_mode", "generation", "provider_options", "output_token_parameter"}
            old_backend = {key: cli_config[key] for key in keys}
        else:
            old_backend = {key: getattr(self.backend, key, None) for key in (
                "name", "base_url", "model", "timeout", "max_attempts", "tls_mode", "structured_output",
                "provider_options", "output_token_parameter")}
        old_backend.update(transport)
        old_identity = {key: value for key, value in self.identity.items() if key != "engine_revision"}
        old_identity.update(package_version="0.1.0.dev19", backend=digest(old_backend))
        if digest(old_identity) != state.fingerprint:
            raise ConfigurationError("Resume identity mismatch: the dev19 fingerprint could not be reconstructed from the same source, task, model, endpoint and generation/planning settings.")
        state.migrations.append({"from_checkpoint_version": "2", "from_package_version": "0.1.0.dev19",
                                 "from_fingerprint": state.fingerprint, "to_checkpoint_version": "3"})
        state.checkpoint_version = "3"
        state.fingerprint = target.fingerprint
        state.engine_revision = target.engine_revision
        state.transport_settings = {**transport, "read_timeout": None}

    def validate_requests(self, state, checkpoint: Path, chunks: dict) -> None:
        # Import lazily: execution is imported by pipeline, but regeneration
        # must use the very same diagnostic-to-message transformation.
        from .pipeline import _format_feedback, _regeneration_request

        directory = checkpoint.parent.parent
        schema = build_output_schema(self.task, output_protocol=state.output_protocol)
        for key, node in state.nodes.items():
            if not node.calls:
                continue
            chunk = chunks[key]
            request = ExtractionRequest(chunk_id=key, output_protocol=state.output_protocol,
                messages=build_messages(self.task, chunk, output_protocol=state.output_protocol),
                output_schema=schema, generation=self.generation)
            self._check_request(directory, request)
            if node.format_retries_reserved:
                # After a second rejection the checkpoint feedback describes
                # that second error, not the feedback sent for regeneration.
                diagnostic = _read(directory, f"validation/{key}.errors.json")
                if not isinstance(diagnostic, dict) or not isinstance(diagnostic.get("errors"), list):
                    raise ValueError("legacy format diagnostic")
                feedback = _format_feedback(diagnostic, self.task, state.output_protocol)
                self._check_request(directory, _regeneration_request(request, feedback))

    def _check_request(self, directory: Path, request: ExtractionRequest) -> None:
        stem = request.artifact_stem
        if _read(directory, f"requests/{stem}.request.json") != self.backend.prepare_payload(request):
            raise ValueError("legacy request is missing, redacted, or incompatible")
        expected = schema_request_info(request.output_schema, self.backend.structured_output)
        if _read(directory, f"schema/{stem}.request.json") != expected:
            raise ValueError("legacy response contract is incompatible")
