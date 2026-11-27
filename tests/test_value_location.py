import pytest

from niceextract.dataset import _has_value_source
from niceextract.value_location import locate_value


def test_escaped_range_preserves_source_offsets_and_rejects_two_spellings_at_two_places():
    raw = r"7.12\%\~8.91\%"
    value = "7.12%~8.91%"
    source = "Specimen Ω " + raw + ". Reference: " + value
    start = source.index(raw)
    end = start + len(raw)
    assert locate_value(source, value, start, end) == {"start": start, "end": end, "quote": raw}
    assert not locate_value(source, value, 0, len(source))
    assert not locate_value(source, "7.12%~8.9%", start, end)


def test_typeset_range_requires_both_endpoints_scale_and_unit_and_returns_the_whole_formula():
    raw = r"$( 3 0 . 1 2 - 1 2 0 . 6 0 ) \times 1 0 ^ { - 5 } ~ { \mu \mathrm { s } } ^ { 2 }$"
    source = "Sensor variance: " + raw + "."
    value = "(30.12-120.60)×10^-5 µs^2"
    start = source.index("$")
    assert locate_value(source, value, 0, len(source)) == {"start": start, "end": start+len(raw), "quote": raw}
    for incompatible in ("(30.12-120.60)", "30.12", "(30.12-120.60)×10^5 µs^2",
                         "(30.12-120.60)×10^-5 µs", "(30.12-120.06)×10^-5 µs^2",
                         "(30.12-120.60)×10^-5×10^-5 µs^2"):
        assert not locate_value(source, incompatible, 0, len(source))
    item = {"value": value, "unit": "×10^-5 µs^2", "source_evidence": [
        {"fields": ["value", "unit"], "span": {"start": 0, "end": len(source)}}]}
    assert _has_value_source(item, source)


@pytest.mark.parametrize("source,value", [
    (r"Area $3 \times 10^{4}$", "4"),
    (r"Rate $\frac{9}{5}$", "9"),
    (r"Channels $1 2$", "12"),
    ("Code R2.7~9.1", "2.7~9.1"),
    ("56.3~79.12", "56.3~79.1"),
    ("1,300", "300"),
    ("4 mmol", "4 m"),
    ("(12-40) ×10^-3 g", "(12-40)"),
    (r"$$3\times 10^{4}$$", "10"),
    (r"\[3\times 10^{4}\]", "4"),
])
def test_identifiers_exponents_partial_tokens_and_unsupported_math(source, value):
    assert not locate_value(source, value, 0, len(source))


def test_single_value_with_a_separate_unit_in_math_keeps_number():
    source = r"An instrument read $4.2\mathrm{mg}$."
    start = source.index("4")
    assert locate_value(source, "4.2", 0, len(source)) == {"start": start, "end": start+3, "quote": "4.2"}


def test_superscripts_and_scales_require_the_same_exponents_in_the_original():
    source = r"$$6\times 10^{-2}\mathrm{cm}^{2}$$"
    assert locate_value(source, "6×10⁻² cm²", 0, len(source))["quote"] == source
    assert not locate_value(source, "6×10² cm²", 0, len(source))


def test_formula_parameter_value_can_be_located_without_inheriting_its_scientific_role():
    source = r"Model input $p = 3 . 2 0 \%$."
    assert locate_value(source, "3.20", 0, len(source))["quote"] == r"3 . 2 0 \%"
    assert not locate_value(source.replace(" = ", " = x + "), "3.20", 0, len(source) + 4)
