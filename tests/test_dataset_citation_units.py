import pytest

from niceextract.dataset_citation import check_reading


def _check(source, value, unit, entity="Unit A", *, quoted=True, owners=()):
    spans = [{"fields": ["value"], "quote": source, "span": {"start": 0, "end": len(source)}}] if quoted else []
    obs = {"source_kind": "prose", "entity": entity, "value": value, "unit": unit, "source_evidence": spans}
    return check_reading(source, obs, owners=owners)


@pytest.mark.parametrize("phrase,value,unit,status", [
    ("8 m²", "8", "m", "unverified"),
    ("8 m2", "8", "m", "unverified"),
    ("8 cm/s", "8 cm/s", "s", "unverified"),
    ("8 m s⁻¹", "8 m", "m", "unverified"),
    ("12 kg / s", "12 kg", "kg", "unverified"),
    ("12 kg per second", "12", "kg", "unverified"),
    ("12 kg h-1", "12", "kg", "unverified"),
    ("12 kg m−3", "12 kg", "kg", "unverified"),
    ("12 kg per second", "12", "kg per second", "located"),
    ("12 kg h-1", "12", "kg h-1", "located"),
    ("12 kg recorded in the second run", "12", "kg", "located"),
    ("12 kg persisted after drying", "12 kg", "kg", "located"),
    ("8 µm²", "8", "μm^2", "located"),
    ("1.25%～2.15%", "1.25%～2.15%", "%", "located"),
    (r"$(1.2-3.4) \times 10^{-3} {\mu \mathrm{m}}^{2}$", "(1.2-3.4)×10^-3 μm^2", "×10^-3 μm^2", "located"),
    (r"$(1.2-3.4) \times 10^{-3} {\mu \mathrm{m}}^{2}$", "(1.2-3.4)×10^-3 μm^2", "×10^-6 μm^2", "unverified"),
    ("120 mm", "12", "mm", "unverified"),
    (r"$(1.2-3.4) \times 10^{-3} {\mu \mathrm{m}}^{2}$", "(1.2-3.4)×10^-3", "μm^2", "located"),
    (r"$(1.2-3.4) \times 10^{-3} {\mu \mathrm{m}}^{2}$", "(1.2-3.4)", "μm^2", "unverified"),
])
def test_whole_quantity_and_unit_match_in_original_positions(phrase, value, unit, status):
    source = "Unit A: " + phrase + "."
    result = _check(source, value, unit)
    assert result["citation_status"] == status
    assert all(source[e["start"]:e["end"]] == e["quote"] for e in result["citation_evidence"])


@pytest.mark.parametrize("text", [
    "Unit A was idle. Unit B registered 12 cm.",
    "Unit A paused while Unit B registered 12 cm.",
    "Unit A3 registered 12 cm.",
    "Unit A registered 12 cm. Unit A again registered 12 cm.",
    "Unit A had 12 cm and an anonymous sample had 12 cm.",
])
def test_questionable_replacements_remain_as_candidates(text):
    report = _check(text, "12", "cm", quoted=False, owners=["Unit A", "Unit B"])
    assert report["citation_status"] == "unverified" and report["citation_candidates"]
    assert all(text[e["start"]:e["end"]] == e["quote"] for e in report["citation_candidates"])


def test_one_matching_scope_with_same_digits_elsewhere():
    source = "Unit B measured 12 cm.\r\nUnit A measured 12 cm."
    report = _check(source, "12", "cm", quoted=False, owners=["Unit A", "Unit B"])
    assert report["citation_status"] == "located"
    excerpt, = report["citation_evidence"]
    assert excerpt["quote"] == "Unit A measured 12 cm." == source[excerpt["start"]:excerpt["end"]]


def test_explicit_reread_checks_only_the_selected_span():
    source = "A separate comment. Unit A measured 12 cm."
    obs = {"source_kind": "prose", "entity": "Unit A", "value": "12", "unit": "cm",
           "source_evidence": [{"fields": ["value"], "quote": "A separate comment.",
                                "span": {"start": 0, "end": len("A separate comment.")}}]}
    assert check_reading(source, obs, allow_search=False)["citation_status"] == "unverified"
    assert check_reading(source, obs)["citation_status"] == "located"


def test_zero_reading_can_be_found_outside_its_wrong_citation():
    assert _check("Unit A measured 0 cm.", 0, "cm", quoted=False)["citation_status"] == "located"


def test_citation_sentence_then_line_are_shown_first_without_selecting_a_duplicate():
    source = "Unit A measured 12 cm yesterday.\r\nUnit A measured 12 cm before lunch. Unit A again measured 12 cm."
    start = source.index("again")
    obs = {"source_kind": "prose", "entity": "Unit A", "value": 12, "unit": "cm",
           "source_evidence": [{"fields": ["value"], "quote": "again", "span": {"start": start, "end": start + 5}}]}
    report = check_reading(source, obs)
    assert report["citation_status"] == "unverified"
    assert [c["quote"].strip() for c in report["citation_candidates"]] == [
        "Unit A again measured 12 cm.", "Unit A measured 12 cm before lunch.", "Unit A measured 12 cm yesterday."]


@pytest.mark.parametrize("heading,cell,unit,linked,status", [
    ("Velocity (m/s)", "8", "m/s", True, "located"),
    ("Velocity (m/s)", "8", "s", True, "unverified"),
    ("cm/s", "8", "s", True, "unverified"),
    ("Velocity (m/s)", "8 kg", "m/s", True, "unverified"),
    ("Velocity (m/s)", "8", "m/s", False, "unverified"),
])
def test_only_the_cited_cell_or_linked_unit_header_can_supply_the_unit(heading, cell, unit, linked, status):
    source = f"{heading} | ignored header\n{cell} | 8 kg"
    cell_start = source.index("\n") + 1
    hdr = {"quote": heading, "start": 0, "end": len(heading)}
    pos = {"quote": cell, "start": cell_start, "end": cell_start + len(cell)}
    unit_span = hdr if linked else {"start": source.index("ignored"), "end": source.index("ignored") + 7}
    obs = {"source_kind": "table", "value": "8", "unit": unit, "entity": "sample",
           "source_evidence": [{"fields": ["unit"], "span": unit_span}],
           "table_context": {"headers": [hdr], "value": pos}}
    assert check_reading(source, obs)["citation_status"] == status
