"""Strict decoding preserves JSON distinctions and produces safe error paths."""

import json

import pytest

from attestextract.json_validation import JSONValidationError, load_json


def diagnostic(raw, **kwargs):
    with pytest.raises(JSONValidationError) as caught:
        load_json(raw, **kwargs)
    return caught.value.diagnostic


@pytest.mark.parametrize("second", ['"A"', '"B"', 'true', '1', 'null'])
def test_equal_and_conflicting_repeated_keys_are_rejected(second):
    raw = '{"records":[{"fields":{"subject":{"value":"A","quote":"A","value":' + second + '}}}]}'
    result = diagnostic(raw, allowed_keys={"subject"})
    assert result["phase"] == "json_syntax"
    assert result["error_count"] == 1
    error = result["errors"][0]
    assert error["path"] == "records[0].fields.subject.value"
    assert error["code"] == "duplicate_key"
    assert error["member_index"] == 2


def test_keys_reused_in_nested_objects_and_different_records_are_valid():
    raw = '{"records":[{"fields":{"value":{"value":1,"quote":"one"}}},{"fields":{"value":{"value":1,"quote":"one"}}}]}'
    assert load_json(raw) == {"records": [
        {"fields": {"value": {"value": 1, "quote": "one"}}},
        {"fields": {"value": {"value": 1, "quote": "one"}}},
    ]}


def test_decoded_escaped_key_is_the_same_key():
    result = diagnostic(r'{"value":1,"\u0076alue":2}')
    assert result["errors"][0]["path"] == "value"
    assert result["errors"][0]["code"] == "duplicate_key"


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e9999", "-1e9999"])
def test_nonfinite_literals_and_float_overflow_report_the_number_path(number):
    result = diagnostic('{"records":[{"fields":{"value":{"value":' + number + ',"quote":"private-text"}}}]}')
    assert result["error_count"] == 1
    error = result["errors"][0]
    assert error["path"] == "records[0].fields.value.value"
    assert error["code"] == "non_finite_number"
    assert error["actual_type"] == "number"
    assert "private-text" not in json.dumps(result)


def test_valid_scalar_types_and_strings_are_preserved():
    raw = '{"value":[1,1.0,1e308,true,null,"NaN","Infinity","a🙂\\n b"]}'
    value = load_json(raw)["value"]
    assert value == [1, 1.0, 1e308, True, None, "NaN", "Infinity", "a🙂\n b"]
    assert [type(item) for item in value[:4]] == [int, float, float, bool]


def test_unknown_keys_and_input_values_do_not_leak_in_diagnostics():
    raw = '{"private-parent":{"private-key":"secret-one","private-key":"secret-two","value":NaN}}'
    result = diagnostic(raw)
    encoded = json.dumps(result)
    assert result["error_count"] == 2
    for private in ("private-parent", "private-key", "secret-one", "secret-two"):
        assert private not in encoded
    assert result["errors"][0]["path"] == '["<undeclared>"]["<undeclared>"]'
    assert result["errors"][1]["path"] == '["<undeclared>"].value'


def test_declared_unicode_task_field_can_be_identified():
    result = diagnostic('{"fields":{"测量":{"value":1,"value":2}}}', allowed_keys={"测量"})
    assert result["errors"][0]["path"] == 'fields["测量"].value'


def test_all_repeated_occurrences_are_counted_but_details_are_bounded():
    raw = "{" + ",".join('"value":1' for _ in range(55)) + "}"
    result = diagnostic(raw)
    assert result["error_count"] == 54
    assert len(result["errors"]) == 50
    assert result["truncated"] is True


def test_duplicate_diagnostics_include_total_occurrences_in_that_object():
    result = diagnostic('{"value":1,"value":2,"value":3}')
    assert result["error_count"] == 2
    assert [error["occurrences"] for error in result["errors"]] == [3, 3]
    assert [error["member_index"] for error in result["errors"]] == [1, 2]


def test_invalid_nested_duplicate_values_are_checked_too():
    result = diagnostic('{"value":1,"value":{"value":NaN,"value":2}}')
    assert result["error_count"] == 3
    assert [error["code"] for error in result["errors"]] == [
        "non_finite_number", "duplicate_key", "duplicate_key",
    ]
    assert [error["path"] for error in result["errors"]] == ["value.value", "value.value", "value"]


@pytest.mark.parametrize("raw", ['{"records":[}', '{"records":[],}', '{"value":1 // comment\n}'])
def test_syntax_errors_keep_standard_decoder_positions(raw):
    with pytest.raises(json.JSONDecodeError) as expected:
        json.loads(raw)
    with pytest.raises(json.JSONDecodeError) as actual:
        load_json(raw)
    assert (actual.value.lineno, actual.value.colno) == (expected.value.lineno, expected.value.colno)


def test_excessive_nesting_is_reported_without_raw_input():
    result = diagnostic("[" * 2000 + '"private-text"' + "]" * 2000)
    assert result["errors"][0]["code"] == "excessive_nesting"
    assert result["errors"][0]["path"] == "$"
    assert "private-text" not in json.dumps(result)
