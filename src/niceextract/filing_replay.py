"""Recheck a frozen optional filing proposal against the extraction input that produced it."""

import re
from hashlib import sha256
from pathlib import Path

from .contextual_chunking import merge_spans
from .document_routing import table_input_for_chunk
from .errors import ConfigurationError
from .execution import _read_state, digest
from .format_recovery import KIND, read_recovery_cache
from .json_validation import load_json
from .models import BackendResponse, DocumentChunk
from .row_relations import locate_relation, read_open_relation
from .structure import DocumentStructure


def _shown(source, chunk, payload, directory):
    original = DocumentChunk.model_validate(chunk)
    if original.text != "".join(source[s.start:s.end] for s in original.segments):
        return []
    scopes = merge_spans(original.segments)
    for span in [*original.body_segments, *(s.span for s in original.context_segments)]:
        if not any(s.start <= span.start < span.end <= s.end <= len(source) for s in scopes):
            return []
    table = payload.get("table")
    if table is not None:
        saved = directory / "structure.json"
        structure = DocumentStructure.model_validate_json(saved.read_bytes()) if saved.is_file() else None
        expected = table_input_for_chunk(source, original, structure)
        if not expected or any(table.get(k) != expected.get(k) for k in
                               ("source_sha256", "table_id", "table_source", "start", "end", "entries", "window")):
            return []
        entries = table.get("entries", [])
        if any(not 0 <= e["start"] < e["end"] <= len(source) or source[e["start"]:e["end"]] != e["quote"]
               for e in entries if e["quote"]):
            return []
        return [(e["ref"], e["start"], e["quote"]) for e in entries if e["quote"]]
    visible = payload.get("source")
    if isinstance(visible, str):
        spans = chunk["segments"]
        if visible != "".join(source[s["start"]:s["end"]] for s in spans):
            return []
    else:
        roles = [("body", s) for s in chunk.get("body_segments", [])]
        roles.extend((s["role"], s["span"]) for s in chunk.get("context_segments", []))
        if visible != [{"role": role, "text": source[s["start"]:s["end"]]} for role, s in roles]:
            return []
        spans = [s for _, s in roles]
    return [(None, s["start"], source[s["start"]:s["end"]]) for s in spans
            if 0 <= s["start"] < s["end"] <= len(source)]


def _attempts(source, directory, state, key):
    node = state.nodes.get(key)
    cache_path = directory / "execution" / "cache" / (key + ".json")
    if node is None or node.status != "completed" or not cache_path.is_file():
        return
    cache = load_json(cache_path.read_text(encoding="utf-8"))
    if digest(cache) != node.cache_hash:
        return
    chunk_path = directory / "chunks" / (key + ".json")
    if not chunk_path.is_file():
        return
    chunk = load_json(chunk_path.read_text(encoding="utf-8"))
    if (chunk.get("chunk_id") != key or chunk.get("segments") != [s.model_dump() for s in node.segments]
            or chunk.get("body_segments", []) != [s.model_dump() for s in node.body_segments]):
        return
    recovered = [m for m in state.migrations if m.get("chunk_id") == key and m.get("kind") == KIND]
    if recovered:
        if len(recovered) != 1:
            return
        bundle = read_recovery_cache(directory / "execution" / "checkpoint.json", key, recovered[0],
                                     BackendResponse.model_validate(cache))
        original_dir = Path(bundle["source_diagnostics"])
        if not original_dir.is_absolute() or bundle["source_chunk"].get("chunk_id") != key:
            return
        chunk = bundle["source_chunk"]
        items = [(key if i == 0 else key + ".format-2", original_dir, item["response"]["text"], item)
                 for i, item in enumerate(bundle["attempts"])]
    else:
        attempt = key + (".format-2" if node.format_retries_reserved else "")
        items = [(attempt, directory, cache["text"], {})]
    verified = []
    for attempt, origin, accepted, hashes in items:
        request = origin / "requests" / (attempt + ".request.json")
        answer = origin / "responses" / (attempt + ".txt")
        if not request.is_file() or not answer.is_file():
            continue
        raw_request, raw_answer = request.read_bytes(), answer.read_bytes()
        if raw_answer.decode("utf-8") != accepted:
            continue
        if hashes and (sha256(raw_request).hexdigest() != hashes["request_sha256"] or
                       sha256(raw_answer).hexdigest() != hashes["response_sha256"]):
            continue
        messages = load_json(raw_request.decode("utf-8"))["messages"]
        prompt = next((m["content"] for m in messages if m["role"] == "user"), None)
        if not prompt:
            continue
        shown = _shown(source, chunk, load_json(prompt), origin)
        if not shown:
            continue
        response = accepted.strip()
        fence = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", response, flags=re.IGNORECASE)
        try:
            original = load_json(fence.group(1) if fence else response)
        except ValueError:
            continue  # A failed format attempt cannot be the source of a checked record.
        if isinstance(original, dict) and isinstance(original.get("records"), list):
            answers = original["records"]
            if recovered:
                number = 1 if attempt == key else 2
                retained = {item["record_index"] for origins in bundle["record_origins"] for item in origins
                            if item["attempt"] == number}
                answers = [record if i in retained else None for i, record in enumerate(answers)]
            verified.append((attempt, shown, answers))
    if len(verified) == len(items):
        yield from verified


