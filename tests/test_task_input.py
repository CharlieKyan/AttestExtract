"""User-facing task scope, evidence checks and saved-task compatibility."""

from __future__ import annotations

from copy import deepcopy
import json
import warnings

import pytest
from pydantic import ValidationError

from attestextract.errors import TaskValidationError
from attestextract.models import FieldSpec, RecordSpec, TaskSpec
from attestextract.task_input import (
    TaskInputWarning, observation_quotes, observation_values, resolve_task,
    task_from_json, task_summary,
)


@pytest.fixture
def simple_task():
    return {
        "prompt_description": "Extract reported properties, keeping objects and conditions separate.",
        "field_list": {"porosity": "Pore fraction, including reported ranges and means.",
                       "permeability": "Permeability, including direction and test conditions."},
        "examples": [{
            "text": "甲层孔隙度为12%～18%，平均15%。",
            "extractions": [
                {"field": "porosity", "entity": "甲层", "value": "12%～18%", "unit": "%",
                 "conditions": "范围", "quote": "甲层孔隙度为12%～18%，平均15%。"},
                {"field": "porosity", "entity": "甲层", "value": "15", "unit": "%",
                 "conditions": "平均", "quote": "甲层孔隙度为12%～18%，平均15%。"},
            ],
        }],
    }


def test_complete_field_list_remains_authoritative_when_examples_cover_one_target(simple_task):
    task = resolve_task(simple_task)
    assert list(task.record_types) == ["porosity", "permeability"]
    assert task.record_types["porosity"].description == simple_task["field_list"]["porosity"]
    for record in task.record_types.values():
        assert set(record.fields) == {"entity", "value", "unit", "conditions"}
        assert {key for key, field in record.fields.items() if field.required} == {"value"}
        assert {field.type for field in record.fields.values()} == {"string"}
        assert record.identity_fields == record.condition_fields == record.comparison_fields == []
        assert record.measurement_groups == {}
    assert task.input_materials.examples[0].extractions[0].value == "12%～18%"
    assert task.input_materials.examples[0].extractions[1].value == "15"


def test_keyword_and_single_file_inputs_compile_to_the_same_task(simple_task):
    assert resolve_task(**simple_task) == task_from_json(json.dumps(simple_task, ensure_ascii=False))


def test_examples_optional_and_negative_example_preserved():
    task = resolve_task(prompt_description="Extract reported items", field_list={"item": "Reported property"})
    assert task.input_materials.examples == []
    empty = resolve_task(prompt_description="Extract reported items", field_list={"item": "Reported property"},
                         examples=[{"text": "No measurements were reported.", "extractions": []}])
    assert empty.input_materials.examples[0].extractions == []


def test_summary_reports_scope_without_private_example_contents(simple_task):
    task = resolve_task(simple_task)
    assert task_summary(task) == {
        "input_mode": "simple", "record_types": ["porosity", "permeability"],
        "record_type_count": 2, "example_count": 1, "example_extraction_count": 2,
    }
    assert "甲层" not in json.dumps(task_summary(task), ensure_ascii=False)


def test_old_tasks_do_not_acquire_new_serialized_keys():
    original = TaskSpec(name="existing", record_types={
        "item": RecordSpec(fields={"value": FieldSpec(type="number", required=True)})})
    snapshot = original.model_dump(mode="json")
    assert set(snapshot) == {"name", "description", "record_types"}
    assert "input_materials" not in original.model_dump_json()
    assert resolve_task(original) == task_from_json(json.dumps(snapshot)) == original
    assert task_summary(original)["input_mode"] == "task_spec"


def test_compiled_snapshot_roundtrips_and_preserves_original_inputs(simple_task):
    task = resolve_task(simple_task)
    restored = task_from_json(task.model_dump_json())
    assert restored == task
    assert restored.input_materials.prompt_description == simple_task["prompt_description"]
    assert restored.input_materials.field_list == simple_task["field_list"]
    assert restored.input_materials.examples[0].text == simple_task["examples"][0]["text"]
    assert restored.input_materials.compiler_revision == "simple-observation-v1"


