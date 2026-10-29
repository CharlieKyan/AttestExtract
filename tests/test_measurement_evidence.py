"""Synthetic regressions for measurement binding, missingness and source links.

The expected spans are computed from independently written source expressions;
none of these fixtures is a paper excerpt or a saved model completion.
"""

from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from attestextract import TaskSpec, extract
from attestextract.backends import ReplayBackend
from attestextract.chunking import chunk_document
from attestextract.errors import InvalidResponseError
from attestextract.prompting import build_example, build_output_schema
from attestextract.schema_diagnostics import assess_schema_compliance


@pytest.fixture
def task():
    return {
        "name": "Synthetic measurement review",
        "record_types": {
            "observation": {
                "fields": {
                    "subject": {"type": "string", "required": True},
                    "parameter": {"type": "string", "required": True},
                    "value": {"type": "string", "required": True},
                    "unit": {"type": "string"},
                    "conditions": {"type": "string"},
                },
                "identity_fields": ["subject", "parameter"],
                "measurement_groups": {
                    "reading": {
                        "value_field": "value",
                        "unit_field": "unit",
                        "condition_fields": ["conditions"],
                    }
                },
                "comparison_fields": ["parameter", "value", "unit"],
            }
        },
    }


def record(context="厚度17.5 m", value="17.5", unit="m", *,
           subject="样品甲", parameter="厚度", condition=None,
           condition_quote="", unit_quote=""):
    return {
        "record_type": "observation",
        "fields": {
            "subject": {"value": subject, "quote": subject},
            "parameter": {"value": parameter, "quote": context},
            "value": {"value": value, "quote": context},
            "unit": {"value": unit, "quote": (unit_quote or context) if unit is not None else ""},
            "conditions": {"value": condition, "quote": condition_quote},
        },
        "measurements": {
            "reading": {
                "context_quote": context,
                "value_text": value,
                "unit_text": unit or "",
                "unit_quote": unit_quote,
            }
        },
    }


def run(task, source, *records):
    return extract(source, task, backend=ReplayBackend([{"records": list(records)}]))


def assert_exact_span(evidence, source, token, start):
    assert evidence.status == "matched"
    assert (evidence.start, evidence.end) == (start, start + len(token))
    assert source[evidence.start:evidence.end] == token == evidence.quote


def test_measurement_locations_are_scoped_and_condition_unit_is_separate(task):
    source = "前置🙂 样品甲位于井段3100–3117.5 m，厚度17.5 m；样品乙厚度9 m。"
    result = run(task, source, record(condition="井段3100–3117.5 m", condition_quote="井段3100–3117.5 m"))
    candidate = result.candidates[0]
    measurement = candidate.measurements["reading"]
    start = source.index("厚度17.5 m")
    assert_exact_span(measurement.context, source, "厚度17.5 m", start)
    assert_exact_span(measurement.value, source, "17.5", start + len("厚度"))
    assert_exact_span(measurement.unit, source, "m", start + len("厚度17.5 "))
    assert measurement.condition_fields == ["conditions"]
    assert measurement.semantic_status == "not_verified"
    assert not measurement.issues
    assert not candidate.needs_review
    assert candidate.fields["conditions"].evidence.start != measurement.unit.start


def test_missing_measurement_metadata_is_reviewed_without_inventing_tokens(task):
    response = record()
    del response["measurements"]
    result = run(task, "样品甲厚度17.5 m。", response)
    candidate = result.candidates[0]
    assert candidate.needs_review
    assert any(item.severity == "review" for item in result.review)
    assert not candidate.measurements or candidate.measurements["reading"].value.start is None
    assert candidate.fields["value"].value == "17.5"


@pytest.mark.parametrize("context,value,unit", [
    ("厚度280 m", "28", "m"),
    (r"容量7\times10^3", r"7\times10^3", "t"),
])
def test_token_substrings_are_not_measurement_evidence(task, context, value, unit):
    result = run(task, "样品甲" + context + "。", record(context, value, unit))
    measurement = result.candidates[0].measurements["reading"]
    rejected = measurement.value if value == "28" else measurement.unit
    assert rejected.status != "matched"
    assert rejected.start is None and rejected.end is None
    assert measurement.issues
    assert result.candidates[0].needs_review


@pytest.mark.parametrize("sign", ["-", "−"])
def test_positive_value_cannot_bind_to_negative_source_value(task, sign):
    context = f"位移{sign}17.5 m"
    result = run(task, "样品甲" + context + "。", record(context, parameter="位移"))
    measurement = result.candidates[0].measurements["reading"]
    assert measurement.value.status != "matched"
    assert measurement.value.start is None and measurement.value.end is None
    assert result.candidates[0].needs_review


