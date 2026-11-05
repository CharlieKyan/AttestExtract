"""Run the same synthetic acceptance shipped for installed packages."""

import json
from pathlib import Path
import subprocess
import sys


def test_protocol_and_range_acceptance(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "protocol_evidence_smoke.py"
    target = tmp_path / "acceptance"
    completed = subprocess.run([sys.executable, "-X", "utf8", str(script), "--output-dir", str(target)],
                               capture_output=True, text=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads((target / "summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] and summary["real_endpoint_calls"] == 0
    assert summary["legacy_replay_passed"] and summary["implicit_native_rejected"]
    assert len(summary["cases"]) == 4
