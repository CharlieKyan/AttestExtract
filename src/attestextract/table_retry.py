"""Explicit one-shot table regeneration, with verified dev30 compatibility.

This never repairs a response. Originals and reservations survive in the new
checkpoint; a retry that times out or produces another rejection is not renewed.
"""
from __future__ import annotations

from copy import deepcopy

from .errors import ConfigurationError
from .execution_migration import _read
from .models import BackendResponse
from .table_routes import parse_route_response_partial


def validate_retry_options(backend, options, resume_from, table_mode):
    if resume_from is None or table_mode != "auto":
        raise ConfigurationError("retry_tables requires --resume and automatic table routing.")
    if options.max_model_calls is None:
        raise ConfigurationError("Table retry requires an explicit cumulative max_model_calls budget.")
    if getattr(backend, "max_attempts", 1) != 1:
        raise ConfigurationError("Table retry requires max_attempts=1; HTTP retries cannot be stacked.")


def read_original(execution_dir, key, node):
    from .execution import digest
    import re

    if not re.fullmatch(r"table-[0-9]{4,}", key):
        raise ValueError("table retry node must identify a table")
    if (node.table_retry_pending and (node.table_retries_reserved or node.status != "pending" or node.calls != 1)
            or node.table_retries_reserved and node.calls != 2
            or not node.table_retry_pending and not node.table_retries_reserved):
        raise ValueError("invalid table retry reservation")
    payload = _read(execution_dir, f"table-original/{key}.json")
    if digest(payload) != node.table_original_hash:
        raise ValueError("original table response checksum")
    response = BackendResponse.model_validate(payload)
    if response.finish_reason not in (None, "stop") or not parse_route_response_partial("A", response.text)["rejections"]:
        raise ValueError("original table response was not a complete rejected table")
    return response


def select_table_retries(session):
    from .document_routing import is_table_chunk
    from .execution import digest

    selected = []
    for key in session.leaves():
        node = session.state.nodes[key]
        if not is_table_chunk(session.chunks[key]):
            continue
        if node.table_retry_pending:
            selected.append(key)
        elif key in session.cached and not node.table_retries_reserved and node.calls == 1:
            parsed = parse_route_response_partial("A", session.cached[key].text)
            if parsed["rejections"]:
                selected.append(key)
    if not selected:
        raise ConfigurationError("No rejected table has an unused retry allowance; completed, unknown and previously retried tables are not regenerated.")
    if any(key not in selected and key not in session.cached for key in session.leaves()):
        raise ConfigurationError("Targeted table retry requires completed caches for all other chunks; unfinished or outcome-unknown chunks will not be resent by this operation.")
    if session.state.calls_used + len(selected) > session.state.max_model_calls:
        raise ConfigurationError("The cumulative model-call budget does not cover the selected table retries.")
    for key in selected:
        node = session.state.nodes[key]
        if node.table_retry_pending:
            continue
        response = session.cached.pop(key)
        session.table_originals[key] = response
        node.table_original_hash = digest(response.model_dump(mode="json"))
        if session.trace is not None:
            session.trace.write_json(f"execution/table-original/{key}.json", response.model_dump(mode="json"))
        node.status, node.cache_hash = "pending", None
        node.table_retry_pending = True
    session.retry_tables = True
    session.save()
    session.event("table_retry_planned", chunks=selected, additional_calls=len(selected),
                  **session.summary())


class Dev30TableMigration:
    """Only fully received, single-call dev30 Chat runs have this bridge."""
    def __init__(self, backend, task, generation, identity, text, structure):
        self.backend, self.task, self.generation = backend, task, generation
        self.identity, self.text, self.structure = identity, text, structure

    def migrate_identity(self, state, checkpoint, target):
        from .execution import digest

        if state.checkpoint_version != "3" or state.package_version != "0.1.0.dev30":
            raise ConfigurationError("Only verified dev30 v3 Chat table checkpoints can migrate for targeted retry.")
        old_identity = deepcopy(self.identity)
        old_identity["table_routing"] = {"mode": "auto", "revision": "dev29-table-a-2"}
        if state.fingerprint != digest(old_identity):
            raise ConfigurationError("The old table fingerprint does not match source, task, backend or planning settings.")
        if (state.roots != list(state.nodes) or any(node.status != "completed" or node.calls != 1
                or node.format_retries_reserved or node.table_original_hash is not None for node in state.nodes.values())):
            raise ConfigurationError("dev30 table migration requires complete single-call root responses; split, interrupted and regenerated runs need separate review.")
        directory = checkpoint.parent.parent
        manifest = _read(directory, "manifest.json")
        if (not isinstance(manifest, dict) or manifest.get("package_version") != "0.1.0.dev30"
                or manifest.get("status") not in {"partial_format_error", "format_error"}):
            raise ValueError("unverified dev30 partial run metadata")
        if _read(directory, "task.json") != self.task.model_dump(mode="json"):
            raise ValueError("saved task does not match")
        document = _read(directory, "document.json")
        if (not isinstance(document, dict) or document.get("text") != self.text
                or document.get("source_name") != self.identity["source_name"]):
            raise ValueError("saved document does not match")
        state.migrations.append({"from_checkpoint_version": "3", "from_package_version": "0.1.0.dev30",
                                 "from_fingerprint": state.fingerprint, "to_checkpoint_version": "3",
                                 "reason": "explicit_table_protocol_retry"})
        state.fingerprint = target.fingerprint

    def validate_requests(self, state, checkpoint, chunks):
        from .document_routing import table_input_for_chunk
        from .pipeline import _request
        from .schema_diagnostics import schema_request_info
        from .table_routes import _build_legacy_route_request

        directory = checkpoint.parent.parent
        for key in state.roots:
            chunk = chunks[key]
            table = table_input_for_chunk(self.text, chunk, self.structure)
            if table is not None:
                request = _build_legacy_route_request(self.task.model_dump(mode="json"), table, chunk_id=key)
                request = request.model_copy(update={"generation": self.generation})
            else:
                request = _request(self.task, chunk, self.generation, state.output_protocol)
            if _read(directory, f"requests/{key}.request.json") != self.backend.prepare_payload(request):
                raise ValueError("dev30 request is missing, redacted, or incompatible")
            if _read(directory, f"schema/{key}.request.json") != schema_request_info(request.output_schema, self.backend.structured_output):
                raise ValueError("dev30 requested schema does not match")
