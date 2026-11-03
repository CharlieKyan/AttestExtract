"""The distributable acceptance uses the CLI and synthetic HTTP in a fresh process."""

import json
from pathlib import Path
import subprocess
import sys


def test_structured_output_runner(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/structured_output_smoke.py"
    output = tmp_path / "acceptance"
    completed = subprocess.run([sys.executable, str(script), "--output-dir", str(output)],
                               capture_output=True, text=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] and summary["real_endpoint_calls"] == 0
    assert summary["mock_http_calls"] == 4 and summary["completed_resume_new_calls"] == 0
    assert summary["responses_schema_sent"] and summary["localized_format_feedback"]
    assert summary["server_enforcement"] == "not_verified"
