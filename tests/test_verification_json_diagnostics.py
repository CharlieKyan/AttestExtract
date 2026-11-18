"""Verification JSON diagnostics identify protocol keys without exposing data."""

import json

import pytest

from attestextract.json_validation import JSONValidationError, load_json
from attestextract.verification_protocol import Step, VerificationValidationError, parse_step


def rejected(text):
    with pytest.raises(VerificationValidationError) as caught:
        parse_step(text)
    assert caught.value.code == "invalid_analysis_json"
    return caught.value.details["diagnostic"]["errors"]


def test_conflicting_object_lists_are_rejected_with_observed_shapes():
    raw = ('{"action":"decide","question":"Check specimen identity",'
           '"objects":[],"bindings":[],"objects":[{"object_id":"SYNTHETIC-PRIVATE-ID"}]}')
    error, = rejected(raw)
    assert error["path"] == "objects" and error["code"] == "duplicate_key"
    assert error["first_member_index"] == 2 and error["member_index"] == 4
    assert error["occurrences"] == 2
    assert error["first_actual_type"] == error["actual_type"] == "array"
    assert error["first_array_length"] == 0 and error["array_length"] == 1
    assert "SYNTHETIC-PRIVATE-ID" not in json.dumps(error)


@pytest.mark.parametrize("value", ["[]", '[{"object_id":"same"}]'])
def test_equal_duplicate_keys_are_not_silently_accepted(value):
    errors = rejected('{"action":"decide","question":"Identity","objects":' + value + ',"objects":' + value + '}')
    assert len(errors) == 1 and errors[0]["path"] == "objects"


def test_nested_protocol_properties_remain_visible_but_values_do_not():
    raw = ('{"action":"decide","question":"Identity","bindings":['
           '{"subject_id":"PRIVATE-FIRST","subject_id":"PRIVATE-SECOND",'
           '"evidence":[{"passage_id":"p1","passage_id":"p2","quote":"PRIVATE-QUOTE"}]}]}')
    errors = rejected(raw)
    assert [error["path"] for error in errors] == [
        "bindings[0].subject_id", "bindings[0].evidence[0].passage_id"]
    assert "PRIVATE" not in json.dumps(errors)


def test_unknown_keys_remain_masked_even_when_their_values_contain_known_names():
    errors = rejected('{"PRIVATE-KEY":{"objects":[]},"PRIVATE-KEY":{"objects":["PRIVATE-VALUE"]}}')
    assert errors[0]["path"] == '["<undeclared>"]'
    assert "PRIVATE" not in json.dumps(errors)


def test_escaped_protocol_key_is_still_the_same_duplicate_key():
    error, = rejected(r'{"objects":[],"\u006fbjects":[{}]}')
    assert error["path"] == "objects" and error["occurrences"] == 2


def test_repeated_keys_in_separate_objects_are_not_duplicates():
    payload = {"action": "decide", "question": "Identity", "objects": [], "relations": [],
               "bindings": [{"observation_id": name, "subject_id": None, "status": "unknown",
                             "evidence": [], "reason": "No identity evidence", "target_match": "unknown"}
                            for name in ("obs-a", "obs-b")]}
    before = Step.model_json_schema()
    step = parse_step(json.dumps(payload))
    assert [item.observation_id for item in step.bindings] == ["obs-a", "obs-b"]
    assert Step.model_json_schema() == before


def test_structural_extra_fields_are_masked_without_hiding_known_fields():
    with pytest.raises(VerificationValidationError) as caught:
        parse_step('{"action":"decide","question":{},"PRIVATE-KEY":"PRIVATE-VALUE"}')
    error = caught.value
    assert error.code == "invalid_analysis_contract"
    assert [item["path"] for item in error.details["errors"]] == [["question"], ["<undeclared>"]]
    assert "PRIVATE" not in json.dumps(error.details)


def test_array_metadata_does_not_mislabel_objects_as_arrays_or_leak_scalars():
    with pytest.raises(JSONValidationError) as caught:
        load_json('{"value":{"x":"PRIVATE"},"value":"PRIVATE"}')
    error = caught.value.diagnostic["errors"][0]
    assert error["first_actual_type"] == "object" and error["actual_type"] == "string"
    assert "array_length" not in error and "first_array_length" not in error
    assert "PRIVATE" not in json.dumps(error)


def test_third_declaration_is_compared_to_first_without_overwriting_it():
    errors = rejected('{"objects":[],"objects":[{}],"objects":[{},{}]}')
    assert [item["first_member_index"] for item in errors] == [0, 0]
    assert [item["member_index"] for item in errors] == [1, 2]
    assert [item["occurrences"] for item in errors] == [3, 3]
    assert [item["first_array_length"] for item in errors] == [0, 0]
    assert [item["array_length"] for item in errors] == [1, 2]
