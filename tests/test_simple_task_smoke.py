"""Run the standalone simple-task wheel acceptance from the source environment."""

import json
from pathlib import Path
import subprocess
import sys


def test_simple_task_smoke(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'scripts/simple_task_smoke.py'
    output = tmp_path / 'a'
    process = subprocess.run([sys.executable, '-X', 'utf8', str(script), '--output-dir', str(output)],
                             capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert process.returncode == 0, process.stdout + process.stderr
    summary = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
    assert summary['passed'] and summary['real_model_calls'] == summary['network_attempts'] == 0
    assert summary['target_count'] == 3 and summary['example_target_count'] == 1
    assert summary['prose_records'] == 2 and summary['table_records'] == 1
    assert summary['reported_targets'] == 2 and summary['report_includes_unobserved_target']
    assert summary['completed_resume_new_calls'] == summary['cli_check_synthetic_calls'] == 0
    assert summary['cli_exit_status'] == 0 and summary['synthetic_backend_generate_calls'] == 4
    assert summary['historical_artifacts_unchanged']
