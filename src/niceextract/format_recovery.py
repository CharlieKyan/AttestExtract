"""Import complete, previously rejected prose attempts into a fresh protocol.

The cache retains the actual responses. The union is a decoded view rebuilt
from those responses, with an occurrence map back to each model's record.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

from .document_routing import is_table_chunk
from .errors import ConfigurationError, InvalidResponseError
from .execution import _checkpoint_path, _read_state, digest
from .json_validation import load_json
from .models import BackendResponse, ContextOptions, TaskSpec


KIND = "prose-format-recovery-v1"


def recovery_entries(state):
    entries = {}
    for entry in state.migrations:
        if entry.get("kind") != KIND:
            continue
        key = entry["chunk_id"]
        node = state.nodes[key]
        if (key in entries or key not in state.roots or node.status != "completed"
                or node.calls != 2 or node.format_failures != 2 or node.format_retries_reserved != 1):
            raise ValueError("inconsistent recovered format ledger")
        entries[key] = entry
    return entries


def _bytes(directory, relative):
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError("artifact outside archived diagnostics")
    return path.read_bytes()


def _json(directory, relative):
    return load_json(_bytes(directory, relative).decode("utf-8"))


def read_recovery_cache(checkpoint, key, entry, primary):
    bundle = _json(checkpoint.parent, f"format-recovery/{key}.json")
    if (digest(bundle) != entry["bundle_sha256"] or bundle["chunk_id"] != key
            or len(bundle["attempts"]) != 2 or bundle["attempts"][0]["response"] != primary.model_dump(mode="json")):
        raise ValueError("damaged format recovery cache")
    return bundle


def merge_recovered(bundle, task):
    from .pipeline import _RawResponse, _parse_response, _check_task_records

    merged, positions, origins = [], {}, []
    for attempt, item in enumerate(bundle["attempts"], 1):
        response = BackendResponse.model_validate(item["response"])
        raw = _parse_response(response.text, output_protocol="flat-v1",
                              row_relation_protocol=task.row_relation_protocol)
        _check_task_records(raw, task, trace=None, chunk_id=bundle["chunk_id"], format_attempt=attempt)
        body = response.text.strip()
        fence = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", body, flags=re.IGNORECASE)
        original = load_json(fence.group(1) if fence else body)["records"]
        seen = Counter()
        for ordinal, record in enumerate(raw.records):
            fingerprint = digest(original[ordinal])
            occurrence = (fingerprint, seen[fingerprint])
            seen[fingerprint] += 1
            if occurrence not in positions:
                positions[occurrence] = len(merged)
                merged.append(record)
                origins.append([])
            origins[positions[occurrence]].append({"attempt": attempt, "record_index": ordinal})
    return _RawResponse(records=merged), origins


def _verify_source(directory, state, session, identity, generation):
    from .documents import Document
    from .planning import plan_chunks
    from .document_routing import routing_structure

    old = _json(directory, "task.json")
    expected = dict(identity["task"], statement_protocol="source-v1", row_relation_protocol="evidence-v1")
    document = Document.model_validate(_json(directory, "document.json"))
    if document.text != session.text or document.source_name != identity["source_name"]:
        raise ValueError("source mismatch")
    old_context = ContextOptions.model_validate(_json(directory, "context-policy.json"))
    mode = "auto" if "table_routing" in identity else "text"
    structure = routing_structure(session.text, mode, document.structure) or document.structure
    original_identity = dict(identity, task=old, context=old_context.model_dump(),
                             structure=structure.model_dump(mode="json") if structure else None)
    if (old != expected or state.fingerprint != digest(original_identity)
            or state.engine_revision != session.state.engine_revision
            or state.output_protocol != "flat-v1" or state.roots != session.state.roots
            or set(state.nodes) != set(state.roots) or state.history or state.migrations):
        raise ValueError("source, task, model, generation, or plan mismatch")
    old_task = TaskSpec.model_validate(old)
    old_chunks = {c.chunk_id: c for c in plan_chunks(session.text, old_task, generation, session.options,
                  max_chars=identity["max_chars"], table_rows_per_chunk=identity["table_rows_per_chunk"],
                  context=old_context, structure=structure, table_mode=mode)}
    if list(old_chunks) != state.roots:
        raise ValueError("archived planning mismatch")
    failed = []
    for key, node in state.nodes.items():
        chunk = old_chunks[key]
        if node.segments != chunk.segments or node.body_segments != chunk.body_segments or node.depth or node.children:
            raise ValueError("source spans mismatch")
        if node.calls == 0 and node.status == "pending":
            if (node.error_code or node.cache_hash or node.format_failures or node.format_feedback
                    or node.format_retries_reserved or node.table_original_hash or node.table_retry_pending
                    or node.table_retries_reserved):
                raise ValueError("invalid pending reservation")
            continue
        current = session.chunks[key]
        if (chunk.body_segments or chunk.segments) != (current.body_segments or current.segments):
            raise ValueError("recovery needs identical body ownership")
        if (node.status != "failed" or node.error_code != "invalid_response" or is_table_chunk(chunk)
                or node.cache_hash or node.calls != 2 or node.format_failures != 2 or node.format_retries_reserved != 1):
            raise ValueError("unaccounted completed, interrupted or unknown work")
        failed.append(key)
    if (not failed or len(failed) * 2 != state.calls_used or state.response_count != state.calls_used
            or state.calls_used > min(session.state.max_model_calls, state.max_model_calls)):
        raise ValueError("source budget ledger mismatch")
    return old_task, failed, old_chunks


def _read_attempt(directory, key, number, task, request, backend):
    from .pipeline import _parse_response, _format_feedback

    stem = key if number == 1 else f"{key}.format-2"
    req_path = f"requests/{stem}.request.json"
    request_bytes = _bytes(directory, req_path)
    archived = load_json(request_bytes.decode("utf-8"))
    prepare = getattr(backend, "prepare_payload", None)
    expected = prepare(request) if callable(prepare) else request.model_dump(mode="json")
    if archived != expected:
        raise ValueError("archived generation request mismatch")
    raw_path = f"responses/{stem}.txt"
    response_bytes = _bytes(directory, raw_path)
    metadata = _json(directory, f"responses/{stem}.meta.json")
    if metadata.pop("format_attempt") != number or metadata["finish_reason"] not in (None, "stop"):
        raise ValueError("incomplete or inconsistent completion")
    response = BackendResponse(text=response_bytes.decode("utf-8"), **metadata)
    if callable(prepare) and (response.model != archived["model"] or response.finish_reason != "stop"):
        raise ValueError("unexpected provider model or completion")
    validation = _json(directory, f"validation/{stem}.errors.json")
    allowed = {k for spec in task.record_types.values() for k in (*spec.fields, *spec.measurement_groups)}
    allowed.update({"values", "quotes", "measurements", "context_quote", "value_text", "unit_text", "unit_quote"})
    try:
        _parse_response(response.text, output_protocol="flat-v1", allowed_keys=allowed)
    except InvalidResponseError as exc:
        if exc.validation_details != validation or validation["phase"] != "records_contract":
            raise ValueError("response does not reproduce archived format diagnostic") from exc
    else:
        raise ValueError("archived attempt did not fail its historical format")
    provenance = {"request_file": req_path, "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
                  "response_file": raw_path, "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
                  "validation_file": f"validation/{stem}.errors.json", "response": response.model_dump(mode="json")}
    return response, provenance, _format_feedback(validation, task, "flat-v1")


def recover_format(source: str | Path, session, identity, backend, generation):
    from .pipeline import _request, _regeneration_request

    if session.task.row_relation_protocol != "explanation-v1" or session.task.statement_protocol != "source-v2":
        raise ConfigurationError("Format recovery requires a fresh source-v2 / explanation-v1 task.")
    path = _checkpoint_path(source)
    directory = path.parent.parent
    try:
        envelope = _json(directory, "execution/checkpoint.json")
        previous = _read_state(envelope)
        if (previous.checkpoint_version != "3" or _json(directory, "manifest.json")["status"] != "failed"
                or (directory / "analysis" / "checkpoint.json").exists()):
            raise ValueError("expected failed extraction before analysis")
        old_task, failed, old_chunks = _verify_source(directory, previous, session, identity, generation)
        bundles, all_responses = {}, []
        for key in failed:
            chunk = old_chunks[key]
            if _json(directory, f"chunks/{key}.json") != chunk.model_dump(mode="json"):
                raise ValueError("archived chunk mismatch")
            request = _request(old_task, chunk, generation, "flat-v1")
            bundle = {"kind": KIND, "source_diagnostics": str(directory), "checkpoint_sha256": envelope["sha256"],
                      "chunk_id": key, "source_chunk": chunk.model_dump(mode="json"),
                      "source_package_version": previous.package_version,
                      "source_body_segments": [s.model_dump() for s in (chunk.body_segments or chunk.segments)],
                      "source_statement_protocol": old_task.statement_protocol,
                      "source_relation_protocol": old_task.row_relation_protocol, "attempts": []}
            for attempt in (1, 2):
                response, item, feedback = _read_attempt(directory, key, attempt, old_task, request, backend)
                all_responses.append(response)
                bundle["attempts"].append(item)
                if attempt == 1:
                    request = _regeneration_request(request, feedback)
            _, bundle["record_origins"] = merge_recovered(bundle, session.task)
            bundles[key] = bundle
        usage = Counter()
        for response in all_responses:
            usage.update(response.usage)
        if (dict(usage) != previous.usage or any(v < 0 for v in usage.values())
                or sum(r.attempts for r in all_responses) != previous.request_attempts
                or all_responses[-1].model != previous.model):
            raise ValueError("response and checkpoint accounting mismatch")
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError, RecursionError, InvalidResponseError) as exc:
        raise ConfigurationError(f"Format recovery could not verify the original attempts ({exc.__class__.__name__}); no model call was made.") from exc

    state = session.state
    state.history.append(str(directory))
    state.calls_used, state.response_count = previous.calls_used, previous.response_count
    state.usage, state.model, state.request_attempts = previous.usage, previous.model, previous.request_attempts
    for key, bundle in bundles.items():
        first = BackendResponse.model_validate(bundle["attempts"][0]["response"])
        old_node = previous.nodes[key]
        state.nodes[key] = state.nodes[key].model_copy(update={
            "status": "completed", "calls": old_node.calls, "format_failures": old_node.format_failures,
            "format_retries_reserved": old_node.format_retries_reserved, "format_feedback": old_node.format_feedback,
            "cache_hash": digest(first.model_dump(mode="json"))})
        session.cached[key] = first
        session.format_recoveries[key] = bundle
        state.migrations.append({"kind": KIND, "chunk_id": key, "bundle_sha256": digest(bundle)})
        if session.trace is not None:
            session.trace.write_json(f"execution/format-recovery/{key}.json", bundle)
            session.trace.describe_artifact(f"execution/format-recovery/{key}.json", "original_format_attempts_and_record_origins")
            session._write_cache(key, first)
        session.event("format_recovered", chunk_id=key, prior_calls_charged=2,
                      merged_records=len(bundle["record_origins"]), new_calls=0)
    session.save()
