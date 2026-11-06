"""Installed-package acceptance for flat responses and exact range evidence.

Synthetic data and offline replay only; never reads credentials or calls a model.
"""

import argparse
import json
from pathlib import Path

import attestextract
from attestextract import Document, create_archive, extract, load_archive, save_archive
from attestextract.backends import ReplayBackend
from attestextract.errors import InvalidResponseError
from attestextract.reporting import render_markdown_report


TASK = {"name": "Synthetic range acceptance", "record_types": {"reading": {
    "fields": {"quantity": {"type": "string", "required": True},
               "unit": {"type": "string", "required": True}},
    "measurement_groups": {"reading": {"value_field": "quantity", "unit_field": "unit"}},
}}}


def response(source, value, unit):
    return {"records": [{"record_type": "reading",
        "values": {"quantity": value, "unit": unit},
        "quotes": {"quantity": source, "unit": source},
        "measurements": {"reading": {"context_quote": source, "value_text": value,
                                      "unit_text": unit, "unit_quote": ""}},
    }]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    root = parser.parse_args(argv).output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    cases = [
        ("repeated_units", r"X2 content 11%\~24%", r"11%\~24%", "%", []),
        ("shared_unit", "length -2.5–3.0 m", "-2.5–3.0", "m", []),
        ("mixed_units", "length 2 m–4 cm", "2 m–4 cm", "m", ["measurement_value_structure_unresolved"]),
        ("independent_number", "$x_2=8$; content 11%", "11", "%", ["measurement_multiple_values"]),
    ]
    results = []
    for name, source, value, unit, expected in cases:
        document = Document.from_text(source)
        result = extract(document, TASK, backend=ReplayBackend([response(source, value, unit)]))
        measurement = result.candidates[0].measurements["reading"]
        assert measurement.issues == expected, (name, measurement.issues)
        assert result.schema_version == "0.4" and result.run.output_protocol == "flat-v1"
        assert measurement.semantic_status == "not_verified"
        archive = create_archive(document, result)
        assert archive.records == []
        save_archive(archive, root / f"{name}.archive.json")
        assert load_archive(root / f"{name}.archive.json") == archive
        (root / f"{name}.report.md").write_text(render_markdown_report(result), encoding="utf-8")
        if name == "repeated_units":
            assert measurement.unit.status == "ambiguous"
            assert measurement.range_binding.unit_layout == "per_endpoint"
            assert [e.quote for e in measurement.range_binding.endpoints] == ["11", "24"]
            assert "两个端点分别绑定单位" in render_markdown_report(result)
        results.append({"case": name, "issues": measurement.issues,
                        "range_binding": measurement.range_binding.model_dump() if measurement.range_binding else None})

    source = "Reading 12."
    task = {"name": "Legacy replay only", "record_types": {"reading": {
        "fields": {"quantity": {"type": "integer", "required": True}},
    }}}
    native = {"records": [{"record_type": "reading", "fields": {
        "quantity": {"value": 12, "quote": source},
    }}]}
    old = extract(source, task, backend=ReplayBackend([native], output_protocol="native-v1"))
    assert old.run.output_protocol == "native-v1"
    try:
        extract(source, task, backend=ReplayBackend([native]))
    except InvalidResponseError:
        pass
    else:
        raise AssertionError("Native data must not be auto-detected as a new flat response")
    summary = {"package_version": attestextract.__version__, "module_path": str(Path(attestextract.__file__).resolve()),
               "passed": True, "real_endpoint_calls": 0, "mock_http_calls": 0,
               "legacy_replay_passed": True, "implicit_native_rejected": True, "cases": results}
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "real_endpoint_calls": 0, "output": str(root)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