@pytest.mark.parametrize("source_unit,reported_unit", [
    ("m²", "m"), ("m/s", "m"), ("°C", "C"), ("µm", "m"), ("m⁻²", "m"),
])
def test_partial_unit_cannot_bind_inside_a_different_unit(task, source_unit, reported_unit):
    context = f"读数17.5 {source_unit}"
    result = run(task, "样品甲" + context + "。", record(context, unit=reported_unit, parameter="读数"))
    measurement = result.candidates[0].measurements["reading"]
    assert measurement.unit.status != "matched"
    assert measurement.unit.start is None and measurement.unit.end is None
    assert result.candidates[0].needs_review


def test_unit_adjacent_to_chinese_prose_is_still_located(task):
    source = "样品甲厚度17.5 m之间。"
    result = run(task, source, record("厚度17.5 m之间"))
    measurement = result.candidates[0].measurements["reading"]
    assert_exact_span(measurement.unit, source, "m", source.index("m"))
    assert not measurement.issues
    assert not result.candidates[0].needs_review


def test_measurement_group_cannot_switch_to_another_parameter_with_same_value(task):
    source = "样品甲长度17.5 m，深度17.5 m。"
    response = record("长度17.5 m", parameter="长度")
    response["measurements"]["reading"]["context_quote"] = "深度17.5 m"
    result = run(task, source, response)
    candidate = result.candidates[0]
    assert candidate.fields["value"].evidence.quote == "长度17.5 m"
    assert candidate.measurements["reading"].context.quote == "深度17.5 m"
    assert "measurement_field_context_mismatch" in candidate.measurements["reading"].issues
    assert candidate.needs_review


def test_condition_unit_cannot_supply_a_missing_measurement_unit(task):
    source = "样品甲位于井段3100–3117.5 m，校正读数17.5。"
    response = record("校正读数17.5", parameter="校正读数", condition="井段3100–3117.5 m", condition_quote="井段3100–3117.5 m")
    result = run(task, source, response)
    measurement = result.candidates[0].measurements["reading"]
    assert measurement.value.status == "matched"
    assert measurement.unit.status != "matched"
    assert measurement.unit.start is None
    assert result.candidates[0].needs_review


def test_two_measurements_in_one_context_require_review_without_nearest_choice(task):
    source = "样品甲厚度17.5 m，深度3200 m。"
    result = run(task, source, record("厚度17.5 m，深度3200 m"))
    measurement = result.candidates[0].measurements["reading"]
    assert "measurement_multiple_values" in measurement.issues
    assert measurement.unit.status != "matched"
    assert measurement.unit.start is None
    assert result.candidates[0].needs_review


def test_exponent_in_explicit_unit_is_not_another_observation(task):
    source = "样品甲密度2.4 g/cm^3。"
    result = run(task, source, record("密度2.4 g/cm^3", "2.4", "g/cm^3", parameter="密度"))
    measurement = result.candidates[0].measurements["reading"]
    assert_exact_span(measurement.unit, source, "g/cm^3", source.index("g/cm^3"))
    assert "measurement_multiple_values" not in measurement.issues
    assert not result.candidates[0].needs_review


def test_normalized_scalar_does_not_silently_equal_raw_measurement_text(task):
    source = "样品甲厚度1 7 . 5 m。"
    response = record("厚度1 7 . 5 m")
    response["measurements"]["reading"]["value_text"] = "1 7 . 5"
    result = run(task, source, response)
    measurement = result.candidates[0].measurements["reading"]
    assert measurement.issues
    assert result.candidates[0].needs_review
    assert result.candidates[0].fields["value"].value == "17.5"
    assert source == "样品甲厚度1 7 . 5 m。"


def test_valid_measurement_does_not_silently_repair_invalid_field_quote(task):
    source = "样品甲厚度17.5 m。"
    response = record()
    response["fields"]["value"]["quote"] = "thickness 17.5 m"
    result = run(task, source, response)
    candidate = result.candidates[0]
    assert candidate.measurements["reading"].value.status == "matched"
    assert candidate.fields["value"].evidence.quote == "thickness 17.5 m"
    assert candidate.fields["value"].evidence.status == "unmatched"
    assert candidate.needs_review


def test_repeated_measurement_context_has_no_guessed_global_position(task):
    source = "样品甲厚度17.5 m。样品乙厚度17.5 m。"
    result = run(task, source, record())
    measurement = result.candidates[0].measurements["reading"]
    assert measurement.context.status == "ambiguous"
    assert measurement.value.start is None and measurement.unit.start is None
    assert result.candidates[0].needs_review


