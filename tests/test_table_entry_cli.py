"""Official CLI and automatic routing, using only synthetic responses and mock HTTP."""

from copy import deepcopy
import json

import httpx
import pytest

from attestextract import cli, extract, RunTrace, ModelConnection
from attestextract.automatic_provenance import validate_automatic_provenance
from attestextract.errors import BackendError
from attestextract.models import BackendResponse
from attestextract.registry import BackendRegistry


TABLE = '<table><tr><th>Sample</th><th>Mass (g)</th></tr><tr><td>A</td><td>17</td></tr></table>'
SOURCE = 'Sample P has mass 5 g.\n\n' + TABLE + '\n'
TASK = {'name': 'Synthetic mass observations', 'record_types': {'mass': {
    'fields': {'object': {'type': 'string'}, 'value': {'type': 'string'}, 'unit': {'type': 'string'}},
    'identity_fields': ['object'],
    'measurement_groups': {'mass': {'value_field': 'value', 'unit_field': 'unit'}},
}}}
PROSE = {'records': [{'record_type': 'mass', 'values': {'object': 'P', 'value': '5', 'unit': 'g'},
                     'quotes': {'object': 'P', 'value': '5', 'unit': 'g'}}]}
TABLE_RECORD = {'record_id': 'table-observation', 'record_type': 'mass', 'fields': {
    'object': {'value': 'A', 'sources': [{'ref': 'table-0001-r0001-c0000', 'quote': 'A', 'offset': 0}], 'relation': 'row'},
    'value': {'value': '17', 'sources': [{'ref': 'table-0001-r0001-c0001', 'quote': '17', 'offset': 0}], 'relation': 'anchor'},
    'unit': {'value': 'g', 'sources': [{'ref': 'table-0001-r0000-c0001', 'quote': 'Mass (g)', 'offset': 0}], 'relation': 'column'},
}, 'anchors': [{'ref': 'table-0001-r0001-c0001', 'quote': '17', 'offset': 0}],
    'scope': 'individual', 'scope_sources': []}


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    for key in ('ATTESTEXTRACT_API_KEY', 'ATTESTEXTRACT_MODEL', 'ATTESTEXTRACT_BASE_URL'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('ATTESTEXTRACT_MODEL_CACHE_DIR', str(tmp_path / 'model-cache'))


def setup_cli(tmp_path, *, source=SOURCE, table_records=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source_path, task_path = tmp_path / 'paper.md', tmp_path / 'task.json'
    source_path.write_text(source, encoding='utf-8')
    task_path.write_text(json.dumps(TASK), encoding='utf-8')
    records = [deepcopy(TABLE_RECORD)] if table_records is None else table_records
    seen = []

    class Backend:
        name = 'synthetic-cli'
        output_protocol = 'flat-v1'
        mode = 'replay'

        def generate(self, request):
            seen.append(request)
            payload = json.loads(request.messages[-1]['content'])
            value = {'records': records} if 'table' in payload else PROSE
            return BackendResponse(text=json.dumps(value), model='synthetic-model')

    registry = BackendRegistry()
    registry.register('synthetic', lambda **_: Backend())
    args = ['extract', '--input', str(source_path), '--task', str(task_path),
            '--base-url', 'https://synthetic.invalid/v1', '--model', 'synthetic-model',
            '--adapter', 'synthetic', '--output', str(tmp_path / 'result.json'),
            '--report', str(tmp_path / 'report.md'), '--log-dir', str(tmp_path / 'diagnostics'),
            '--progress', 'quiet']
    return args, registry, seen


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_cli_accepts_paper_and_task_without_experiment_manifest(tmp_path):
    args, registry, calls = setup_cli(tmp_path)
    assert cli.main(args, registry=registry) == 0
    result = read(tmp_path / 'result.json')
    assert result['schema_version'] == '0.6' and result['format_status'] == 'complete'
    assert result['candidates'][0]['fields']['value']['value'] == '5'
    assert len(result['table_results']) == 1
    table = result['table_results'][0]
    assert table['candidates'][0]['fields'] == TABLE_RECORD['fields']
    assert table['candidates'][0]['scope_sources'] == []
    assert table['semantic_verification'] == 'not_run'
    assert [request.chunk_id for request in calls] == ['prose-0001', 'table-0001']
    inputs = [json.loads(request.messages[-1]['content']) for request in calls]
    assert '<table>' not in inputs[0]['source']
    assert inputs[1]['table']['table_source'] == TABLE
    # RunTrace may create its own diagnostic manifest; no experiment inputs are required.
    assert not (tmp_path / 'manifest.json').exists() and not list(tmp_path.rglob('cases.json'))
    report = (tmp_path / 'report.md').read_text(encoding='utf-8')
    assert 'table-0001' in report.replace('\\-', '-') and '17' in report
    assert (tmp_path / 'diagnostics/tables/table-0001/results.json').is_file()
    assert read(tmp_path / 'diagnostics/execution/checkpoint.json')['state']['calls_used'] == 2


def test_cli_check_has_one_atomic_table_and_no_model_calls(tmp_path, capsys):
    args, registry, calls = setup_cli(tmp_path)
    assert cli.main([*args, '--check', '--max-chunk-chars', '12'], registry=registry) == 0
    plan = json.loads(capsys.readouterr().out)
    assert calls == [] and plan['network_calls'] == 0
    assert plan['table_routing']['table_count'] == 1
    assert plan['table_routing']['table_chunks'] == ['table-0001']
    assert plan['coverage']['uncovered'] == []
    assert plan['coverage']['repeated_body_characters'] == 0
    assert not (tmp_path / 'result.json').exists()


def test_cli_isolates_one_bad_record_and_returns_partial_status(tmp_path, capsys):
    broken = deepcopy(TABLE_RECORD)
    broken['record_id'] = 'malformed'
    broken['fields']['unit'].pop('relation')
    args, registry, calls = setup_cli(tmp_path, table_records=[TABLE_RECORD, broken])
    assert cli.main(args, registry=registry) == 2
    result = read(tmp_path / 'result.json')
    assert result['format_status'] == 'partial_format_error'
    table = result['table_results'][0]
    assert len(table['candidates']) == 1 and table['candidates'][0]['fields'] == TABLE_RECORD['fields']
    assert table['rejections'][0]['index'] == 1 and table['rejections'][0]['raw'] == broken
    assert 'partial_format_error' in capsys.readouterr().err
    assert len(calls) == 2 and (tmp_path / 'report.md').exists()


def test_cli_failed_grid_check_is_explicit_and_offline(tmp_path, capsys):
    broken = '<table><tr><td colspan="0">Broken</td></tr></table>'
    args, registry, calls = setup_cli(tmp_path, source=broken)
    assert cli.main([*args, '--check'], registry=registry) == 1
    assert calls == [] and not (tmp_path / 'result.json').exists()
    assert 'no valid cell grid' in capsys.readouterr().err


def test_cli_resume_preview_reuses_both_protocols_without_calls(tmp_path, capsys):
    args, registry, calls = setup_cli(tmp_path / 'first')
    assert cli.main(args, registry=registry) == 0
    capsys.readouterr()
    preview = [*args, '--check', '--resume', str(tmp_path / 'first/diagnostics'),
               '--output', str(tmp_path / 'preview.json'), '--report', str(tmp_path / 'preview.md'),
               '--log-dir', str(tmp_path / 'preview-diagnostics')]
    assert cli.main(preview, registry=registry) == 0
    plan = json.loads(capsys.readouterr().out)
    assert len(calls) == 2
    assert plan['resume']['reusable_chunks'] == 2 and plan['resume']['remaining_chunks'] == 0
    assert plan['table_routing']['table_count'] == 1


def test_cli_text_compatibility_mode_is_explicit(tmp_path):
    args, registry, calls = setup_cli(tmp_path)
    assert cli.main([*args, '--tables', 'text'], registry=registry) == 0
    result = read(tmp_path / 'result.json')
    assert result['schema_version'] == '0.4' and not result.get('table_results')
    assert all('source' in json.loads(request.messages[-1]['content']) for request in calls)


def install_http(monkeypatch, *, table_content=None):
    original = httpx.Client
    posts = []

    def dispatch(request):
        if request.method == 'GET':
            return httpx.Response(401)
        posts.append(request)
        payload = json.loads(json.loads(request.content)['messages'][-1]['content'])
        content = json.dumps({'records': [TABLE_RECORD]}) if 'table' in payload else json.dumps(PROSE)
        if 'table' in payload and table_content is not None:
            content = table_content
        return httpx.Response(200, json={'model': 'reported-synthetic-model', 'id': 'synthetic-response',
            'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}})

    def client(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(dispatch)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, 'Client', client)
    return posts


def test_automatic_mixed_result_has_per_chunk_provenance_and_resume(monkeypatch, tmp_path):
    posts = install_http(monkeypatch)
    kwargs = {'base_url': 'https://unregistered.invalid/v1', 'model': 'synthetic-model', 'api_key': 'synthetic-key'}
    first = RunTrace(tmp_path / 'first')
    result = extract(SOURCE, TASK, trace=first, **kwargs)
    assert result.schema_version == '0.6' and result.run.model is None
    assert len(posts) == 2 and len(result.run.generation_attempts) == 2
    assert set(result.run.chunk_provenance) == {'prose-0001', 'table-0001'}
    assert result.table_results[0].candidates[0]['fields'] == TABLE_RECORD['fields']
    validate_automatic_provenance(result)
    first.finish(result.model_dump(mode='json'))
    resumed = extract(SOURCE, TASK, trace=RunTrace(tmp_path / 'second'), resume_from=first.directory, **kwargs)
    assert len(posts) == 2
    assert resumed.table_results == result.table_results
    validate_automatic_provenance(resumed)


@pytest.mark.parametrize('content', ['{broken', ''])
def test_automatic_invalid_table_output_has_no_hidden_format_retry(monkeypatch, tmp_path, content):
    posts = install_http(monkeypatch, table_content=content)
    trace = RunTrace(tmp_path / 'trace')
    with pytest.raises(BackendError):
        extract(TABLE, TASK, base_url='https://unregistered.invalid/v1', model='synthetic-model',
                api_key='synthetic-key', trace=trace, format_retries=1,
                fallback=ModelConnection(base_url='https://backup.invalid/v1', model='backup', api_key='synthetic-key'))
    assert len(posts) == 1
    state = read(tmp_path / 'trace/execution/checkpoint.json')['state']
    assert state['calls_used'] == 1
    assert state['generation_attempts'][0]['status'] == 'failed'
