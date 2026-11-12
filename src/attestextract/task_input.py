"""Offline compilation of a complete field list and user-labelled observations.

This module does not interpret prose, discover targets, or call a model. Evidence
checks locate example quotations; they do not establish that labels are true.
"""

from __future__ import annotations

import json
import warnings
from typing import Any

from pydantic import ValidationError

from .errors import TaskValidationError
from .json_validation import JSONValidationError, load_json
from .models import (
    DocumentChunk, FieldSpec, RecordSpec, SimpleTaskInput, SourceSpan,
    TaskExampleExtraction, TaskInputMaterials, TaskSpec,
)
from .validation import error_path


OBSERVATION_FIELDS = ("entity", "value", "unit", "conditions")
_COMPILED_NAME = "observations"
_DESCRIPTIONS = {
    "entity": "The explicitly stated subject of this observation: object, sample, layer, site, or group; null if unreported.",
    "value": "The reported original value, range, or qualitative description as a string. Preserve explicitly reported statistics separately; do not calculate missing values.",
    "unit": "The original unit for this value; null if unreported. Do not convert units.",
    "conditions": "Explicit conditions, direction, scenario, scope, or statistic (such as range or mean) applying to this observation; null if unreported.",
}


class TaskInputWarning(UserWarning):
    """An example is usable but needs the task author's attention."""


def observation_values(extraction: TaskExampleExtraction) -> dict[str, str | None]:
    """Return all four fields; omitted or blank optional fields are unreported."""
    return {
        name: value if isinstance(value := getattr(extraction, name), str) and value.strip() else None
        for name in OBSERVATION_FIELDS
    }


def observation_quotes(extraction: TaskExampleExtraction) -> dict[str, str]:
    """A per-field quote overrides the common quote; missing values have no quote."""
    return {
        name: extraction.quotes.get(name, extraction.quote) if value is not None else ""
        for name, value in observation_values(extraction).items()
    }


def _records(materials: SimpleTaskInput) -> dict[str, RecordSpec]:
    return {
        name: RecordSpec(description=description, fields={
            field: FieldSpec(type="string", description=_DESCRIPTIONS[field], required=field == "value")
            for field in OBSERVATION_FIELDS
        })
        for name, description in materials.field_list.items()
    }


def _validate(model, value):
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        error = exc.errors(include_url=False, include_input=False)[0]
        raise TaskValidationError(f"Invalid task input at {error_path(error['loc'])}: {error['msg']}") from None


def _check_materials(materials: SimpleTaskInput, *, emit_warnings: bool) -> None:
    from .evidence import ground_quote

    if not materials.prompt_description.strip():
        raise ValueError("prompt_description must not be blank")
    for name, description in materials.field_list.items():
        if not name.strip():
            raise ValueError("field_list contains a blank field name")
        if not description.strip():
            raise ValueError(f"field_list.{name} must have a nonblank definition")
    notices = []
    seen_examples = set()
    for example_index, example in enumerate(materials.examples):
        base = f"examples[{example_index}]"
        if not example.text.strip():
            raise ValueError(f"{base}.text must not be blank")
        identity = example.model_dump_json()
        if identity in seen_examples:
            notices.append(f"{base}: duplicate example retained; it is not additional evidence")
        seen_examples.add(identity)
        chunk = DocumentChunk(chunk_id="example", text=example.text,
                              segments=[SourceSpan(start=0, end=len(example.text))])
        for extraction_index, extraction in enumerate(example.extractions):
            prefix = f"{base}.extractions[{extraction_index}]"
            if extraction.field not in materials.field_list:
                raise ValueError(f"{prefix}.field references an undeclared field: {extraction.field}")
            if not extraction.value.strip():
                raise ValueError(f"{prefix}.value must not be blank")
            if set(extraction.quotes) - set(OBSERVATION_FIELDS):
                raise ValueError(f"{prefix}.quotes contains an unknown observation field")
            values = observation_values(extraction)
            for name, quote in extraction.quotes.items():
                if values[name] is None and quote.strip():
                    raise ValueError(f"{prefix}.quotes.{name} supplies evidence for an unreported field")
            # Validate each distinct used quotation once, retaining all paths in
            # warnings when one common excerpt supports several labelled fields.
            references: dict[str, list[str]] = {}
            for name, quote in observation_quotes(extraction).items():
                if values[name] is None:
                    continue
                path = f"{prefix}.quotes.{name}" if name in extraction.quotes else f"{prefix}.quote"
                if not quote.strip():
                    raise ValueError(f"{path}: nonempty {name} needs a source quotation")
                references.setdefault(quote, []).append(path)
            if extraction.quote.strip() and extraction.quote not in references:
                references[extraction.quote] = [f"{prefix}.quote"]
            for quote, paths in references.items():
                evidence = ground_quote(example.text, chunk, quote, normalize=True)
                path = ", ".join(dict.fromkeys(paths))
                if evidence.status in {"missing", "unmatched"}:
                    raise ValueError(f"{path}: quotation cannot be located in {base}.text")
                if evidence.status == "normalized":
                    notices.append(f"{path}: quotation matches only after conservative whitespace/punctuation normalization; original input retained")
                elif evidence.status == "ambiguous":
                    notices.append(f"{path}: quotation has multiple source locations; no location was selected")
    if emit_warnings:
        for notice in notices:
            warnings.warn(notice, TaskInputWarning, stacklevel=3)


