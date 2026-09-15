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
