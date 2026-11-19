"""Source-bound attribution proposals from actual, completed entity-first calls."""

from hashlib import sha256
from pathlib import Path

from .entity_first_protocol import Assignments
from .execution import digest
from .json_validation import load_json


class _ReceiptMismatch(ValueError):
    """A public, value-free coverage reason."""


def freeze_assignment(packet, answer):
    """Keep the exact submitted roster and returned JSON; source text is stored by offset."""
    windows = []
    for window in packet["windows"]:
        stored = dict(window)
        stored["end"] = window["start"] + len(stored.pop("text"))
        windows.append(stored)
    return {"packet": {**packet, "windows": windows}, "answer": answer}


def _embedded(source, report, call):
    stored = call["assignment_receipt"]
    windows = []
    for item in stored["packet"]["windows"]:
        window = dict(item)
        end = window.pop("end")
        start = window["start"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source):
            raise _ReceiptMismatch("invalid_window_range")
        windows.append({**window, "text": source[start:end]})
    packet = {"stage": call["stage"], "task": report["admission_task"], **stored["packet"], "windows": windows}
    return packet, stored["answer"]


def _artifact(run_dir, relative):
    if run_dir is None:
        raise FileNotFoundError(relative)
    root = Path(run_dir).resolve()
    path = (root / "diagnostics" / relative).resolve()
    if not path.is_relative_to(root / "diagnostics"):
        raise _ReceiptMismatch("artifact_outside_run")
    return path.read_bytes().decode("utf-8")


def _messages(payload):
    if not isinstance(payload, dict):
        raise _ReceiptMismatch("unsupported_request_envelope")
    if "messages" in payload:
        messages = payload["messages"]
        if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
            raise _ReceiptMismatch("unsupported_request_envelope")
        return messages
    # The Responses adapter adds only the 'message' envelope to each original message.
    items = payload.get("input")
    if not isinstance(items, list) or not all(isinstance(m, dict) and m.get("type") == "message" for m in items):
        raise _ReceiptMismatch("unsupported_request_envelope")
    return [{k: v for k, v in m.items() if k != "type"} for m in items]


def _archived(run_dir, call, stem):
    request = load_json(_artifact(run_dir, f"requests/{stem}.request.json"))
    messages = _messages(request)
    if digest(messages) != call["request_digest"]:
        raise _ReceiptMismatch("request_digest_mismatch")
    if len(messages) < 2 or messages[1]["role"] != "user":
        raise _ReceiptMismatch("missing_user_packet")
    packet = load_json(messages[1]["content"])
    meta = load_json(_artifact(run_dir, f"responses/{stem}.meta.json"))
    if (meta["format_attempt"] != call["format_attempt"] or meta["attempts"] != call["attempts"]
            or meta["usage"] != call["usage"] or (meta["finish_reason"] or "") != call["finish_reason"]
            or (meta.get("request_id") or "") != call["request_id"]
            or (meta.get("reported_model") or meta.get("model") or "") != call["model"]):
        raise _ReceiptMismatch("response_metadata_mismatch")
    return packet, load_json(_artifact(run_dir, f"responses/{stem}.txt"))


def _check_packet(source, report, blocks, call, packet, answer):
    if packet["stage"] != call["stage"] or packet["task"]["row_subject"] != report["table_subject"]:
        raise _ReceiptMismatch("packet_scope_mismatch")
    primary = []
    seen = set()
    for window in packet["windows"]:
        start, text, key = window["start"], window["text"], window["id"]
        end = start + len(text)
        if (type(start) is not int or not 0 <= start < end <= len(source)
                or source[start:end] != text or key in seen):
            raise _ReceiptMismatch("source_window_mismatch")
        seen.add(key)
        if window["role"] == "primary":
            primary.append(key)
            block = blocks[key]
            if (start, end, window["scope"], window["kind"]) != (
                    block["start"], block["end"], block["scope"], block["kind"]):
                raise _ReceiptMismatch("primary_window_mismatch")
    if primary != call["blocks"] or len(primary) != 1:
        raise _ReceiptMismatch("primary_window_mismatch")
    Assignments.model_validate(answer)
    if call["stage"] == "attributes":
        if len(packet["objects"]) != call["roster_size"]:
            raise _ReceiptMismatch("roster_size_mismatch")
        if len(answer["facts"]) != blocks[primary[0]]["reported_facts"]:
            raise _ReceiptMismatch("reported_fact_count_mismatch")


def collect_receipts(source, report, run_dir=None):
    """Return complete attribute/revisit packets and a coverage ledger, without network access.

    Historical metadata binds requests and reported usage but lacks a generation-time
    answer hash. The answer digest identifies the content actually read by this audit.
    """
    if sha256(source.encode("utf-8")).hexdigest() != report["source_sha256"]:
        raise ValueError("Admission audit source differs from the frozen run.")
    version = report.get("admission_receipt_version")
    if version not in (None, 1):
        raise ValueError("Unsupported admission receipt version.")
    run_id = report["admission_run_id"] if version else report["snapshot_id"]
    blocks = {}
    for block in report["blocks"]:
        blocks[block["block_id"]] = block
        for child in block.get("recovery", []):
            blocks[child["block_id"]] = {**child, "scope": block["scope"], "kind": block["kind"]}
    receipts, coverage = [], []
    for number, call in enumerate(report["calls"], 1):
        stage = call["stage"]
        if stage not in ("attributes", "revisit"):
            continue
        stem = f"entity-{stage}-{number:06d}"
        if call["format_attempt"] != 1:
            stem += f".format-{call['format_attempt']}"
        row = {"call": number, "stage": stage, "artifact_stem": stem,
               "blocks": call["blocks"], "status": "gap", "proposals": None}
        coverage.append(row)
        if call.get("artifact_stem", stem) != stem:
            row["reason"] = "invalid_artifact_stem"
            continue
        if call["state"] != "complete":
            row["reason"] = call["state"]
            continue
        try:
            if version:
                packet, answer = _embedded(source, report, call)
            else:
                packet, answer = _archived(run_dir, call, stem)
            _check_packet(source, report, blocks, call, packet, answer)
        except FileNotFoundError:
            row["reason"] = "missing_call_artifact"
            continue
        except _ReceiptMismatch as exc:
            row["reason"] = str(exc)
            continue
        except (ValueError, KeyError, IndexError, TypeError, OSError, RecursionError):
            # Diagnostics may contain private source text. Only export controlled reasons.
            row["reason"] = "invalid_call_artifact"
            continue
        receipt = {"id": f"{run_id}/{stem}", "call": number, "stage": stage, "artifact_stem": stem,
                   "packet": packet, "answer": answer, "request_digest": call["request_digest"],
                   "answer_digest": digest(answer)}
        receipts.append(receipt)
        row.update(status="complete", proposals=len(answer["facts"]), answer_digest=receipt["answer_digest"])
    return receipts, coverage