def validate_compiled_task(task: TaskSpec) -> None:
    """Reject deserialized snapshots whose compiled contract was altered."""
    materials = task.input_materials
    if materials is None:
        return
    _check_materials(materials, emit_warnings=False)
    if (task.name != _COMPILED_NAME or task.description != materials.prompt_description
            or task.record_types != _records(materials)):
        raise ValueError("input_materials do not match the compiled task; update the simple task inputs and compile again")


def compile_task(*, prompt_description: str, field_list: dict[str, str], examples=None) -> TaskSpec:
    """Compile the declared scope without semantic inference or model calls."""
    materials = _validate(TaskInputMaterials, {
        "prompt_description": prompt_description, "field_list": field_list,
        "examples": [] if examples is None else examples,
    })
    try:
        _check_materials(materials, emit_warnings=True)
    except ValueError as exc:
        raise TaskValidationError(f"Invalid task input: {exc}") from None
    return TaskSpec(name=_COMPILED_NAME, description=materials.prompt_description,
                    record_types=_records(materials), input_materials=materials)


def resolve_task(task: TaskSpec | dict[str, Any] | None = None, *,
                 prompt_description=None, field_list=None, examples=None) -> TaskSpec:
    """Accept exactly one legacy/compiled task or one set of simple inputs."""
    if task is not None:
        if any(value is not None for value in (prompt_description, field_list, examples)):
            raise TaskValidationError("Pass task or prompt_description/field_list/examples, not both")
        if isinstance(task, TaskSpec):
            return _validate(TaskSpec, task.model_dump(mode="python"))
        if not isinstance(task, dict):
            raise TaskValidationError("task must be a TaskSpec or a task dictionary")
        if "record_types" in task:
            if any(name in task for name in ("prompt_description", "field_list", "examples")):
                raise TaskValidationError("Do not mix record_types with simple task inputs")
            return _validate(TaskSpec, task)
        if "field_list" in task or "prompt_description" in task or "examples" in task:
            inputs = _validate(SimpleTaskInput, task)
            return compile_task(**inputs.model_dump(mode="python"))
        return _validate(TaskSpec, task)
    if prompt_description is None or field_list is None:
        raise TaskValidationError("Provide task, or both prompt_description and field_list; examples are optional")
    return compile_task(prompt_description=prompt_description, field_list=field_list, examples=examples)


def task_from_json(text: str) -> TaskSpec:
    """Load legacy or simple task JSON, rejecting duplicate keys/nonfinite data."""
    if not isinstance(text, str):
        raise TaskValidationError("Task JSON must be a string")
    try:
        data = load_json(text, allowed_keys={
            "name", "description", "record_types", "type", "required", "required_when",
            "not_applicable_when", "identity_fields", "condition_fields", "comparison_fields",
            "measurement_groups", "value_field", "unit_field", "input_materials",
            "input_version", "compiler_revision", "prompt_description", "field_list",
            "examples", "text", "extractions", "field", "entity", "unit", "conditions", "quotes",
        })
    except JSONValidationError as exc:
        error = exc.diagnostic["errors"][0]
        raise TaskValidationError(f"Invalid task JSON at {error['path']}: {error['code']}; {error['message']}") from None
    except json.JSONDecodeError as exc:
        raise TaskValidationError(f"Invalid task JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from None
    return resolve_task(data)


def task_summary(task: TaskSpec) -> dict[str, Any]:
    """Describe declared scope without including source or example content."""
    materials = task.input_materials
    examples = materials.examples if materials is not None else []
    return {
        "input_mode": "simple" if materials is not None else "task_spec",
        "record_types": list(task.record_types),
        "record_type_count": len(task.record_types),
        "example_count": len(examples),
        "example_extraction_count": sum(len(example.extractions) for example in examples),
    }
