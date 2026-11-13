"""Verify unchanged root requests before enabling table-window recovery.

The bridge reads only the supplied run. It never follows a history path or
changes the generation limit, task, backend identity, or used call budget.
"""
from copy import deepcopy
import hashlib
import json

from .errors import ConfigurationError
from .execution_migration import _read


class TableWindowMigration:
    def __init__(self, backend, task, generation, identity, text, structure, *, allow_dev30=False):
        self.backend, self.task, self.generation = backend, task, generation
        self.identity, self.text, self.structure = identity, text, structure
        self.allow_dev30 = allow_dev30
        self.delegate = None

    def migrate_identity(self, state, checkpoint, target):
        from .execution import digest
        if state.package_version == "0.1.0.dev30" and self.allow_dev30:
            from .table_retry import Dev30TableMigration
            self.delegate = Dev30TableMigration(self.backend, self.task, self.generation,
                                                self.identity, self.text, self.structure)
            self.delegate.migrate_identity(state, checkpoint, target)
            return
        if (state.checkpoint_version not in {"3", "4"}
                or state.package_version not in {"0.1.0.dev31", "0.1.0.dev32"}):
            raise ConfigurationError("Resume identity mismatch: table-window recovery can migrate verified dev31/dev32 root checkpoints only.")
        old = deepcopy(self.identity)
        old["table_routing"] = {"mode": "auto", "revision": "dev31-table-a-3"}
        if state.fingerprint != digest(old):
            raise ConfigurationError("Resume identity mismatch: the prior table source, task, model, generation or planning settings changed.")
        if (state.roots != list(state.nodes) or any(
                node.calls > 1 or node.format_failures or node.format_retries_reserved
                or node.table_original_hash is not None or node.table_retry_pending
                or node.table_retries_reserved for node in state.nodes.values())):
            raise ConfigurationError("Legacy table-window migration requires unmodified, single-attempt root chunks; previously split or regenerated runs need separate verification.")
        directory = checkpoint.parent.parent
        manifest = _read(directory, "manifest.json")
        if (not isinstance(manifest, dict) or manifest.get("package_version") != state.package_version
                or manifest.get("trace_schema_version") != "0.3"
                or manifest.get("status") not in {"failed", "succeeded", "partial_format_error", "format_error"}):
            raise ValueError("unverified legacy run metadata")
        if _read(directory, "task.json") != self.task.model_dump(mode="json"):
            raise ValueError("saved task differs")
        document = _read(directory, "document.json")
        if (not isinstance(document, dict) or document.get("text") != self.text
                or document.get("source_name") != self.identity["source_name"]):
            raise ValueError("saved document differs")
        state.migrations.append({"from_checkpoint_version": state.checkpoint_version,
                                 "from_package_version": state.package_version,
                                 "from_fingerprint": state.fingerprint,
                                 "to_checkpoint_version": state.checkpoint_version,
                                 "reason": "verified_table_window_recovery"})
        state.fingerprint = target.fingerprint

    def validate_requests(self, state, checkpoint, chunks):
        if self.delegate is not None:
            self.delegate.validate_requests(state, checkpoint, chunks)
            return
        from .document_routing import table_input_for_chunk
        from .pipeline import _request
        from .schema_diagnostics import schema_request_info
        from .table_routes import build_route_request

        directory = checkpoint.parent.parent
        truncations = {}
        for key, node in state.nodes.items():
            if not node.calls:
                continue
            backend, generation, stem = self.backend, self.generation, key
            if state.checkpoint_version == "4":
                attempts = [a for a in state.generation_attempts if a.chunk_id == key]
                if len(attempts) != 1 or attempts[0].format_attempt != 1:
                    raise ValueError("legacy automatic attempts cannot be reconstructed")
                route = self.backend.routes[attempts[0].route_id]
                backend, generation, stem = route.backend, route.generation, attempts[0].attempt_id
            chunk = chunks[key]
            table = table_input_for_chunk(self.text, chunk, self.structure)
            request = _request(self.task, chunk, generation, state.output_protocol)
            if table is not None:
                request = build_route_request("A", self.task.model_dump(mode="json"), table, chunk_id=key)
                request = request.model_copy(update={"generation": generation})
            if _read(directory, f"requests/{stem}.request.json") != backend.prepare_payload(request):
                raise ValueError("legacy request missing, redacted or incompatible")
            if _read(directory, f"schema/{stem}.request.json") != schema_request_info(request.output_schema, backend.structured_output):
                raise ValueError("legacy response contract differs")
            if table is not None and node.error_code == "truncated_response":
                metadata = _read(directory, f"responses/{stem}.meta.json")
                if not isinstance(metadata, dict) or metadata.get("finish_reason") != "length":
                    raise ValueError("table failure was not a known length truncation")
                path = (directory / f"responses/{stem}.txt").resolve()
                if not path.is_relative_to(directory.resolve()):
                    raise ValueError("failure evidence outside supplied run")
                truncations[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        if truncations:
            state.migrations[-1]["verified_truncated_text_sha256"] = json.dumps(truncations, sort_keys=True)
