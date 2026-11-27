"""Prepare a reusable interpretation of field definitions without sending a paper."""

import json

from .errors import BackendError, ConfigurationError, TaskValidationError
from .json_validation import load_json
from .models import ExtractionRequest, FieldTypePlan, GenerationOptions


def missing_types(task):
    return [name for name in task.record_types if name not in task.field_types] if task.input_materials else []


def inherited_preparation(task, resume_from):
    """Reuse the saved interpretation only for the same user task, including declarations."""
    from .execution import _checkpoint_path
    from .task_input import task_from_json
    prior = _checkpoint_path(resume_from).parent.parent
    if not (prior / "task.json").is_file():
        return task, None
    saved = task_from_json((prior / "task.json").read_bytes().decode("utf-8"))
    if saved.field_types and saved.input_materials == task.input_materials:
        if all(saved.field_types.get(k) == v for k, v in task.field_types.items()):
            task = task.model_copy(update={"field_types": saved.field_types})
    path = prior / "field-preparation.json"
    if not path.is_file():
        return task, None
    receipt = load_json(path.read_bytes().decode("utf-8"))
    calls = receipt.get("calls_reserved")
    attempts = receipt.get("route_attempts")
    if (receipt.get("status") != "completed" or type(calls) is not int or calls < 1
            or (calls != 1 if attempts is None else len(attempts) != calls)
            or receipt.get("fields") != {k: v.model_dump() for k, v in saved.field_types.items()}):
        raise ConfigurationError("Cannot verify the prior field preparation receipt.")
    return task, receipt


def account_preparation(execution, charged):
    if charged and execution is not None and execution.max_model_calls is not None:
        if execution.max_model_calls <= charged:
            raise ConfigurationError("The total call budget must cover field preparation and at least one extraction call. A prepared task uses no preparation call.")
        return execution.model_copy(update={"max_model_calls": execution.max_model_calls - charged})
    return execution


def _generate_plan(backend, request, receipt, trace, limit):
    from .automatic import AutomaticBackend
    automatic = isinstance(backend, AutomaticBackend)
    route_id = backend.preferred_route_id if automatic else None
    attempted = set()
    while True:
        call = receipt["calls_reserved"] + 1
        route = backend.routes[route_id] if automatic else None
        stem = request.chunk_id if call == 1 else f"{request.chunk_id}.route-{call}"
        req = request.model_copy(update={"artifact_id": stem, "generation": route.generation if route else request.generation})
        attempt = {"route_id": route_id, "artifact": stem, "status": "reserved"}
        receipt.update(calls_reserved=call, status="reserved")
        if automatic:
            receipt.setdefault("route_attempts", []).append(attempt)
        if trace:
            trace.write_json("field-preparation.json", receipt, atomic=True)
            trace.event("field_preparation", "started", calls_reserved=call, field_count=len(receipt["fields_requested"]))
            trace.write_json(f"requests/{stem}.request.json", {"messages": req.messages})
        try:
            response = (route.backend if route else backend).generate(req)
        except BackendError as exc:
            attempt.update(status="failed", code=exc.code)
            receipt["status"] = "backend_error"
            if trace:
                trace.write_json("field-preparation.json", receipt, atomic=True)
            attempted.add(route_id)
            next_route = backend.next_route(route_id, exc, attempted) if automatic else None
            if next_route is None or limit is not None and call >= limit:
                raise
            route_id = next_route
            continue
        if trace and trace.directory and not (trace.directory / f"responses/{stem}.txt").is_file():
            trace.write_text(f"responses/{stem}.txt", response.text)
        attempt["status"] = "returned"
        return response, route


def prepare_types(task, backend, *, generation=None, trace=None, receipt=None, limit=None):
    """One bounded generation for all undeclared fields; user constraints keep priority."""
    if task.input_materials is None:
        raise ConfigurationError("Field type preparation requires a field_list task.")
    missing = missing_types(task)
    if not missing:
        return task
    fields = task.input_materials.field_list
    payload = {"task": task.description, "fields": {name: fields[name] for name in missing}}
    system = (
        "Read these research extraction field definitions once for all documents. Return JSON with "
        "field_types, an object keyed by the exact supplied field names. Each entry has kind: text "
        "(names, categories, identifiers including numeric-only codes and descriptions), numeric "
        "(actual measured or reported quantities), mixed (both), or unknown (definition unclear). "
        "For numeric or mixed fields use shapes scalar, range, bound, by default all three unless "
        "the user requests a narrower form. Use [] for shapes on text or unknown. Mean is a statistic, "
        "not a shape. Judge the definition and task, not the spelling of an example value. "
        "Return precisely the requested fields."
    )
    rule = {"type": "object", "additionalProperties": False, "required": ["kind", "shapes"],
            "properties": {"kind": {"enum": ["text", "numeric", "mixed", "unknown"], "type": "string"},
                           "shapes": {"type": "array", "items": {"type": "string",
                                                                 "enum": ["scalar", "range", "bound"]}}}}
    schema = {"type": "object", "additionalProperties": False, "required": ["field_types"],
              "properties": {"field_types": {"type": "object", "additionalProperties": False,
                             "properties": dict.fromkeys(missing, rule), "required": missing}}}
    request = ExtractionRequest(chunk_id="task-field-types", messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        output_schema=schema, generation=generation or GenerationOptions())
    receipt = receipt if receipt is not None else {}
    receipt.update(calls_reserved=0, fields_requested=missing)
    response, route = _generate_plan(backend, request, receipt, trace, limit)
    try:
        result = load_json(response.text)
        if (response.finish_reason not in (None, "stop", "completed") or not isinstance(result, dict)
                or set(result) != {"field_types"} or not isinstance(result["field_types"], dict)
                or set(result["field_types"]) != set(missing)):
            raise ValueError("incomplete response")
        rules = dict(task.field_types)
        for name, spec in result["field_types"].items():
            if not isinstance(spec, dict) or not {"kind"} <= set(spec) <= {"kind", "shapes"}:
                raise ValueError("invalid field type entry")
            rules[name] = FieldTypePlan.model_validate({**spec, "source": "model"})
    except (ValueError, RecursionError):
        if trace:
            trace.write_json("field-preparation.json", {**receipt, "status": "invalid_response"}, atomic=True)
        raise TaskValidationError("Field type preparation returned an invalid or incomplete plan. Inspect the saved response.") from None
    prepared = task.model_copy(update={"field_types": rules})
    if route and route.role == "primary":
        backend.preferred_route_id = route.route_id
        backend.remember_success(route.route_id)
    if trace:
        receipt.update(status="completed", model=response.model, usage=response.usage,
                       attempts=response.attempts, fields={k: v.model_dump() for k, v in rules.items()})
        trace.write_json("field-preparation.json", receipt, atomic=True)
        trace.event("field_preparation", "completed", calls_reserved=receipt["calls_reserved"], field_count=len(missing))
    return prepared
