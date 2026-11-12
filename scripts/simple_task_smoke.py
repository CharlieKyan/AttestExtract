"""Offline acceptance of simple tasks from an installed wheel, outside the checkout.

All input and responses are synthetic. No source-path injection or test imports
are used, and socket access is blocked for the complete acceptance run.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import attestextract
from attestextract import ExecutionOptions, RunTrace, cli, extract
from attestextract.models import BackendResponse
from attestextract.registry import BackendRegistry
from attestextract.reporting import render_markdown_report


PROSE = 'Sample A mass ranges from 10-20 g; mean mass is 15 g.'
TABLE = '<table><tr><th>Sample</th><th>Appearance</th></tr><tr><td>B</td><td>blue</td></tr></table>'
SOURCE = PROSE + '\n\n' + TABLE
EXAMPLE = 'Sample Z has mass 2 g.'
TASK = {
    'prompt_description': 'Extract reported properties. Keep ranges and explicitly reported means separately.',
    'field_list': {'mass': 'Reported sample mass', 'appearance': 'Reported appearance',
                   'length': 'Reported sample length'},
    'examples': [{'text': EXAMPLE, 'extractions': [
        {'field': 'mass', 'entity': 'Sample Z', 'value': '2', 'unit': 'g', 'quote': EXAMPLE}]}],
}


class SyntheticBackend:
    name = 'synthetic-simple-task-acceptance'
    mode = 'replay'
    output_protocol = 'flat-v1'

    def __init__(self):
        self.calls = []

    def generate(self, request):
        self.calls.append(request.chunk_id)
        payload = json.loads(request.messages[-1]['content'])
        assert set(payload['task']['record_types']) == {'mass', 'appearance', 'length'}
        assert 'input_materials' not in payload['task']
        if 'table' not in payload:
            assert EXAMPLE not in payload['source']
            assert EXAMPLE in request.messages[0]['content']
            content = {'records': [
                {'record_type': 'mass',
                 'values': {'entity': 'Sample A', 'value': value, 'unit': 'g', 'conditions': condition},
                 'quotes': {name: PROSE for name in ('entity', 'value', 'unit', 'conditions')}}
                for value, condition in (('10-20', 'ranges'), ('15', 'mean'))]}
        else:
            assert payload['table']['table_source'] == TABLE
            assert payload['task_examples'][0]['text'] == EXAMPLE
            table_id = payload['table']['table_id']

            def selection(column, quote):
                return {'ref': f'{table_id}-r0001-c{column:04d}', 'quote': quote, 'offset': 0}

            content = {'records': [{
                'record_id': 'appearance-1', 'record_type': 'appearance',
                'fields': {
                    'entity': {'value': 'B', 'sources': [selection(0, 'B')], 'relation': 'row'},
                    'value': {'value': 'blue', 'sources': [selection(1, 'blue')], 'relation': 'anchor'},
                    'unit': {'value': None, 'sources': [], 'relation': None},
                    'conditions': {'value': None, 'sources': [], 'relation': None},
                }, 'anchors': [selection(1, 'blue')], 'scope': 'individual', 'scope_sources': [],
            }]}
        return BackendResponse(text=json.dumps(content), model='synthetic-fixed-answer')


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def hashes(root: Path) -> dict:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def assert_result(result) -> str:
    assert list(result.task.record_types) == ['mass', 'appearance', 'length']
    assert result.task.input_materials.examples[0].text == EXAMPLE
    assert [(c.fields['value'].value, c.fields['conditions'].value) for c in result.candidates] == [
        ('10-20', 'ranges'), ('15', 'mean')]
    assert all(c.fields['value'].evidence.status == 'matched' for c in result.candidates)
    assert len(result.table_results) == 1
    table = result.table_results[0]
    assert table.candidates[0]['record_type'] == 'appearance'
    assert table.candidates[0]['values']['value'] == 'blue'
    assert table.semantic_verification == 'not_run'
    report = render_markdown_report(result)
    assert '| mass | Reported sample mass | 2 | 0 |' in report
    assert '| appearance | Reported appearance | 1 | 0 |' in report
    assert '| length | Reported sample length | 0 | 0 |' in report
    return report


def accept(root: Path) -> dict:
    backend = SyntheticBackend()
    trace = RunTrace(root / 'python')
    result = extract(text=SOURCE, backend=backend, backend_identity='simple-task-smoke-v1',
                     execution=ExecutionOptions(max_model_calls=2), trace=trace,
                     prompt_description=TASK['prompt_description'], field_list=TASK['field_list'],
                     examples=TASK['examples'])
    assert len(backend.calls) == 2
    (root / 'REPORT.md').write_text(assert_result(result), encoding='utf-8')
    write_json(root / 'result.json', result.model_dump(mode='json'))
    trace.finish(result.model_dump(mode='json'))
    history = hashes(trace.directory)

    resumed_trace = RunTrace(root / 'python-resumed')
    resumed = extract(text=SOURCE, backend=backend, backend_identity='simple-task-smoke-v1',
                      execution=ExecutionOptions(max_model_calls=2), trace=resumed_trace,
                      resume_from=trace.directory, **TASK)
    assert_result(resumed)
    assert len(backend.calls) == 2
    resumed_trace.finish(resumed.model_dump(mode='json'))
    assert hashes(trace.directory) == history

    inputs = root / 'inputs'
    inputs.mkdir()
    (inputs / 'source.md').write_bytes(SOURCE.encode('utf-8'))
    write_json(root / 'task.json', TASK)
    cli_backend = SyntheticBackend()
    registry = BackendRegistry()
    registry.register('synthetic', lambda **_: cli_backend)
    arguments = ['extract', '--input', str(inputs / 'source.md'), '--task', str(root / 'task.json'),
                 '--base-url', 'https://synthetic.invalid/v1', '--model', 'synthetic',
                 '--adapter', 'synthetic', '--progress', 'quiet']
    check_stdout = io.StringIO()
    with redirect_stdout(check_stdout):
        check_status = cli.main([*arguments, '--check'], registry=registry)
    check = json.loads(check_stdout.getvalue())
    assert check_status == 0 and not cli_backend.calls
    assert check['network_calls'] == 0 and check['task']['record_types'] == list(TASK['field_list'])
    status = cli.main(arguments, registry=registry)
    assert status == 0 and len(cli_backend.calls) == 2
    results = list((root / 'outputs').glob('*/result.json'))
    assert len(results) == 1
    cli_result = attestextract.ExtractionResult.model_validate_json(results[0].read_text(encoding='utf-8'))
    report = assert_result(cli_result)
    assert (results[0].parent / 'report.md').read_text(encoding='utf-8') == report
    assert cli_result.task == result.task
    assert cli_result.candidates == result.candidates
    return {'target_count': 3, 'example_target_count': 1, 'prose_records': 2, 'table_records': 1,
            'reported_targets': 2, 'report_includes_unobserved_target': True,
            'completed_resume_new_calls': 0, 'cli_exit_status': status, 'cli_synthetic_calls': 2,
            'cli_check_synthetic_calls': 0, 'historical_artifacts_unchanged': True,
            'synthetic_backend_generate_calls': len(backend.calls) + len(cli_backend.calls)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    root = parser.parse_args().output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    network_attempts = []

    def blocked(*args, **kwargs):
        network_attempts.append(True)
        raise AssertionError('Network access is forbidden in simple-task acceptance')

    environment = {key: value for key, value in os.environ.items() if not key.startswith('ATTESTEXTRACT_')}
    environment['ATTESTEXTRACT_MODEL_CACHE_DIR'] = str(root / 'model-cache')
    with patch.dict(os.environ, environment, clear=True), patch('socket.socket.connect', blocked), \
            patch('socket.socket.connect_ex', blocked), patch('socket.create_connection', blocked), \
            patch('socket.getaddrinfo', blocked):
        details = accept(root)
    assert not network_attempts
    summary = {'passed': True, 'package_version': attestextract.__version__,
               'module_path': str(Path(attestextract.__file__).resolve()),
               'real_model_calls': 0, 'network_attempts': 0, **details,
               'notice': 'Synthetic offline installed-package acceptance; no API or scientific verification.'}
    write_json(root / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
