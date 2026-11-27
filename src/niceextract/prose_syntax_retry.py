"""Subdivide a prose block only after two complete replies failed JSON syntax validation."""

from hashlib import sha256
from pathlib import Path

from .document_routing import is_table_chunk
from .errors import ConfigurationError, InvalidResponseError
from .json_validation import load_json


def _saved_syntax_hash(session, key, number, directory):
    from .pipeline import _parse_response

    stem = key if number == 1 else f"{key}.format-2"
    try:
        def read(relative):
            return load_json((directory / relative).read_bytes().decode("utf-8"))

        if read(f"chunks/{key}.json") != session.chunks[key].model_dump(mode="json"):
            raise ValueError("source ranges or chunk contents changed")
        meta = read(f"responses/{stem}.meta.json")
        diagnostic = read(f"validation/{stem}.errors.json")
        if meta.get("format_attempt") != number:
            raise ValueError("archived attempt number changed")
        if meta.get("finish_reason") != "stop" or diagnostic.get("phase") != "json_syntax":
            return None
        response = (directory / f"responses/{stem}.txt").read_bytes()
        try:
            _parse_response(response.decode("utf-8"), output_protocol=session.state.output_protocol,
                            row_relation_protocol=session.task.row_relation_protocol)
        except InvalidResponseError as exc:
            observed = exc.validation_details
            first, prior = observed["errors"][0], diagnostic["errors"][0]
            if (observed["phase"] == "json_syntax" and first["code"] == prior["code"] == "invalid_json"
                    and first.get("raw_offset") is not None and first["raw_offset"] == prior.get("raw_offset")):
                return sha256(response).hexdigest()
        raise ValueError("archived response does not reproduce its original syntax diagnostic")
    except (KeyError, OSError, TypeError, ValueError, IndexError) as exc:
        raise ConfigurationError("Cannot verify archived prose syntax failure; no split was performed.") from exc


def split_verified_syntax_failure(session, key):
    node = session.state.nodes[key]
    if (session.task.statement_protocol != "source-v2" or is_table_chunk(session.chunks[key])
            or node.status != "failed" or node.error_code != "invalid_response"
            or node.format_failures != 2 or node.format_retries_reserved != 1 or node.calls != 2):
        return []
    hashes, directories = [], []
    for number in (1, 2):
        if (key, number) in session.syntax_hashes:
            signature = session.syntax_hashes[key, number]
            origin = session.trace.directory if session.trace is not None else None
        else:
            # A regeneration may have been issued by a different invocation. Replay the
            # archived diagnosis only in the diagnostics that actually contain that attempt.
            stem = key if number == 1 else f"{key}.format-2"
            origin = next((Path(d) for d in reversed(session.state.history)
                           if (Path(d) / f"responses/{stem}.txt").is_file()), None)
            if origin is None:
                return []
            signature = _saved_syntax_hash(session, key, number, origin)
        if not signature:
            return []
        hashes.append(signature)
        directories.append(str(origin.resolve()) if origin is not None else None)

    receipt = {"kind": "prose-syntax-split-v1", "chunk_id": key,
               "source_diagnostics": directories[-1], "response_sha256": hashes}
    if directories[0] != directories[1]:
        receipt["attempt_diagnostics"] = directories
    return session.subdivide(key, syntax_receipt=receipt)