def test_table_header_unit_maps_to_original_header_in_later_chunk(task):
    header = "| 样品 | 厚度 (m) | 温度 (℃) |"
    row = "| 样品乙 | 17.5 | 20 |"
    source = header + "\n| --- | --- | --- |\n| 样品甲 | 9 | 25 |\n" + row + "\n"
    response = record(row, subject="样品乙", unit_quote=header)
    chunks = chunk_document(source, table_rows_per_chunk=1)
    responses = [{"records": [response]} if row in chunk.text else {"records": []} for chunk in chunks]
    result = extract(source, task, backend=ReplayBackend(responses), table_rows_per_chunk=1)
    measurement = result.candidates[0].measurements["reading"]
    assert measurement.unit_source == "table_header"
    assert_exact_span(measurement.value, source, "17.5", source.index("17.5"))
    assert_exact_span(measurement.unit, source, "m", source.index("m"))
    assert measurement.context.quote == row
    assert not measurement.issues


def test_table_header_unit_from_another_column_is_not_bound(task):
    header = "| 样品 | 厚度 (m) | 压力 (bar) |"
    row = "| 样品甲 | 17.5 | 20 |"
    source = header + "\n| --- | --- | --- |\n" + row + "\n"
    response = record(row, unit="bar", unit_quote=header)
    result = run(task, source, response)
    measurement = result.candidates[0].measurements["reading"]
    assert measurement.issues
    assert measurement.unit.status != "matched"
    assert measurement.unit.start is None
    assert result.candidates[0].needs_review


def test_unit_from_another_table_is_not_bound(task):
    source = "| 样品 | 厚度 |\n| --- | --- |\n| 样品甲 | 17.5 |\n\n| 其他量 (m) |\n| --- |\n| 20 |\n"
    response = record("| 样品甲 | 17.5 |", unit_quote="| 其他量 (m) |")
    result = run(task, source, response)
    measurement = result.candidates[0].measurements["reading"]
    assert measurement.unit.status != "matched"
    assert measurement.unit.start is None
    assert measurement.issues


@pytest.mark.parametrize("omit", [False, True])
def test_optional_unreported_field_is_one_information_item(task, omit):
    response = record()
    if omit:
        del response["fields"]["conditions"]
    result = run(task, "样品甲厚度17.5 m。", response)
    candidate = result.candidates[0]
    assert candidate.field_status["conditions"] == "not_reported"
    items = [item for item in result.review if item.field == "conditions"]
    assert len(items) == 1
    assert items[0].severity == "info"
    assert items[0].code == "missing_value"
    assert not candidate.needs_review


def test_task_declared_dimensionless_unit_is_not_applicable(task):
    spec = task["record_types"]["observation"]
    spec["fields"]["unit"]["not_applicable_when"] = {"parameter": ["校正系数"]}
    result = run(task, "样品甲校正系数0.8。", record("校正系数0.8", "0.8", None, parameter="校正系数"))
    candidate = result.candidates[0]
    assert candidate.field_status["unit"] == "not_applicable"
    assert not [item for item in result.review if item.field == "unit" and item.severity == "review"]
    assert not candidate.needs_review


def test_reported_percent_unit_is_preserved_despite_missing_unit_applicability_rule(task):
    task["record_types"]["observation"]["fields"]["unit"]["not_applicable_when"] = {"parameter": ["校正系数"]}
    source = "样品甲校正系数80%。"
    result = run(task, source, record("校正系数80%", "80", "%", parameter="校正系数"))
    candidate = result.candidates[0]
    assert candidate.field_status["unit"] == "reported"
    assert candidate.fields["unit"].value == "%"
    assert_exact_span(candidate.measurements["reading"].unit, source, "%", source.index("%"))
    assert not any(item.code == "applicability_conflict" for item in result.review)
    assert not candidate.needs_review


def test_record_with_only_optional_null_fields_is_still_empty_and_reviewable(task):
    spec = task["record_types"]["observation"]
    spec.update(identity_fields=[], comparison_fields=[], measurement_groups={})
    for field in spec["fields"].values():
        field["required"] = False
    response = {
        "record_type": "observation",
        "fields": {name: {"value": None, "quote": ""} for name in spec["fields"]},
    }
    result = run(task, "本段仅介绍实验流程，没有给出观测。", response)
    assert result.candidates[0].needs_review
    severe_items = [item for item in result.review if item.severity == "review"]
    assert len(severe_items) == 1
    assert severe_items[0].code == "empty_record"