@pytest.mark.parametrize("mutation", ["description", "type", "required", "unknown_target", "snapshot", "revision"])
def test_altered_compiled_snapshot_cannot_be_loaded(simple_task, mutation):
    data = resolve_task(simple_task).model_dump(mode="json")
    if mutation == "description":
        data["description"] = "Another task"
    elif mutation == "type":
        data["record_types"]["porosity"]["fields"]["value"]["type"] = "number"
    elif mutation == "required":
        data["record_types"]["porosity"]["fields"]["value"]["required"] = False
    elif mutation == "unknown_target":
        data["record_types"]["other"] = deepcopy(data["record_types"]["porosity"])
    elif mutation == "snapshot":
        data["input_materials"]["field_list"]["porosity"] = "Changed meaning"
    else:
        data["input_materials"]["compiler_revision"] = "unsupported-version"
    with pytest.raises(TaskValidationError, match="input_materials"):
        task_from_json(json.dumps(data))
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(data)


def test_direct_instance_mutation_cannot_bypass_revalidation(simple_task):
    task = resolve_task(simple_task)
    task.record_types["porosity"].fields["value"].required = False
    with pytest.raises(TaskValidationError, match="input_materials"):
        resolve_task(task)


def test_per_field_quotes_override_whole_quote_without_fabrication():
    task = resolve_task(prompt_description="Extract porosity", field_list={"porosity": "Pore fraction"}, examples=[{
        "text": "甲层孔隙度为15。单位为%。",
        "extractions": [{"field": "porosity", "entity": "甲层", "value": "15", "unit": "%",
                         "quote": "甲层孔隙度为15。", "quotes": {"unit": "单位为%。"}}],
    }])
    extraction = task.input_materials.examples[0].extractions[0]
    assert observation_values(extraction) == {"entity": "甲层", "value": "15", "unit": "%", "conditions": None}
    assert observation_quotes(extraction) == {
        "entity": "甲层孔隙度为15。", "value": "甲层孔隙度为15。", "unit": "单位为%。", "conditions": "",
    }


def test_per_field_only_quotes_can_supply_all_reported_evidence():
    task = resolve_task(prompt_description="Extract porosity", field_list={"porosity": "Pore fraction"}, examples=[{
        "text": "孔隙度15%。", "extractions": [{"field": "porosity", "value": "15%",
                                             "quotes": {"value": "孔隙度15%。"}}],
    }])
    assert observation_quotes(task.input_materials.examples[0].extractions[0])["value"] == "孔隙度15%。"


def test_normalized_example_warns_once_and_retains_original_quote():
    original = {"prompt_description": "Extract values", "field_list": {"porosity": "Pore fraction"}, "examples": [{
        "text": "甲层，孔隙度为  15%。", "extractions": [{"field": "porosity", "value": "15%",
                                                  "quote": "甲层,孔隙度为 15%。"}],
    }]}
    with pytest.warns(TaskInputWarning, match="normalization") as notices:
        task = resolve_task(original)
    assert len(notices) == 1
    assert task.input_materials.examples[0].extractions[0].quote == "甲层,孔隙度为 15%。"
    assert task.input_materials.examples[0].text == "甲层，孔隙度为  15%。"
    with warnings.catch_warnings(record=True) as later:
        warnings.simplefilter("always")
        resolve_task(task)
        task_from_json(task.model_dump_json())
    assert later == []


def test_duplicate_examples_and_ambiguous_quotes_are_warned_not_removed():
    example = {"text": "孔隙度15%。孔隙度15%。",
               "extractions": [{"field": "porosity", "value": "15%", "quote": "孔隙度15%。"}]}
    with pytest.warns(TaskInputWarning) as notices:
        task = resolve_task(prompt_description="Extract values", field_list={"porosity": "Pore fraction"},
                            examples=[example, deepcopy(example)])
    assert len(task.input_materials.examples) == 2
    assert any("multiple source locations" in str(notice.message) for notice in notices)
    assert any("duplicate example retained" in str(notice.message) for notice in notices)


