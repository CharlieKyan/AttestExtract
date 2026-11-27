"""Synthetic ownership, atomic-table and shared-ledger checks for the public entry."""

import hashlib

import pytest

from niceextract.automatic import ModelConnection, build_automatic_backend
from niceextract.automatic_execution import prepare_automatic_execution
from niceextract.contextual_chunking import coverage_report
from niceextract.diagnostics import RunTrace
from niceextract.document_routing import is_table_chunk, routing_structure, table_input_for_chunk
from niceextract.errors import ConfigurationError
from niceextract.execution import prepare_execution
from niceextract.models import BackendResponse, ContextOptions, ExecutionOptions, GenerationOptions, TaskSpec
from niceextract.planning import plan_chunks, split_chunk
from niceextract.structure import build_structure
from niceextract.table_routes import build_table_input


TASK = TaskSpec.model_validate({"name": "Synthetic observations", "record_types": {"reading": {
    "fields": {"object": {"type": "string"}, "value": {"type": "string"}, "unit": {"type": "string"}},
    "identity_fields": ["object"], "measurement_groups": {"reading": {"value_field": "value", "unit_field": "unit"}},
}}})
HTML = '<table><tr><th>Sample</th><th>Mass (g)</th></tr><tr><td>A</td><td>17</td></tr></table>'
MARKDOWN = '| Sample | Mass (g) |\n| --- | --- |\n| B | 23 |\n'
TEXT = '# Results\n\nProse value 5 g.\n\n' + HTML + '\n\nOther prose 9 g.\n\n' + MARKDOWN + '\nFinal prose 8 g.\n'


class NeverCalled:
    name = 'synthetic'
    output_protocol = 'flat-v1'

    def generate(self, request):
        raise AssertionError('Planning must not call a model')


def plan(text=TEXT, **kwargs):
    return plan_chunks(text, TASK, GenerationOptions(max_output_tokens=100),
                       ExecutionOptions(min_chunk_chars=1), max_chars=28, table_mode='auto', **kwargs)


@pytest.mark.parametrize('mode', ['none', 'structure', 'neighbors'])
def test_complete_tables_are_separate_from_prose_and_cover_original_text(mode):
    structure = build_structure(TEXT)
    chunks = plan(context=ContextOptions(mode=mode, neighbor_chars=20), structure=structure)
    tables = [chunk for chunk in chunks if is_table_chunk(chunk)]
    assert [chunk.chunk_id for chunk in tables] == ['table-0001', 'table-0002']
    assert [table_input_for_chunk(TEXT, chunk, structure)['table_source'] for chunk in tables] == [HTML, MARKDOWN]
    report = coverage_report(TEXT, chunks, max_chars=28)
    assert report['uncovered'] == [] and report['repeated_body_characters'] == 0
    assert report['covered_characters'] == len(TEXT)
    for chunk in chunks:
        assert chunk.text == ''.join(TEXT[span.start:span.end] for span in chunk.segments)
        if is_table_chunk(chunk):
            assert split_chunk(TEXT, chunk, min_chars=1, structure=structure) == []
        else:
            assert table_input_for_chunk(TEXT, chunk, structure) is None
            assert not any(left.start < table.span.end and table.span.start < left.end
                           for left in chunk.segments for table in structure.tables)


@pytest.mark.parametrize('text', [HTML, '\n\n' + HTML + '\n\n', HTML + '\n\n' + MARKDOWN])
def test_whitespace_does_not_create_model_calls(text):
    chunks = plan(text)
    assert len(chunks) == len(build_structure(text).tables)
    assert all(is_table_chunk(chunk) for chunk in chunks)
    assert coverage_report(text, chunks)['uncovered'] == []
    for chunk in chunks:
        assert table_input_for_chunk(text, chunk)['table_source'] in (HTML, MARKDOWN)


def test_observation_table_and_retry_windows_can_cite_adjacent_intro_without_owning_it():
    from niceextract.contextual_chunking import extraction_context
    from niceextract.pipeline import configure_observation_task, resolve_task
    from niceextract.table_windows import split_table_chunk

    task = configure_observation_task(resolve_task(None, prompt_description="List masses.",
                                                  field_list={"mass": "Reported mass."}, examples=[]))
    policy = extraction_context(task)
    assert policy == ContextOptions(mode="neighbors", neighbor_chars=500)
    source = "# Results\n\nThe material came from unit A.\n" + HTML + "\nThese were dry weights."
    structure = build_structure(source)
    chunks = plan_chunks(source, task, GenerationOptions(), ExecutionOptions(), max_chars=6000,
                         context=policy, table_mode="auto", structure=structure)
    parent = next(chunk for chunk in chunks if is_table_chunk(chunk))
    for chunk in [parent, *split_table_chunk(source, parent, structure=structure)]:
        table = table_input_for_chunk(source, chunk, structure)
        prose = {item["kind"]: item for item in table["entries"] if item["kind"].startswith("neighbor_")}
        assert {role: item["quote"].strip() for role, item in prose.items()} == {
            "neighbor_before": "The material came from unit A.", "neighbor_after": "These were dry weights."}
        for item in prose.values():
            assert item["quote"] == source[item["start"]:item["end"]]
            assert all(item["end"] <= span.start or item["start"] >= span.end for span in chunk.body_segments)
            assert item["ref"] not in table.get("window", {}).get("owned_cell_refs", [])
    assert extraction_context(task, mode="none").mode == "none"


