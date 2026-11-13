"""Saved synthetic results can be reviewed without credentials or generation."""
import io
import json
import socket

import pytest

from attestextract import cli
from attestextract.diagnostics import RunTrace
from attestextract.progress import ConsoleProgress


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbid(*args, **kwargs):
        pytest.fail("Expert export must not access the network")
    monkeypatch.setattr(socket, "getaddrinfo", forbid)
    monkeypatch.setattr(socket.socket, "connect", forbid)


def saved_run(tmp_path):
    run = tmp_path / "original"
    run.mkdir()
    source = run / "result.json"
    source.write_text(json.dumps(cli._demo("ccus"), ensure_ascii=False), encoding="utf-8")
    return run, source


def test_offline_export_accepts_old_result_without_modifying_original(tmp_path, capsys):
    run, source = saved_run(tmp_path)
    before = source.read_bytes()
    out = tmp_path / "expert"
    assert cli.main(["export-review", "--input", str(run), "--output-dir", str(out)]) == 0
    assert source.read_bytes() == before
    assert set(p.name for p in run.iterdir()) == {"result.json"}
    view = json.loads((out / "review.json").read_text(encoding="utf-8"))
    assert view["source_result"] == str(source.resolve())
    assert (out / "review.md").is_file()
    assert "Offline display only" in capsys.readouterr().out


def test_export_refuses_to_write_inside_original_run(tmp_path):
    run, source = saved_run(tmp_path)
    assert cli.main(["export-review", "--input", str(source), "--output-dir", str(run / "review")]) == 1
    assert not (run / "review").exists()


def test_export_refuses_invalid_result_and_existing_output(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text('{"candidates": [], "candidates": []}', encoding="utf-8")
    out = tmp_path / "expert"
    assert cli.main(["export-review", "--input", str(source), "--output-dir", str(out)]) == 1
    assert not out.exists()
    _, source = saved_run(tmp_path)
    out.mkdir()
    marker = out / "keep.txt"
    marker.write_text("unchanged")
    assert cli.main(["export-review", "--input", str(source), "--output-dir", str(out)]) == 1
    assert marker.read_text() == "unchanged"


def test_progress_distinguishes_processed_responses_from_quarantined_records(tmp_path):
    stream = io.StringIO()
    progress = ConsoleProgress(stream=stream)
    progress({"stage": "execution", "event": "finished", "completed_chunks": 9, "leaf_chunks": 9,
              "first_pass_chunks": 9, "format_recovered_chunks": 0, "split_chunks": 0,
              "reused_chunks": 0, "failed_chunks": 0, "pending_chunks": 0,
              "calls_reserved": 9, "max_model_calls": 27})
    trace = RunTrace(tmp_path / "diagnostics", on_event=progress)
    trace.finish({"format_status": "partial_format_error", "run": {}, "candidates": [{}] * 3,
                  "table_results": [{"candidates": [], "rejections": [{}] * 2}]})
    progress.close()
    text = stream.getvalue()
    assert "9/9 responses processed" in text
    assert "3 prose candidates retained" in text
    assert "2 table records quarantined" in text
    assert "ordinary resume reuses" in text
    assert "Completed:" not in text
