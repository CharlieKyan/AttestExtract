"""Revalidate archived relation proposals against exactly their displayed source."""

from __future__ import annotations

from hashlib import sha256
import re

from .dataset_io import _read_json_object
from .execution import digest
from .json_validation import load_json
from .models import BackendResponse
from .verification_protocol import apply_decision, parse_unified_step
from .verification_sources import SourceIndex


def _checked(path, expected):
    value = _read_json_object(path)
    if digest(value) != expected:
        raise ValueError(f"Archived verification checksum mismatch in {path.name}.")
    return value


def revalidate_archived_relations(source, analysis, directory):
    """Return an enriched in-memory copy; keep old reviews, bindings and files intact."""
    if (analysis.get("revision") not in {"bounded-source-verification-v9", "bounded-source-verification-v10"}
            or not directory.is_dir()):
        return analysis
    groups = {group["job_id"]: group for group in analysis.get("review_groups", [])
              if any(review.get("proposal_type") == "relation" and
                     isinstance(review.get("raw_proposal"), dict) and
                     review["raw_proposal"].get("source_kind", "object") == "object"
                     for review in group["diagnostic"].get("reviews", []))}
    if not groups:
        return analysis
    source_hash = sha256(source.encode("utf-8")).hexdigest()
    if source_hash != analysis["source"]["sha256"]:
        raise ValueError("Archived verification source differs from the supplied text.")
    index = SourceIndex(source)
    observations = {row["observation_id"]: row for row in analysis["observations"]}
    registered = {row["object_id"]: row for row in analysis["objects"]}
    entries = {row["evidence_id"]: row for row in analysis["evidence"]}
    edges = {row["relation_id"]: row for row in analysis["relations"]}
    contexts = {(row["evidence_id"], row["artifact_id"]): row for row in analysis.get("evidence_contexts", [])}
    for job in analysis["jobs"]:
        if job["job_id"] not in groups:
            continue
        group = groups[job["job_id"]]
        last = job["steps"][-1]
        if (not re.fullmatch(r"verify-[0-9]+", job["job_id"]) or type(last["number"]) is not int
                or last.get("action") != "decide" or last.get("error_code") != "proposal_review"):
            raise ValueError("Invalid archived verification review entry.")
        artifact = f"{job['job_id']}.step-{last['number']:02d}"
        request_path = directory / "requests" / (artifact + ".json")
        cache_path = directory / "cache" / (artifact + ".json")
        # Diagnostics are optional in a copied run; never borrow the next
        # job's reply or fail a valid legacy export when one side is missing.
        if not request_path.is_file() or not cache_path.is_file():
            continue
        request = _checked(request_path, last["request_hash"])
        response = BackendResponse.model_validate(_checked(cache_path, last["response_hash"]))
        payload = load_json(request["messages"][-1]["content"])
        if (request["chunk_id"] != job["job_id"] or response.attempts != 1
                or response.finish_reason not in (None, "stop") or response.text != group["response_text"]
                or payload["target_scope"]["observation_ids"] != job["observation_ids"]
                or payload["table_subject"] != analysis["table_subject"]
                or digest(group["diagnostic"]) != last["validation_hash"]):
            raise ValueError("Archived request, response and review do not belong to the same verification decision.")
        step = parse_unified_step(response.text)
        shown = [*payload["initial_context"]["passages"], *payload["known_object_evidence"]]
        known = {row["object_id"]: row for row in payload["known_objects"]}
        for frame in payload["lookup_history"]:
            result = frame["result"]
            shown.extend(result.get("passages", []))
            shown.extend(result.get("matches", []))
            known.update({row["object_id"]: row for row in result.get("related_objects", [])})
        if any(registered.get(key) != row for key, row in known.items()):
            raise ValueError("Displayed registry objects differ from the frozen analysis.")
        evidence_contexts = {}
        accepted_edge_ids = {}
        _, accepted, _, located = apply_decision(step, source_text=source, source_hash=source_hash,
            batch=[observations[key] for key in job["observation_ids"]], objects=list(known.values()),
            relations=[], entries=entries, source_index=index, visible_passages=shown,
            shown_objects=set(known), citation_passages=shown,
            evidence_contexts=evidence_contexts, review_items=[], accepted_edge_ids=accepted_edge_ids)
        recovered_ids = {key for number, key in accepted_edge_ids.items() if any(
            review["path"] == f"relations[{number}]" and review["raw_proposal"] == step._relation_inputs[number]
            for review in group["diagnostic"]["reviews"] if review["proposal_type"] == "relation")}
        for edge in accepted:
            if edge["relation_id"] in edges or edge["relation_id"] not in recovered_ids:
                continue
            if edge["source_id"] not in registered or edge["target_id"] not in registered:
                continue
            edges[edge["relation_id"]] = {**edge, "replayed_from": artifact,
                "request_hash": last["request_hash"], "response_hash": last["response_hash"]}
            refs = set(edge["evidence_ids"])
            refs.update(edge[field] for field in ("source_mention_id", "target_mention_id") if field in edge)
            for key in sorted(refs):
                entries[key] = located[key]
                context = evidence_contexts[key]
                contexts[key, artifact] = {**context, "artifact_id": artifact}
    return {**analysis, "relations": list(edges.values()), "evidence": list(entries.values()),
            "evidence_contexts": list(contexts.values())}
