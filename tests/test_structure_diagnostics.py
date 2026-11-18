"""Hand-written invalid structures produce observations, never repair suggestions.

The former shape hints guessed whether a brace should be removed. In particular,
a missing record boundary can look like an extra opening brace inside a record;
the diagnostic now reports the observed position without choosing a repair.
"""

import json

import pytest

from attestextract.structure_diagnostics import OBSERVED_OBJECT_MESSAGE, diagnose_structure


KEYS = {"records", "record_type", "fields", "subject", "parameter", "value", "quote"}
BROKEN = ('{"records":[{"record_type":"reading","fields":'
          '{"subject":{"value":"Synthetic sample","quote":"Synthetic sample"},'
          '{"parameter":{"value":"Depth","quote":"Depth"}}}}]}')


def diagnose(text, *, allowed_keys=KEYS):
    with pytest.raises(json.JSONDecodeError) as error:
        json.loads(text)
    return diagnose_structure(text, error.value.pos, allowed_keys=allowed_keys)


def test_anonymous_field_object_has_only_value_free_observations():
    hint = diagnose(BROKEN)
    assert hint["code"] == "unexpected_object_in_object"
    assert hint["path"] == "records[0].fields"
    assert hint["actual_token"] == "{"
    assert hint["previous_key"] == "subject" and hint["next_key"] == "parameter"
    assert set(hint) == {"code", "path", "actual_token", "previous_key", "next_key", "message"}
    assert hint["message"] == OBSERVED_OBJECT_MESSAGE
    encoded = json.dumps(hint)
    assert "Synthetic sample" not in encoded and "Depth" not in encoded and "reading" not in encoded
    # A diagnostic must leave the original response invalid and unchanged.
    assert diagnose(BROKEN) == hint


def test_missing_record_boundary_and_quotes_do_not_imply_a_brace_repair():
    # Two intended records, a missing closing boundary after the first values
    # object, and no quotes in either record. The JSON error is not sufficient
    # to recover the boundary, supply quotes, or declare either record valid.
    text = ('{"records":[{"record_type":"reading","values":{"entity":"Sample A","value":"12"},'
            '{"record_type":"reading","values":{"entity":"Sample B","value":"13"}}]}')
    hint = diagnose(text, allowed_keys=KEYS | {"values", "quotes", "entity"})
    assert hint == {
        "code": "unexpected_object_in_object",
        "path": "records[0]",
        "actual_token": "{",
        "previous_key": "values",
        "next_key": "record_type",
        "message": OBSERVED_OBJECT_MESSAGE,
    }
    assert "a record boundary or repair cannot be inferred" in hint["message"]
    assert "not necessarily the origin" in hint["message"]
    encoded = json.dumps(hint)
    assert "Sample A" not in encoded and "Sample B" not in encoded
    assert "Keep named properties as siblings" not in encoded
    # No response repair or invented evidence: the source remains invalid.
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


def test_array_siblings_are_not_object_siblings():
    text = '{"records":[{"fields":{}},{"fields":{}}]}'
    assert json.loads(text)["records"] == [{"fields": {}}, {"fields": {}}]
    assert diagnose_structure(text, text.index(',{') + 1, allowed_keys=KEYS) is None


def test_repeated_array_and_nested_states_have_correct_path():
    text = '{"records":[{"fields":{}},{"fields":{"subject":null,{"parameter":1}}}]}'
    assert diagnose(text)["path"] == "records[1].fields"


@pytest.mark.parametrize("value", [None, True, False, 0, -12.75, 1e12, [], {}, [1, {"private": "hidden"}]])
def test_scalar_and_container_values_do_not_escape_to_hint(value):
    text = '{"fields":{"subject":' + json.dumps(value) + ',{"parameter":null}}'
    hint = diagnose(text)
    assert hint["path"] == "fields"
    assert hint["previous_key"] == "subject"
    assert "hidden" not in json.dumps(hint)