def test_null_unit_without_task_rule_is_not_assumed_dimensionless(task):
    result = run(task, "样品甲厚度17.5。", record("厚度17.5", unit=None))
    assert result.candidates[0].field_status["unit"] == "not_reported"


def test_critical_calculation_scenario_missing_has_one_review_item(task):
    spec = task["record_types"]["observation"]
    spec["fields"]["conditions"]["required_when"] = {"parameter": ["理论容量"]}
    result = run(task, "样品甲完全反应时理论容量60 t。", record("理论容量60 t", "60", "t", parameter="理论容量"))
    candidate = result.candidates[0]
    assert candidate.field_status["conditions"] == "required_missing"
    items = [item for item in result.review if item.field == "conditions"]
    assert len(items) == 1
    assert items[0].code == "missing_required_field"
    assert items[0].severity == "review"
    assert candidate.needs_review
    assert candidate.fields["value"].value == "60"


@pytest.mark.parametrize("blank", ["", " "])
def test_blank_critical_condition_is_missing_even_when_a_quote_exists(task, blank):
    spec = task["record_types"]["observation"]
    spec["fields"]["conditions"]["required_when"] = {"parameter": ["理论容量"]}
    response = record("理论容量60 t", "60", "t", parameter="理论容量", condition=blank, condition_quote="完全反应")
    result = run(task, "样品甲完全反应时理论容量60 t。", response)
    candidate = result.candidates[0]
    assert candidate.field_status["conditions"] == "required_missing"
    items = [item for item in result.review if item.field == "conditions" and item.severity == "review"]
    assert len(items) == 1
    assert items[0].code == "missing_required_field"
    assert candidate.needs_review


def test_required_when_requires_all_predicates(task):
    spec = task["record_types"]["observation"]
    spec["fields"]["conditions"]["required_when"] = {"parameter": ["理论容量"], "subject": ["样品乙"]}
    result = run(task, "样品甲理论容量60 t。", record("理论容量60 t", "60", "t", parameter="理论容量"))
    assert result.candidates[0].field_status["conditions"] == "not_reported"
    assert not any(item.code == "missing_required_field" and item.field == "conditions" for item in result.review)


def linked_records(first_condition="完全反应", second_condition="完全反应", *, second_subject="样品甲"):
    first_context = "摘要：样品甲在完全反应时容量60 t。"
    second_context = "结论：" + second_subject + "在" + (second_condition or "未知情景") + "时容量60 t。"
    source = first_context + "\n\n" + second_context
    first = record(first_context, "60", "t", parameter="容量", condition=first_condition, condition_quote=first_context if first_condition else "")
    second = record(second_context, "60", "t", subject=second_subject, parameter="容量", condition=second_condition, condition_quote=second_context if second_condition else "")
    first["fields"]["subject"]["quote"] = first_context
    second["fields"]["subject"]["quote"] = second_context
    return source, first, second


def test_repeated_observations_are_linked_but_not_deleted(task):
    source, first, second = linked_records()
    result = run(task, source, first, second)
    assert len(result.candidates) == 2
    assert len({candidate.candidate_id for candidate in result.candidates}) == 2
    links = [link for link in result.links if link.kind == "possible_repeat"]
    assert len(links) == 1
    assert set(links[0].candidate_ids) == {candidate.candidate_id for candidate in result.candidates}
    assert all(candidate.measurements["reading"].semantic_status == "not_verified" for candidate in result.candidates)


def test_same_value_in_different_known_scenarios_is_not_a_repeat_or_conflict(task):
    source, first, second = linked_records(second_condition="部分反应")
    result = run(task, source, first, second)
    assert len(result.candidates) == 2
    assert not result.links


def test_link_comparison_does_not_coerce_numeric_scalar_types(task):
    task["record_types"]["observation"]["fields"]["value"]["type"] = "number"
    source = "样品甲厚度1 m。样品甲厚度1.0 m。"
    first = record("厚度1 m", "1")
    second = record("厚度1.0 m", "1.0")
    first["fields"]["value"]["value"] = 1
    second["fields"]["value"]["value"] = 1.0
    result = run(task, source, first, second)
    assert len(result.candidates) == 2
    assert not result.links


def test_missing_scenario_prevents_same_fact_claim(task):
    source, first, second = linked_records(second_condition=None)
    result = run(task, source, first, second)
    assert len(result.candidates) == 2
    assert result.links
    assert {link.kind for link in result.links} == {"insufficient_context"}
    assert "conditions" in result.links[0].unresolved_fields


