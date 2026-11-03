"""Exercise the distributable recovery acceptance runner in a fresh process."""

import json
from pathlib import Path
import subprocess
import sys


def test_installed_format_recovery_runner(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "format_recovery_smoke.py"
    output = tmp_path / "acceptance"
    completed = subprocess.run([sys.executable, str(script), "--output-dir", str(output)],
                               capture_output=True, text=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] and summary["real_endpoint_calls"] == 0
    assert summary["mock_http_calls"] == 5
    assert summary["recovery"]["candidate_values"] == [11, 22]
    assert summary["exhausted"]["resume_new_calls"] == 0