def test_mineru_heading_and_metadata_survive_table_input():
    source = 'MinerU heading\n\nCaption X\n' + HTML + '\nFootnote X\n'
    mineru = [{'type': 'text', 'text': 'MinerU heading', 'text_level': 2},
              {'type': 'table', 'table_body': HTML, 'table_caption': ['Caption X'],
               'table_footnote': ['Footnote X'], 'page_idx': 2}]
    structure = build_structure(source, mineru_content=mineru)
    chunk = next(chunk for chunk in plan(source, structure=structure) if is_table_chunk(chunk))
    table = table_input_for_chunk(source, chunk, structure)
    assert [(entry['kind'], entry['quote']) for entry in table['entries'] if entry['kind'] != 'cell'] == [
        ('heading', 'MinerU heading'), ('caption', 'Caption X'), ('footnote', 'Footnote X')]
    assert table['source_sha256'] == hashlib.sha256(source.encode()).hexdigest()
    for entry in table['entries']:
        assert source[entry['start']:entry['end']] == entry['quote']
    with pytest.raises(ValueError, match='either structure'):
        build_table_input(source, structure=structure, mineru_content=mineru)
    with pytest.raises(ValueError, match='exact text'):
        build_table_input(source + 'changed', structure=structure)


def test_invalid_grid_is_a_planning_error_before_any_request():
    source = '<table><tr><td colspan="0">Broken</td></tr></table>'
    with pytest.raises(ConfigurationError, match='no valid cell grid'):
        plan(source)
    with pytest.raises(ConfigurationError, match='no valid cell grid'):
        prepare_execution(source, TASK, NeverCalled(), GenerationOptions(), ExecutionOptions(), table_mode='auto')


def test_invalid_table_mode_is_not_silently_treated_as_text():
    with pytest.raises(ConfigurationError, match='table_mode'):
        routing_structure(TEXT, 'guess')


def test_table_free_auto_plan_and_fingerprint_preserve_text_compatibility():
    source = 'Sample A reads 5 g.\n\nSample B reads 7 g.'
    kwargs = dict(text=source, task=TASK, backend=NeverCalled(), generation=GenerationOptions(),
                  options=ExecutionOptions(), max_chars=25)
    old = prepare_execution(**kwargs)
    new = prepare_execution(**kwargs, table_mode='auto')
    assert new.chunks == old.chunks
    assert new.state.fingerprint == old.state.fingerprint


def test_table_and_prose_share_budget_and_completed_table_cache(tmp_path):
    kwargs = dict(text=TEXT, task=TASK, backend=NeverCalled(), generation=GenerationOptions(),
                  options=ExecutionOptions(max_model_calls=3), backend_identity='synthetic-v1', table_mode='auto')
    previous = tmp_path / 'previous'
    run = prepare_execution(**kwargs, trace=RunTrace(previous))
    run.reserve('table-0001')
    response = BackendResponse(text='{"records":[]}', model='synthetic')
    run.observe(response)
    run.complete('table-0001', response)
    run.reserve('prose-0001')
    run.observe(response)
    run.complete('prose-0001', response)
    assert run.state.calls_used == 2
    old_bytes = {p.relative_to(previous): p.read_bytes() for p in previous.rglob('*') if p.is_file()}
    resumed = prepare_execution(**kwargs, trace=RunTrace(tmp_path / 'resumed'), resume_from=previous)
    assert set(resumed.cached) == {'table-0001', 'prose-0001'}
    assert resumed.state.calls_used == 2 and resumed.state.max_model_calls == 3
    assert old_bytes == {p.relative_to(previous): p.read_bytes() for p in previous.rglob('*') if p.is_file()}
    with pytest.raises(ConfigurationError, match='identity mismatch'):
        prepare_execution(**{**kwargs, 'table_mode': 'text'}, trace=RunTrace(tmp_path / 'wrong'), resume_from=previous)


def test_automatic_execution_uses_the_same_routed_plan(tmp_path):
    backend = build_automatic_backend(ModelConnection(base_url='https://synthetic.invalid/v1',
        model='synthetic-model', api_key='synthetic-key'), use_cache=False, cache_dir=tmp_path / 'cache')
    run = prepare_automatic_execution(TEXT, TASK, backend, GenerationOptions(), ExecutionOptions(), table_mode='auto')
    assert {'table-0001', 'table-0002', 'prose-0001'} <= set(run.state.roots)
    assert run.subdivide('table-0001') == []


def test_table_lookup_rejects_partial_body():
    chunk = next(chunk for chunk in plan() if is_table_chunk(chunk))
    truncated = chunk.model_copy(update={'body_segments': [chunk.body_segments[0].model_copy(update={
        'end': chunk.body_segments[0].start + 1})]})
    with pytest.raises(ConfigurationError, match='complete original table'):
        table_input_for_chunk(TEXT, truncated)
