"""Publish entity-first results through the ordinary candidate, dataset and run interfaces."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import time

from .dataset import _POLICY_COLUMNS, _project
from .documents import Document
from .errors import ConfigurationError
from .execution import digest
from .identity import grounded_candidate_id
from .models import Candidate, CheckResult, EvidenceRef, ExtractionResult, FieldValue, ReviewItem, RunInfo, SourceInfo


def validate_strategy(strategy, rescue, *, subject, task, options, resume, context, tables,
                      analysis_steps, table_rows, execution):
    if strategy not in {"observation-first", "entity-first"} or type(rescue) is not bool:
        raise ConfigurationError("Choose observation-first or entity-first and a boolean entity_rescue.")
    if strategy != "entity-first":
        if not rescue:
            raise ConfigurationError("Disabling entity rescue requires the entity-first strategy.")
        return
    if task.input_materials is None or not isinstance(subject, str) or not subject.strip():
        raise ConfigurationError("Entity-first requires a simple field_list task and table_subject.")
    if (options or {}).get("row_subject", subject) != subject:
        raise ConfigurationError("Entity-first row_subject must agree with table_subject.")
    if resume or context.mode != "none" or tables != "auto" or analysis_steps != 4 or table_rows != 12:
        raise ConfigurationError("Entity-first uses fresh source blocks. Legacy resume, table retries, context,"
                                 " verification steps and custom table window options are unsupported.")
    if execution and (not execution.adaptive_chunking or execution.max_split_depth != 3
                      or execution.min_chunk_chars != 500 or execution.planning_output_tokens != 8192):
        raise ConfigurationError("The entity-first planner accepts max_model_calls and max_chunk_chars;"
                                 " legacy adaptive planner overrides are unsupported.")


def dataset_view(report, task, info, options):
    fields = task.input_materials.field_list
    label = options.get("subject_label_field")
    identity = options.get("identity_fields") or ([label] if label else [])
    policies = options.get("field_policies", {})
    objects = {obj["object_id"]: obj for obj in report["objects"]}
    evidence = {}
    observations = []
    for i, fact in enumerate(report["observations"]):
        obj = objects.get(fact["owner_id"])
        decision = {"status": "supported" if obj else "unknown",
                    "id": obj["object_id"] if obj else None, "label": obj["label"] if obj else None,
                    "scope": obj["scope"] if obj else None, "evidence": obj["evidence"] if obj else [],
                    "basis": "model_proposed"}
        blockers = [key for key, allowed in policies.get(fact["field"], {}).items()
                    if fact.get(_POLICY_COLUMNS[key]) not in allowed]
        if obj and fact["field"] == label and fact["value"] not in {obj["label"], *(a["name"] for a in obj["aliases"])}:
            blockers.append("conflicting_subject_label")
        fits = fact["field_supported"] and not blockers
        fit_status = "supported" if fits else "contradicted" if fact["field_fit"] == "unsupported" else "unknown"
        observations.append({**fact, "source_record": f"/candidates/{i}", "direct": decision, "target": decision,
                             "record_group": [fact["block_id"], fact["owner_source"], fact["purpose"]],
                             "fit_blockers": blockers,
                             "fit": {"status": fit_status, "reason": fact["reason"],
                                     "evidence": fact["evidence"], "basis": "model_proposed"},
                             "related": [], "candidates": [], "eligible": bool(obj and fits and fact["qualified"]),
                             "row_ids": []})
        for span in fact["evidence"] + decision["evidence"]:
            evidence[(span["start"], span["end"])] = span
    rows, lineage = _project(fields, observations, identity, info.sha256, policies, label)
    return {"dataset_version": "1", "subject_strategy": "entity-first", "document_id": info.document_id,
            "source_sha256": info.sha256, "schema": list(fields), "field_definitions": fields,
            "row_subject": report["table_subject"], "identity_fields": list(identity), "subject_label_field": label,
            "field_policies": policies, "row_identity_location": "schema_columns" if identity else "lineage_rows_in_csv_order",
            "rows": rows, "observations": observations, "coverage_gaps": report["issues"],
            "lineage": {"rows": lineage, "objects": report["objects"], "evidence": list(evidence.values()),
                        "relations": [], "quarantined": []},
            "budget": {"assembly_calls": 0, "entity_first": report["budget"],
                       "extraction_http_attempts": report["budget"]["http_attempts"]}}


def project_archive(source, result, options):
    from .dataset_io import check_dataset_request
    parsed = ExtractionResult.model_validate(result)
    report = dict(parsed.entity_first)
    checksum = report.pop("snapshot_id", None)
    if (report.get("source_sha256") != sha256(source.encode("utf-8")).hexdigest()
            or report["source_sha256"] != parsed.source.sha256 or len(source) != parsed.source.character_count
            or checksum != digest([parsed.task.model_dump(mode="json"), report])):
        raise ValueError("Entity-first source, task or frozen report differs from its archived snapshot.")
    options = {**report["dataset_options"], **options}
    check_dataset_request(parsed.task, options)
    if options.get("row_subject", report["table_subject"]) != report["table_subject"]:
        raise ValueError("Entity-first frozen bindings require the original row_subject.")
    if len(parsed.candidates) != len(report["observations"]):
        raise ValueError("Entity-first candidate count differs from the frozen report.")
    for candidate, obs in zip(parsed.candidates, report["observations"]):
        fields = {k: _field(obs[k], obs["evidence"]) for k in ("entity", "value", "unit", "conditions")}
        if (candidate.record_type != obs["field"] or candidate.fields != fields
                or candidate.candidate_id != grounded_candidate_id(parsed.source.sha256, obs["field"], fields)):
            raise ValueError("Entity-first candidate differs from its frozen observation.")
    return dataset_view(report, parsed.task, parsed.source, options)


def _field(value, evidence):
    span = next((e for e in evidence if value and e["quote"] == value), None)
    if span is None:
        span = next((e for e in evidence if value and value in e["quote"]), None)
    if span is None:
        ref = EvidenceRef(status="missing", quote="")
    else:
        loc = {"start": span["start"], "end": span["end"]}
        ref = EvidenceRef(quote=span["quote"], status="matched", matches=[loc], **loc)
    return FieldValue(value=value, evidence=ref)


def extract_entities(text, task, *, backend, source_name, table_subject, rescue, max_calls, max_chars,
                     input_budget, generation, format_retries, dataset_options, trace):
    from .entity_first import run_entity_first

    if getattr(backend, "name", None) == "automatic" or getattr(backend, "max_attempts", 1) != 1:
        raise ConfigurationError("Entity-first requires a fixed single-attempt backend or model preset.")
    if isinstance(text, Document):
        if source_name is not None and source_name != text.source_name:
            raise ConfigurationError("source_name must agree with the supplied Document.")
        document = text
    else:
        representation = "text/markdown" if source_name and source_name.lower().endswith(".md") else "text/plain"
        document = Document.from_text(text, source_name=source_name, representation=representation)
    source = document.text
    info = SourceInfo(name=document.source_name, document_id=document.document_id, character_count=len(source),
                      sha256=sha256(source.encode("utf-8")).hexdigest())
    started = time.perf_counter()
    report = run_entity_first(source, task.input_materials.field_list, table_subject, backend,
                              max_calls=max_calls, rescue=rescue, max_chars=max_chars, input_budget=input_budget,
                              task_description=task.description, examples=task.input_materials.model_dump(mode="json")["examples"],
                              generation=generation, format_retries=format_retries,
                              secrets=trace._secrets if trace else ())
    report["table_subject"] = table_subject
    report["source_sha256"] = info.sha256
    report["dataset_options"] = dataset_options or {}
    report["snapshot_id"] = digest([task.model_dump(mode="json"), report])
    dataset = dataset_view(report, task, info, dataset_options or {})
    reviews = []
    candidates = []
    for item in dataset["observations"]:
        fields = {k: _field(item[k], item["evidence"]) for k in ("entity", "value", "unit", "conditions")}
        identifier = grounded_candidate_id(info.sha256, item["field"], fields)
        if not item["row_ids"]:
            reviews.append(ReviewItem(candidate_id=identifier, code="entity_first_detail", message=item["reason"]))
        candidates.append(Candidate(candidate_id=identifier, record_type=item["field"], fields=fields,
                          chunk_ids=[item["block_id"]], checks=[CheckResult(code="entity_first_eligible",
                          passed=bool(item["row_ids"]))], needs_review=not item["row_ids"]))
    budget = report["budget"]
    result = ExtractionResult(task=task, source=info, candidates=candidates, review=reviews,
                              entity_first=report, dataset=dataset,
                              run=RunInfo(backend=backend.name, mode=getattr(backend, "mode", "live"),
                                          output_protocol="entity-first-v1", chunk_count=len(report["blocks"]),
                                          response_count=budget["response_count"], request_attempts=budget["http_attempts"],
                                          candidate_count=len(candidates), usage=budget["usage"], model=budget.get("model"),
                                          elapsed_seconds=time.perf_counter() - started))
    return result


def report_heading(report):
    counts = Counter(obj["stage"] for obj in report["objects"])
    return (f"# Entity-first extraction\n\nCoverage status: **{report['status']}**. "
            f"Inventory objects: {counts['inventory']}. Recovered objects: {counts['rescue']}. "
            f"Inspect nominations ({len(report['nominations'])}) and block coverage in [entity-first.json](entity-first.json). "
            "Open [dataset rows](dataset/main.csv) and [source observations](dataset/observations.csv). "
            "Source-linked model proposals require scientific review.\n\n")
