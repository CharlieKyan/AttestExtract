"""Source coverage for the experimental entity-first strategy is independent of its roster."""

import pytest

from attestextract.entity_first_blocks import source_blocks


def test_text_without_numeric_or_owner_cues_and_image_gaps_stay_in_order():
    source = "# Trial\r\n\r\nA strip is smooth.\r\n\r\n![scan](page.png)\r\n\r\n# Later trial\r\n\r\nThe remaining material was brittle."
    blocks = source_blocks(source)
    assert "".join(b["text"] for b in blocks) == source
    assert [b["kind"] for b in blocks] == ["text", "image", "text"]
    assert blocks[1]["gap"] == "image_content_unavailable"
    assert blocks[0]["scope"] != blocks[2]["scope"]
    assert blocks[-1]["gap"] is None


def test_wide_scope_persists_across_table_windows_with_qualifier_context():
    table = "| Device | Rate |\n| --- | --- |\n" + "".join(f"| U{i} | {i*3} |\n" for i in range(25))
    source = "# Test A\n\nDaily readings\n\n" + table + "\nNote: readings taken at noon.\n"
    blocks = source_blocks(source, max_chars=250)
    windows = [b for b in blocks if b["kind"] == "table"]
    assert len(windows) >= 2
    assert "".join(b["text"] for b in blocks) == source
    assert all(b["scope"] == "section@0" for b in windows)
    assert any(c["role"] == "table_header" and "Device" in c["text"] for c in windows[-1]["context"])
    for b in blocks:
        for c in b["context"]:
            assert source[c["start"]:c["end"]] == c["text"]


def test_malformed_grid_is_reported_at_its_original_location():
    source = "Look here:\n\n| A | B |\n| --- | --- |\n| z |\n\nThe next paragraph stands alone."
    blocks = source_blocks(source)
    invalid = [b for b in blocks if b["gap"] == "invalid_table_grid"]
    assert len(invalid) == 1 and "| z |" in invalid[0]["text"]
    assert "stands alone." in blocks[-1]["text"] and not blocks[-1]["gap"]


@pytest.mark.parametrize("source", ["", " \r\n "])
def test_empty_source_is_not_reported_as_covered(source):
    with pytest.raises(ValueError):
        source_blocks(source)


def test_each_paragraph_that_fits_stays_with_its_number_and_unit():
    lines = ["Specimen A has mass 40 g.", "Specimen A has temperature 20 K."]
    source = "\n\n".join(lines)
    blocks = source_blocks(source, max_chars=max(map(len, lines)))
    assert [b["text"].strip() for b in blocks] == lines
    assert "".join(b["text"] for b in blocks) == source
