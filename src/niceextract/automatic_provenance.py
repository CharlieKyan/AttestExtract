"""Self-contained automatic source maps, shared by archives and reports."""

from __future__ import annotations

import re

from .models import ExtractionResult, GenerationOptions


def validate_automatic_provenance(extraction: ExtractionResult, leaves: set[str] | None = None) -> None:
    """Validate the self-contained source map without copying service configuration."""
    run = extraction.run
    if leaves is None:
        leaves = set(run.chunk_provenance)
    if len(leaves) != run.chunk_count:
        raise ValueError("Automatic source count must match the final chunks")
    for candidate in extraction.candidates:
        if (not candidate.chunk_ids or len(candidate.chunk_ids) != len(set(candidate.chunk_ids))
                or set(candidate.chunk_ids) - leaves):
            raise ValueError("Automatic candidates must reference known final chunks")
    if any(table.chunk_id not in leaves for table in extraction.table_results):
        raise ValueError("Automatic table results must reference known final chunks")
    if run.backend != "automatic" or run.model is not None or not run.routes:
        raise ValueError("Automatic results must identify their models per chunk")
    config_keys = {"adapter", "model", "structured_output", "output_token_parameter", "generation"}
    for route_id, route in run.routes.items():
        if (not isinstance(route, dict) or set(route) != {"id", "role", "config", "config_digest"}
                or route["id"] != route_id or not isinstance(route["role"], str)
                or route["role"] not in {"primary", "fallback"}
                or not isinstance(route["config"], dict) or set(route["config"]) != config_keys
                or not isinstance(route["config_digest"], str)
                or re.fullmatch(r"[0-9a-f]{64}", route["config_digest"]) is None):
            raise ValueError("Automatic route metadata must contain only verified public descriptor fields")
        config = route["config"]
        if (any(not isinstance(config[key], str) or not config[key] for key in config_keys - {"generation"})
                or not isinstance(config["generation"], dict)
                or set(config["generation"]) - {"temperature", "max_output_tokens"}
                or config["structured_output"] not in {"off", "json_object", "json_schema"}
                or config["output_token_parameter"] not in {"max_tokens", "max_completion_tokens", "max_output_tokens"}):
            raise ValueError("Automatic route metadata must not contain credentials or service options")
        GenerationOptions.model_validate(config["generation"])
    attempts = {attempt.attempt_id: attempt for attempt in run.generation_attempts}
    if len(attempts) != len(run.generation_attempts) or set(run.chunk_provenance) != leaves:
        raise ValueError("Automatic provenance requires unique attempts and every final leaf")
    usage: dict[str, int] = {}
    chunk_calls: dict[str, int] = {}
    for attempt in attempts.values():
        if (attempt.route_id not in run.routes or attempt.status in {"reserved", "responded"}
                or not any(key == attempt.chunk_id or key.startswith(attempt.chunk_id + ".") for key in leaves)
                or any(value < 0 for value in attempt.usage.values())):
            raise ValueError("Automatic attempts must reference known routes and completed run branches")
        chunk_calls[attempt.chunk_id] = chunk_calls.get(attempt.chunk_id, 0) + 1
        expected_id = f"{attempt.chunk_id}.{attempt.route_id}.attempt-{chunk_calls[attempt.chunk_id]}"
        if (chunk_calls[attempt.chunk_id] > 3 or attempt.attempt_id != expected_id
                or attempt.http_attempts > 1
                or attempt.response_received and (attempt.http_attempts != 1 or attempt.outcome_unknown)
                or not attempt.response_received and attempt.usage
                or attempt.status in {"rejected", "completed"} and not attempt.response_received):
            raise ValueError("Automatic attempt bounds and response accounting must remain consistent")
        for name, count in attempt.usage.items():
            usage[name] = usage.get(name, 0) + count
    if (sum(attempt.response_received for attempt in attempts.values()) != run.response_count
            or sum(attempt.http_attempts for attempt in attempts.values()) != run.request_attempts
            or usage != run.usage):
        raise ValueError("Automatic attempt totals must match the extraction run")
    completed = {attempt.attempt_id for attempt in attempts.values() if attempt.status == "completed"}
    origins = set()
    for chunk_id, origin in run.chunk_provenance.items():
        attempt = attempts.get(origin.attempt_id)
        if (attempt is None or attempt.status != "completed" or not attempt.response_received
                or attempt.chunk_id != chunk_id or attempt.route_id != origin.route_id
                or attempt.model != origin.model or attempt.reported_model != origin.reported_model):
            raise ValueError("Chunk model provenance must identify its successful generation")
        origins.add(origin.attempt_id)
    if origins != completed:
        raise ValueError("Every successful generation must have exactly one final leaf source")
