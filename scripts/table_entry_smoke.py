"""Offline installed-package acceptance of the unified prose/table entry point.

Run this standalone script outside the checkout after installing the wheel. It
uses only synthetic material, never imports tests, and blocks socket access.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import attestextract
from attestextract import ExecutionOptions, RunTrace, cli, extract
from attestextract.errors import BackendError
from attestextract.models import BackendResponse
from attestextract.registry import BackendRegistry
from attestextract.reporting import render_markdown_report


HTML = '<table><tr><th>Sample</th><th>Mass (g)</th></tr><tr><td>A</td><td>17</td></tr></table>'
MARKDOWN = '| Sample | Mass (g) |\n| --- | --- |\n| B | 23 |\n'
SOURCE = 'Sample P has mass 5 g.\n\n' + HTML + '\n\n' + MARKDOWN
TASK = {'name': 'Synthetic unified entry', 'record_types': {'mass': {
    'fields': {'object': {'type': 'string'}, 'value': {'type': 'string'}, 'unit': {'type': 'string'}},
    'identity_fields': ['object'],
    'measurement_groups': {'mass': {'value_field': 'value', 'unit_field': 'unit'}},
}}}
PROSE = {'records': [{'record_type': 'mass', 'values': {'object': 'P', 'value': '5', 'unit': 'g'},
                     'quotes': {'object': 'P', 'value': '5', 'unit': 'g'}}]}


def table_record(table_id: str, subject: str, value: str) -> dict:
    def source(row, column, quote):
        return {'ref': f'{table_id}-r{row:04d}-c{column:04d}', 'quote': quote, 'offset': 0}
    return {'record_id': 'observation', 'record_type': 'mass', 'fields': {
        'object': {'value': subject, 'sources': [source(1, 0, subject)], 'relation': 'row'},
        'value': {'value': value, 'sources': [source(1, 1, value)], 'relation': 'anchor'},
        'unit': {'value': 'g', 'sources': [source(0, 1, 'Mass (g)')], 'relation': 'column'},
    }, 'anchors': [source(1, 1, value)], 'scope': 'individual', 'scope_sources': []}


class SyntheticBackend:
    name = 'synthetic-unified-acceptance'
    output_protocol = 'flat-v1'
    mode = 'replay'

    def __init__(self):
        self.calls = []

    def generate(self, request):
        self.calls.append(request.chunk_id)
        payload = json.loads(request.messages[-1]['content'])
        if 'table' not in payload:
            assert '<table>' not in payload['source'] and '| Sample |' not in payload['source']
            content = PROSE
        else:
            table = payload['table']
            assert table['table_source'] in (HTML, MARKDOWN)
            subject, value = ('A', '17') if table['table_source'] == HTML else ('B', '23')
            record = table_record(table['table_id'], subject, value)
            records = [record]
            if subject == 'B':
                invalid = deepcopy(record)
                invalid['record_id'] = 'isolated-bad-record'
                invalid['fields']['unit'].pop('relation')
                records.append(invalid)
            content = {'records': records}
        return BackendResponse(text=json.dumps(content), model='synthetic-fixed-answer')


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def hashes(root: Path) -> dict:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def accept(root: Path) -> dict:
    (root / 'source.md').write_bytes(SOURCE.encode('utf-8'))
    write_json(root / 'task.json', TASK)
    backend = SyntheticBackend()
    first = RunTrace(root / 'first')
    try:
        extract(SOURCE, TASK, backend=backend, backend_identity='synthetic-fixed-v1', trace=first,
                execution=ExecutionOptions(max_model_calls=1))
    except BackendError as exc:
        assert exc.code == 'call_budget_exhausted'
    else:
        raise AssertionError('The shared budget must stop before a second generation')
    assert backend.calls == ['prose-0001']
    first_hashes = hashes(first.directory)

    resumed_trace = RunTrace(root / 'resumed')
    before = len(backend.calls)
    result = extract(SOURCE, TASK, backend=backend, backend_identity='synthetic-fixed-v1', trace=resumed_trace,
                     execution=ExecutionOptions(max_model_calls=3), resume_from=first.directory)
    resume_calls = len(backend.calls) - before
    assert resume_calls == 2 and backend.calls == ['prose-0001', 'table-0001', 'table-0002']
    assert result.schema_version == '0.6' and result.format_status == 'partial_format_error'
    assert len(result.candidates) == 1 and len(result.table_results) == 2
    assert result.candidates[0].fields['value'].value == '5'
    for table, subject, value in zip(result.table_results, ('A', 'B'), ('17', '23')):
        expected = table_record(table.table_id, subject, value)
        assert len(table.candidates) == 1
        assert all(table.candidates[0][key] == item for key, item in expected.items())
        assert table.semantic_verification == 'not_run'
        assert table.candidates[0]['field_checks']['value']['literal_check'] == 'passed'
    rejected = result.table_results[1].rejections
    assert len(rejected) == 1 and rejected[0]['index'] == 1
    assert rejected[0]['raw']['record_id'] == 'isolated-bad-record'
    write_json(root / 'result.json', result.model_dump(mode='json'))
    (root / 'REPORT.md').write_text(render_markdown_report(result), encoding='utf-8')
    resumed_trace.finish(result.model_dump(mode='json'))
    resumed_hashes = hashes(resumed_trace.directory)

    final_trace = RunTrace(root / 'completed-resume')
    before = len(backend.calls)
    repeated = extract(SOURCE, TASK, backend=backend, backend_identity='synthetic-fixed-v1', trace=final_trace,
                       execution=ExecutionOptions(max_model_calls=3), resume_from=resumed_trace.directory)
    assert len(backend.calls) == before and repeated.table_results == result.table_results
    final_trace.finish(repeated.model_dump(mode='json'))
    assert hashes(first.directory) == first_hashes
    assert hashes(resumed_trace.directory) == resumed_hashes

    cli_root = root / 'cli'
    cli_root.mkdir()
    (cli_root / 'source.md').write_bytes(MARKDOWN.encode('utf-8'))
    write_json(cli_root / 'task.json', TASK)
    cli_backend = SyntheticBackend()
    registry = BackendRegistry()
    registry.register('synthetic', lambda **_: cli_backend)
    status = cli.main(['extract', '--input', str(cli_root / 'source.md'), '--task', str(cli_root / 'task.json'),
        '--base-url', 'https://synthetic.invalid/v1', '--model', 'synthetic', '--adapter', 'synthetic',
        '--output', str(cli_root / 'result.json'), '--report', str(cli_root / 'REPORT.md'),
        '--log-dir', str(cli_root / 'logs'), '--progress', 'quiet'], registry=registry)
    cli_result = json.loads((cli_root / 'result.json').read_text(encoding='utf-8'))
    assert status == 2 and cli_result['format_status'] == 'partial_format_error'
    assert len(cli_backend.calls) == 1
    return {'record_count': 3, 'prose_records': 1, 'table_records': 2, 'table_count': 2,
            'isolated_records': 1, 'initial_budget_calls': 1, 'resume_new_calls': resume_calls,
            'completed_resume_new_calls': 0, 'cli_exit_status': status, 'cli_synthetic_calls': 1,
            'synthetic_backend_generate_calls': len(backend.calls) + len(cli_backend.calls),
            'historical_artifacts_unchanged': True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    root = parser.parse_args().output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    network_attempts = []

    def blocked(*args, **kwargs):
        network_attempts.append(True)
        raise AssertionError('Network access is forbidden in unified-entry acceptance')

    environment = {key: value for key, value in os.environ.items() if not key.startswith('ATTESTEXTRACT_')}
    with patch.dict(os.environ, environment, clear=True), patch('socket.socket.connect', blocked), \
            patch('socket.socket.connect_ex', blocked), patch('socket.create_connection', blocked), \
            patch('socket.getaddrinfo', blocked):
        details = accept(root)
    assert not network_attempts
    summary = {'passed': True, 'package_version': attestextract.__version__,
               'module_path': str(Path(attestextract.__file__).resolve()),
               'network_attempts': 0, 'real_model_calls': 0, **details,
               'notice': 'Synthetic offline installed-package acceptance; no new API or scientific verification.'}
    write_json(root / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
