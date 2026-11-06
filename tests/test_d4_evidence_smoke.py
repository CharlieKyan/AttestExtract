"""The D4 installed-package acceptance also runs as an ordinary subprocess test."""

import json
from pathlib import Path
import subprocess
import sys


def test_d4_evidence_installed_acceptance(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "d4_evidence_smoke.py"
    output = tmp_path / "d4-acceptance"
    completed = subprocess.run([sys.executable, "-X", "utf8", str(script), "--output-dir", str(output)],
                               capture_output=True, text=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] and summary["real_endpoint_calls"] == summary["mock_http_calls"] == 0
    assert summary["offline_replay_calls"] == 3
    assert summary["synthetic_backend_generate_calls"] > 1
    assert summary["resume_additional_calls"] == 0
    assert summary["cases"] == ["normalized-review", "html-binding", "escaped-pipe-binding", "context-truncation-resume"]
    assert len(list(output.glob("*.archive.json"))) == 4
