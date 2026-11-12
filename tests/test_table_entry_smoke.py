"""The standalone installed-package acceptance also runs from the source environment."""

import json
from pathlib import Path
import subprocess
import sys


def test_table_entry_acceptance_script(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'scripts/table_entry_smoke.py'
    output = tmp_path / 'acceptance'
    process = subprocess.run([sys.executable, '-X', 'utf8', str(script), '--output-dir', str(output)],
                             capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert process.returncode == 0, process.stdout + process.stderr
    summary = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
    assert summary['passed'] and summary['real_model_calls'] == summary['network_attempts'] == 0
    assert summary['record_count'] == 3 and summary['table_count'] == 2 and summary['isolated_records'] == 1
    assert summary['resume_new_calls'] == 2 and summary['completed_resume_new_calls'] == 0
    assert summary['synthetic_backend_generate_calls'] == 4 and summary['cli_exit_status'] == 2
    assert summary['historical_artifacts_unchanged']