@pytest.mark.parametrize("value", ['quoted },{"parameter":', 'escaped " and \\ braces {}', '\n\t🙂', 'subject'])
def test_braces_and_keys_inside_strings_are_data(value):
    text = '{"fields":{"subject":' + json.dumps(value) + ',{"parameter":null}}'
    hint = diagnose(text)
    assert hint["path"] == "fields" and hint["next_key"] == "parameter"
    if value == 'quoted },{"parameter":':
        inside = text.index("{", text.index("quoted"))
        assert diagnose_structure(text, inside, allowed_keys=KEYS) is None


def test_unknown_keys_and_values_are_masked_in_observations():
    text = '{"PRIVATE_CONTAINER":{"PRIVATE_OLD":"SECRET_VALUE",{"PRIVATE_NEW":"SECOND_SECRET"}}}'
    hint = diagnose(text)
    assert hint["path"] == '["<undeclared>"]'
    assert hint["previous_key"] == hint["next_key"] == "<undeclared>"
    assert all(secret not in json.dumps(hint) for secret in ("PRIVATE", "SECRET", "SECOND"))


def test_unknown_next_key_with_escapes_is_not_echoed():
    text = '{"fields":{"subject":1,{"secret\\\"key":null}}}'
    hint = diagnose(text)
    assert hint["next_key"] == "<undeclared>" and "secret" not in json.dumps(hint)


def test_first_anonymous_property_has_no_previous_key():
    hint = diagnose('{"fields":{{"parameter":1}}}')
    assert hint["path"] == "fields" and hint["previous_key"] is None
    assert hint["next_key"] == "parameter" and hint["actual_token"] == "{"
    assert "wrong_shape" not in hint and "correct_shape" not in hint


@pytest.mark.parametrize("text", [
    '{"fields":{"subject":1,}}',
    '{"fields":{"subject" 1,{"parameter":2}}}',
    '{"fields":{"subject":NaN,{"parameter":2}}}',
    '{"fields":{"subject":"unterminated,{"parameter":2}}}',
    '{"fields":{"subject":1,{broken}}}',
    '{"fields":{"subject":1,{"parameter" 2}}}',
])
def test_unrecognized_syntax_keeps_generic_decoder_error(text):
    assert diagnose(text) is None


def test_offset_beyond_earlier_syntax_error_is_not_trusted():
    text = '{"fields":{"subject" 1,{"parameter":2}}}'
    assert diagnose_structure(text, text.index(',{') + 1, allowed_keys=KEYS) is None


@pytest.mark.parametrize("offset", [-1, 999999, True, None, 1.5])
def test_invalid_offsets_return_no_hint(offset):
    assert diagnose_structure(BROKEN, offset, allowed_keys=KEYS) is None


def test_whitespace_and_unicode_offsets_still_match_local_structure():
    text = '\r\n{"fields": {"subject":"🙂汉字",\r\n { "parameter":null}}}'
    hint = diagnose(text)
    assert hint["path"] == "fields" and hint["next_key"] == "parameter"


def test_resource_limits_are_conservative():
    oversized = '{"fields":{"subject":"' + 'x' * 131072 + '",{"parameter":null}}}'
    assert diagnose(oversized) is None
    deep = '{"fields":' * 34 + '{"subject":null,{"parameter":null}}' + '}' * 34
    assert diagnose(deep) is None
    many = '{"records":[' + 'null,' * 8200 + '{"subject":null,{"parameter":null}}]}'
    assert diagnose(many) is None


def test_hint_strings_have_bounded_lengths():
    old, new = '"' * 64, '\\' * 64
    text = '{' + json.dumps(old) + ':null,{' + json.dumps(new) + ':null}}'
    hint = diagnose(text, allowed_keys={old, new})
    assert len(hint["path"]) <= 256
    assert len(hint["previous_key"]) <= 64 and len(hint["next_key"]) <= 64
    assert len(hint["message"]) <= 256


def test_long_or_control_character_keys_are_withheld_even_if_declared():
    key = "X" * 100
    text = '{"fields":{' + json.dumps(key) + ':null,{' + json.dumps('\n') + ':null}}}'
    hint = diagnose(text, allowed_keys={key, '\n', 'fields'})
    assert hint["previous_key"] == hint["next_key"] == "<undeclared>"