@pytest.mark.parametrize(("key", "bad_value", "error_path"), [
    ("value", "", "value"), ("value", "   ", "value"), ("value", 15, "value"),
    ("value", {"min": 12, "max": 18}, "value"), ("field", "not_declared", "field"),
    ("quote", "", "quote"), ("quote", "Other source", "quote"),
    ("entity", 12, "entity"), ("unexpected", "x", "unexpected"),
    ("quotes", {"unknown": "甲层"}, "quotes"),
])
def test_bad_examples_have_actionable_paths(simple_task, key, bad_value, error_path):
    simple_task["examples"][0]["extractions"][0][key] = bad_value
    with pytest.raises(TaskValidationError, match=rf"examples\[0\]\.(?:extractions\[0\]\.)?{error_path}"):
        resolve_task(simple_task)


@pytest.mark.parametrize("quote", ["孔隙度17.9%", "孔隙度1 7.9%", "孔隙度17.9％"])
def test_ocr_numeric_and_unit_changes_are_not_example_alignment_repairs(quote):
    with pytest.raises(TaskValidationError, match="cannot be located"):
        resolve_task(prompt_description="Extract values", field_list={"porosity": "Pore fraction"}, examples=[{
            "text": "孔隙度l7.9%", "extractions": [{"field": "porosity", "value": "17.9%", "quote": quote}],
        }])


@pytest.mark.parametrize("description,fields", [(" ", {"item": "value"}), ("Items", {}),
    ("Items", {" ": "value"}), ("Items", {"item": " "}), ("Items", {"item": 3})])
def test_empty_or_untyped_scope_rejected(description, fields):
    with pytest.raises(TaskValidationError):
        resolve_task(prompt_description=description, field_list=fields)


def test_mixed_legacy_and_simple_inputs_rejected(simple_task):
    with pytest.raises(TaskValidationError, match="not both"):
        resolve_task(simple_task, prompt_description="Override")
    with pytest.raises(TaskValidationError, match="Do not mix"):
        resolve_task({**simple_task, "record_types": {"other": {"fields": {"value": {"type": "number"}}}}})


@pytest.mark.parametrize("text", [
    '{"prompt_description":"x","field_list":{"item":"first","item":"second"}}',
    '{"prompt_description":"x","prompt_description":"y","field_list":{"item":"value"}}',
    '{"prompt_description":"x","field_list":{"item":NaN}}',
    '[]', 'null', '{bad}',
])
def test_invalid_json_and_duplicate_keys_rejected(text):
    with pytest.raises(TaskValidationError):
        task_from_json(text)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_strict_json_rejects_nonfinite_numbers_before_task_validation(number):
    text = '{"private-value":' + number + '}'
    with pytest.raises(TaskValidationError, match="non_finite_number") as caught:
        task_from_json(text)
    assert "private-value" not in str(caught.value)
    assert number not in str(caught.value)


def test_excessive_json_nesting_is_a_task_error_not_a_recursion_error():
    with pytest.raises(TaskValidationError, match="excessive_nesting"):
        task_from_json("[" * 2000 + "0" + "]" * 2000)


def test_duplicate_unknown_key_is_masked_but_error_kind_is_visible():
    with pytest.raises(TaskValidationError, match="duplicate_key") as caught:
        task_from_json('{"private-value":"first","private-value":"second"}')
    assert "private-value" not in str(caught.value)
    assert "first" not in str(caught.value)
    assert "second" not in str(caught.value)


def test_json_grammar_error_keeps_line_and_column():
    with pytest.raises(TaskValidationError, match="line 2, column 1"):
        task_from_json('{\n?}')
