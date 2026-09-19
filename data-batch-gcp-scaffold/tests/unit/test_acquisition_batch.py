import pytest

pytest.importorskip("bulk_download", reason="cloud acquisition image includes the optional bulk downloader")

from worker.acquisition_batch import acquired_sources, identity, source_payload


def row(ident='one'):
    return {'provider':'hotscope', 'provider_id':ident, 'status':'complete',
        'object':f'gs://archive/videos/{ident}.mp4','generation':123,
        'sha256':'a'*64,'bytes':100,'content_type':'video/mp4',
        'page_url':f'https://hotscope.tv/video/{ident}'}


def test_selected_sources_recover_duplicates_without_replaying_unselected_sources():
    source=row()
    duplicate={'provider':'hotscope','provider_id':'two','status':'duplicate',
        'duplicate_of':source['object'],'page_url':'https://hotscope.tv/video/two'}
    found, failed=acquired_sources([source,duplicate,row('unselected'),
        {'provider':'hotscope','provider_id':'two','status':'failed'},
        {'provider':'hotscope','provider_id':'missing','status':'failed'}], {'two','missing'})
    assert set(found)=={'two'}
    assert found['two']['object']==source['object']
    assert found['two']['page_url']==duplicate['page_url']
    assert failed=={'missing'}


def test_payload_preserves_generation_attribution_and_explicit_policy():
    source=row()
    payload=source_payload(source,'retain_and_enroll')
    assert payload['selection_policy']=='all_tracks'
    assert payload['handling_policy']=='retain_and_enroll'
    assert payload['source']['generation']==123
    assert payload['source']['page_url']==source['page_url']
    assert identity(source)=='gs://archive/videos/one.mp4#123'


def test_duplicate_archive_aliases_are_recorded_before_handoff():
    from unittest.mock import Mock

    from worker.acquisition_batch import record_source_urls
    source=row()
    alias={**source,'page_url':'https://hotscope.tv/video/alias'}
    database=Mock()
    record_source_urls(database,[source,alias,alias])
    args=database.connect.return_value.cursor.return_value.executemany.call_args.args
    assert args[1]==[('a'*64,'https://hotscope.tv/video/alias'),('a'*64,'https://hotscope.tv/video/one')]
    database.connect.return_value.commit.assert_called_once()


def test_profile_discovery_selects_every_video_and_rejects_incomplete_pagination():
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import Mock

    from bulk_download.pipeline import DiscoveryFailure

    from worker.acquisition_batch import discover_profile
    state = {'users':['Mary']}
    discover = Mock(return_value=([SimpleNamespace(provider_id=str(i)) for i in range(250)], []))
    assert len(discover_profile(state, None, fetcher_factory=lambda _: nullcontext('fetcher'), discover=discover)) == 250
    discover.assert_called_once_with('fetcher', ['Mary'], set(), None, 1000)
    assert state['discovery_complete']
    state = {'users':['Mary']}
    discover.return_value = ([SimpleNamespace(provider_id='one')], [DiscoveryFailure('pagination_limit','incomplete',{})])
    with pytest.raises(ValueError):
        discover_profile(state, None, fetcher_factory=lambda _: nullcontext('fetcher'), discover=discover)
    assert not state.get('discovery_complete')
    assert state['discovery_failures'] == ['pagination_limit']


def test_policy_change_uses_new_handoff_keys():
    from worker.acquisition_batch import handoff_key
    old=handoff_key('batch','search_then_discard','object#42')
    new=handoff_key('batch','retain_and_enroll','object#42')
    assert new!=old and new==handoff_key('batch','retain_and_enroll','object#42')


def test_manifest_progress_tails_new_rows_and_reconstructs_on_restart(tmp_path):
    import json

    from worker.acquisition_batch import ManifestProgress
    path=tmp_path/'manifest.jsonl'
    source=row()
    duplicate={'provider':'hotscope','provider_id':'two','status':'duplicate',
        'duplicate_of':source['object'],'page_url':'https://hotscope.tv/video/two'}
    path.write_text(json.dumps(duplicate)+'\n')
    progress=ManifestProgress(path)
    assert progress.refresh({'one','two'})==({},set())
    with path.open('a') as handle:handle.write(json.dumps(source)+'\n')
    expected=acquired_sources([duplicate,source],{'one','two'})
    assert progress.refresh({'one','two'})==expected
    assert progress.refresh({'one','two'})==expected
    assert progress.text==path.read_text()
    assert ManifestProgress(path).refresh({'one','two'})==expected
    from bulk_download.manifest import completed_hashes, completed_urls
    assert progress.completed_urls == completed_urls(path)
    assert progress.completed_hashes == completed_hashes(path)
