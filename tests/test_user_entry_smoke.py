"""The standalone acceptance runner must work without a project bootstrap."""

import json
from pathlib import Path
import subprocess
import sys


def test_user_entry_acceptance_runner(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "user_entry_smoke.py"
    output = tmp_path / "u"
    result = subprocess.run([sys.executable, str(script), "--output-dir", str(output)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] and summary["mock_model_posts"] == 3
    assert summary["network_attempts"] == summary["real_model_calls"] == 0
