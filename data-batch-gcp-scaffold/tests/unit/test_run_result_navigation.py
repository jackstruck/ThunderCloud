from unittest.mock import Mock

from worker.run_repository import RunRepository


def test_result_navigation_uses_survivor_without_changing_historical_rank_or_score():
    database = Mock()
    cursor = database.connect.return_value.cursor.return_value
    cursor.fetchall.side_effect = [
        [('group', 'old-subject', 9, .36694753, 'Original name', 1, 2,
          'preview', .8, 1000, ['https://media.example/video'], 2, 'changed', 'survivor')],
        [('survivor', 'source', 'https://media.example/video', ['https://media.example/video'])],
    ]
    cursor.fetchone.side_effect = [('source','https://media.example/video'), None, None]
    repo = RunRepository(database)
    repo.get = Mock(return_value={'state':'succeeded','handling_policy':'retain_and_enroll','outcome':'candidates'})
    candidate = repo.results('run')['groups'][0]['candidates'][0]
    assert candidate['subject_id'] == 'old-subject'
    assert candidate['resolved_subject_id'] == 'survivor'
    assert candidate['rank'] == 9
    assert candidate['similarity'] == .36694753
    assert candidate['display_name'] == 'Original name'
    assert candidate['representative_faces'][0]['representative_id'] == 'preview'
    assert candidate['sources'] == [{'source_id':'source','page_url':'https://media.example/video','page_urls':['https://media.example/video']}]


def test_run_summary_links_only_to_library_sources():
    from worker.run_repository import serialize_run
    base = ['run', 'succeeded', 'retain_and_enroll', 'no_faces', False,
            None, None, 'created', 'updated', 'expires']
    result = serialize_run(base + ['source', 'https://example.com/video'])
    assert result['source_url'] == '/sources/source'
    assert result['source']['source_id'] == 'source'
    result = serialize_run(base + [None, 'https://example.com/video'])
    assert result['source_url'] is None
    assert serialize_run(base + [None, None])['source_url'] is None
