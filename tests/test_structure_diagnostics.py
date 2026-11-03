"""Hand-written invalid structures produce hints, never repaired records or values."""

import json

import pytest

from attestextract.structure_diagnostics import diagnose_structure


KEYS = {"records", "record_type", "fields", "subject", "parameter", "value", "quote"}
BROKEN = ('{"records":[{"record_type":"reading","fields":'
          '{"subject":{"value":"Synthetic sample","quote":"Synthetic sample"},'
          '{"parameter":{"value":"Depth","quote":"Depth"}}}}]}')


def diagnose(text, *, allowed_keys=KEYS):
    with pytest.raises(json.JSONDecodeError) as error:
        json.loads(text)
    return diagnose_structure(text, error.value.pos, allowed_keys=allowed_keys)


def test_anonymous_field_object_has_value_free_local_shape():
    hint = diagnose(BROKEN)
    assert hint["code"] == "unexpected_object_in_object"
    assert hint["path"] == "records[0].fields"
    assert hint["actual_token"] == "{"
    assert hint["previous_key"] == "subject" and hint["next_key"] == "parameter"
    assert hint["wrong_shape"] == '{"subject": <value>, {"parameter": <value>}}'
    assert hint["correct_shape"] == '{"subject": <value>, "parameter": <value>}'
    encoded = json.dumps(hint)
    assert "Synthetic sample" not in encoded and "Depth" not in encoded and "reading" not in encoded
    # A diagnostic must leave the original response invalid and unchanged.
    assert diagnose(BROKEN) == hint


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


def test_unknown_keys_and_values_are_masked_in_path_and_shapes():
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
    assert hint["wrong_shape"] == '{{"parameter": <value>}}'
    assert hint["correct_shape"] == '{"parameter": <value>}'


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
    assert len(hint["wrong_shape"]) <= 384 and len(hint["correct_shape"]) <= 384
    assert len(hint["message"]) <= 256


def test_long_or_control_character_keys_are_withheld_even_if_declared():
    key = "X" * 100
    text = '{"fields":{' + json.dumps(key) + ':null,{' + json.dumps('\n') + ':null}}}'
    hint = diagnose(text, allowed_keys={key, '\n', 'fields'})
    assert hint["previous_key"] == hint["next_key"] == "<undeclared>"
