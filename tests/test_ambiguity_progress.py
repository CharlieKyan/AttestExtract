"""Progress distinguishes isolated proposals from successful or failed decisions."""

from io import StringIO

from niceextract.progress import ConsoleProgress


def test_review_event_reports_counts_without_echoing_private_proposals():
    stream = StringIO()
    console = ConsoleProgress(stream=stream)
    console({"stage": "analysis", "event": "review_required", "job_id": "verify-0001",
        "observation_count": 6, "used": 3, "limit": 43,
        "validation_details": "analysis/validation/verify-0001.step-03.errors.json",
        "error": "PRIVATE RAW TEXT", "validation_summary": {"quote": "PRIVATE QUOTE"}})
    text = stream.getvalue()
    assert "6 observations isolated for review" in text and "calls 3/43" in text
    assert "No group proposals were applied" in text and "Later jobs may continue" in text
    assert "no automatic correction or retry" in text and "PRIVATE" not in text


def test_review_resume_and_completion_never_claim_complete_analysis():
    stream = StringIO()
    console = ConsoleProgress(stream=stream)
    console({"stage": "analysis", "event": "reused", "job_id": "verify-0001", "status": "review_required"})
    console({"stage": "complete", "event": "analysis_review_required"})
    text = stream.getvalue()
    assert "preserved review_required state; no request resent" in text
    assert "isolated group remains unapplied" in text and "Analysis remains partial" in text
    assert "reused validated response" not in text


def test_review_progress_ignores_unsafe_detail_path_and_count():
    stream = StringIO()
    console = ConsoleProgress(stream=stream)
    console({"stage": "analysis", "event": "review_required", "observation_count": "PRIVATE",
             "validation_details": "../PRIVATE.txt"})
    assert "PRIVATE" not in stream.getvalue()