def _same_fact(answer, row, index):
    if not isinstance(answer, dict) or answer.get("record_type") != row.get("record_type"):
        return False
    if "record_id" in row:
        if index not in row.get("input_indices", []) or answer.get("record_id") not in row.get("record_ids", []):
            return False
        from .table_routes import TableRecord
        def identity(record):
            return TableRecord.model_validate({k: record[k] for k in TableRecord.model_fields}).model_dump()
        try:
            return any(identity(record) == identity(answer) for record in row["original_records"])
        except (KeyError, ValueError):
            return False
    fields = row.get("fields")
    if not fields or set(answer.get("values", {})) != set(fields) or set(answer.get("quotes", {})) != set(fields):
        return False
    return (digest(answer["values"]) == digest({k: f["value"] for k, f in fields.items()})
            and all(answer["quotes"][k] == f["evidence"]["quote"] for k, f in fields.items())
            and all(row.get(k) == answer.get(k) for k in ("statement_basis", "reported_use", "source_property")))


def replay_filing_issues(source, result, directory):
    if result["task"].get("statement_protocol") != "source-v2" or not directory.is_dir():
        return {}
    records = [(f"/candidates/{i}", row, row.get("chunk_ids", []))
               for i, row in enumerate(result["candidates"]) if row.get("row_relation_issues")]
    for i, table in enumerate(result.get("table_results", [])):
        records.extend((f"/table_results/{i}/candidates/{j}", row, [table["chunk_id"]])
                       for j, row in enumerate(table["candidates"]) if row.get("row_relation_issues"))
    checkpoint = directory / "execution" / "checkpoint.json"
    if not records or not checkpoint.is_file():
        return {}
    try:
        state = _read_state(load_json(checkpoint.read_text(encoding="utf-8")))
    except (ConfigurationError, ValueError):
        return {}
    replayed, cached = {}, {}
    for pointer, row, ids in records:
        if len(ids) != 1 or not re.fullmatch(r"(?:prose|chunk|table)-\d+(?:\.[1-9]\d*)*", ids[0]):
            continue
        key = ids[0]
        if row.get("row_relations") or len(row["row_relation_issues"]) != 1:
            continue
        if key not in cached:
            try:
                cached[key] = list(_attempts(source, directory, state, key))
            except (KeyError, TypeError, ValueError, OSError, ConfigurationError):
                cached[key] = []
        raw = row["row_relation_issues"][0]["raw"]
        matches = [(attempt, shown, i) for attempt, shown, answers in cached[key] for i, answer in enumerate(answers)
                   if isinstance(answer, dict) and answer.get("row_relation") == raw and _same_fact(answer, row, i)]
        proposal, errors = read_open_relation(raw)
        if len(matches) == 1 and proposal is not None and not errors:
            attempt, shown, index = matches[0]
            found = [r.model_dump() for r in locate_relation(proposal, shown)]
            replayed[pointer] = {"attempt": attempt, "record_index": index, "receipts": found}
    return replayed