def test_same_measurement_with_different_source_identity_is_only_possible_conflict(task):
    source, first, second = linked_records(second_subject="样品乙")
    result = run(task, source, first, second)
    assert len(result.candidates) == 2
    assert {candidate.fields["subject"].value for candidate in result.candidates} == {"样品甲", "样品乙"}
    assert {link.kind for link in result.links} == {"possible_conflict"}
    assert "subject" in result.links[0].differing_fields


@pytest.mark.parametrize("mutation", [
    lambda spec: spec["fields"]["unit"].update(not_applicable_when={"unknown": ["ratio"]}),
    lambda spec: spec["fields"]["conditions"].update(required_when={"conditions": ["warm"]}),
    lambda spec: spec["fields"]["conditions"].update(required_when={"parameter": []}),
    lambda spec: spec["fields"]["conditions"].update(required_when={"parameter": [1]}),
    lambda spec: spec["fields"]["conditions"].update(required_when={"parameter": [None]}),
    lambda spec: spec["fields"]["conditions"].update(required_when={"parameter": [" "]}),
    lambda spec: spec.update(identity_fields=[]),
    lambda spec: spec["measurement_groups"]["reading"].update(unit_field="value"),
    lambda spec: spec["measurement_groups"]["reading"].update(condition_fields=["unknown"]),
])
def test_invalid_task_references_are_rejected(task, mutation):
    invalid = deepcopy(task)
    mutation(invalid["record_types"]["observation"])
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(invalid)


@pytest.mark.parametrize("malformed", [None, {}, {"context_quote": 17, "value_text": "17.5"}])
def test_malformed_unknown_measurement_names_are_absent_from_error_diagnostics(task, malformed):
    generated_name = "synthetic-private-generated-measurement-name"
    response = record()
    response["measurements"] = {generated_name: malformed}
    with pytest.raises(InvalidResponseError) as caught:
        run(task, "样品甲厚度17.5 m。", response)
    assert caught.value.validation_details["error_count"] >= 1
    safe_details = json.dumps(caught.value.validation_details, ensure_ascii=False) + str(caught.value)
    assert generated_name not in safe_details


def test_duplicate_measurement_token_has_precise_safe_path_and_retry_feedback(task):
    source = "样品甲厚度17.5 m。"
    corrected = {"records": [record()]}
    private_value = "synthetic-private-raw-value"
    malformed = json.dumps(corrected, ensure_ascii=False).replace(
        '"value_text": "17.5"',
        '"value_text": "17.5", "value_text": "' + private_value + '"',
    )
    path = "records[0].measurements.reading.value_text"
    with pytest.raises(InvalidResponseError) as caught:
        extract(source, task, backend=ReplayBackend([malformed]))
    assert caught.value.validation_details["errors"][0]["path"] == path
    assert private_value not in json.dumps(caught.value.validation_details) + str(caught.value)

    class CapturingReplay(ReplayBackend):
        def __init__(self):
            super().__init__([malformed, corrected])
            self.requests = []

        def generate(self, request):
            self.requests.append(request.model_copy(deep=True))
            return super().generate(request)

    backend = CapturingReplay()
    result = extract(source, task, backend=backend, format_retries=1)
    assert len(backend.requests) == 2
    first, second = backend.requests
    assert first.messages[1] == second.messages[1]
    assert json.loads(second.messages[1]["content"])["source"] == source
    assert first.output_schema == second.output_schema
    assert path in second.messages[0]["content"]
    assert private_value not in second.messages[0]["content"]
    assert not result.candidates[0].needs_review


def test_measurement_prompt_example_satisfies_strict_schema_and_local_checks(task):
    specification = TaskSpec.model_validate(task)
    schema = build_output_schema(specification)

    def check_strict_objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                check_strict_objects(value)
        elif isinstance(node, list):
            for child in node:
                check_strict_objects(child)

    check_strict_objects(schema)
    record_schema = schema["properties"]["records"]["items"]
    group_schema = record_schema["properties"]["measurements"]["properties"]["reading"]
    assert group_schema["properties"] == {
        name: {"type": "string"}
        for name in ("context_quote", "value_text", "unit_text", "unit_quote")
    }
    example = build_example(specification)
    compliance = assess_schema_compliance(json.dumps(example["response"]), schema, "json_schema")
    assert compliance["status"] == "passed"
    assert compliance["server_enforcement"] == "not_verified"
    result = extract(example["source"], specification, backend=ReplayBackend([example["response"]]))
    assert len(result.candidates) == 1
    assert not result.candidates[0].needs_review
    assert not result.candidates[0].measurements["reading"].issues
    assert not result.review
