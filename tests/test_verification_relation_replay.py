"""A frozen request may lend only its own checked citations to a reread edge."""

from hashlib import sha256
import json

import pytest

from niceextract.analysis import _evidence
from niceextract.execution import digest
from niceextract.verification import _attach_source_citations
from niceextract.verification_protocol import apply_decision, parse_unified_step
from niceextract.verification_replay import revalidate_archived_relations
from niceextract.verification_sources import SourceIndex


def test_replay_copies_new_endpoint_markers_and_checks_the_archived_request(tmp_path):
    source = "Grain G came from Mural M."
    index = SourceIndex(source)
    passage, = index.passages
    def q(words):
        return {"quote": words, "passage_id": passage["passage_id"]}
    entries = {}
    span = {"start": 0, "end": len(source)}
    citation = _evidence(source, {**span, "quote": source}, entries)
    batch = [{"observation_id": "obs", "entity": "Grain G", "evidence_ids": [citation],
              "source_evidence": [{"location_status": "located", "span": span}]}]
    shown = [{**passage}]
    _attach_source_citations(batch, entries, shown)
    local_objects = [
        {"object_id": key, "name": name, "kind": "instance", "scope": "wall",
         "is_table_subject": row, "mentions": [q(source)]}
        for key, name, row in (("g", "Grain G", False), ("m", "Mural M", True))]
    binding = {"observation_id": "obs", "subject_id": "g", "status": "supported",
               "evidence": [q(source)], "reason": "This grain is the specimen.", "target_match": "supported"}
    answer = {"action": "decide", "question": "", "objects": local_objects, "bindings": [binding]}
    source_hash = sha256(source.encode()).hexdigest()
    registered, _, _, entries = apply_decision(parse_unified_step(json.dumps(answer)),
        source_text=source, source_hash=source_hash, batch=batch, objects=[], relations=[],
        entries=entries, source_index=index, visible_passages=shown)
    edge = {"source_id": "g", "target_id": "m", "predicate": "sample_of",
            "evidence": [{"evidence_id": citation}], "mention": q("Grain G"), "target_mention": q("Mural M")}
    answer["relations"] = [edge]
    response = {"text": json.dumps(answer)}
    payload = {"initial_context": {"passages": shown}, "known_object_evidence": [], "known_objects": [],
               "lookup_history": [], "table_subject": "artworks", "target_scope": {"observation_ids": ["obs"]}}
    request = {"chunk_id": "verify-0001", "messages": [{"content": json.dumps(payload)}]}
    diagnostic = {"reviews": [{"path": "relations[0]", "raw_proposal": edge, "proposal_type": "relation"}]}
    job = {"job_id": "verify-0001", "observation_ids": ["obs"], "steps": [
        {"number": 1, "action": "decide", "error_code": "proposal_review", "request_hash": digest(request),
         "response_hash": digest(response), "validation_hash": digest(diagnostic)}]}
    snapshot = {"revision": "bounded-source-verification-v9", "source": {"sha256": source_hash},
                "jobs": [job], "table_subject": "artworks", "relations": [], "objects": registered,
                "evidence": list(entries.values()), "observations": batch, "review_groups": [
                    {"job_id": "verify-0001", "response_text": response["text"], "diagnostic": diagnostic}]}
    for directory, content in (("requests", request), ("cache", response)):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "verify-0001.step-01.json").write_text(json.dumps(content), encoding="utf8")
    enriched = revalidate_archived_relations(source, snapshot, tmp_path)
    recovered, = enriched["relations"]
    refs = {row["evidence_id"]: row["quote"] for row in enriched["evidence"]}
    contexts = {row["evidence_id"] for row in enriched["evidence_contexts"]}
    for field, name in (("source_mention_id", "Grain G"), ("target_mention_id", "Mural M")):
        assert recovered[field] not in recovered["evidence_ids"]
        assert refs[recovered[field]] == name and recovered[field] in contexts
    assert not snapshot["relations"] and len(snapshot["evidence"]) == 1
    cached = tmp_path / "cache" / "verify-0001.step-01.json"
    cached.unlink()
    incomplete = revalidate_archived_relations(source, snapshot, tmp_path)
    assert not incomplete["relations"] and incomplete["review_groups"] == snapshot["review_groups"]
    cached.write_text(json.dumps(response), encoding="utf8")
    (tmp_path / "requests" / "verify-0001.step-01.json").write_text("{}", encoding="utf8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        revalidate_archived_relations(source, snapshot, tmp_path)
