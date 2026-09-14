import base64
import hashlib
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import google_crc32c
from bulk_download.cli import main, run
from bulk_download.download import (
    Downloaded,
    _download_hls,
    _media_playlist,
    _validate_hls,
)
from bulk_download.hotscope import discover_users, resolve_video, user_name, video_id
from bulk_download.manifest import append, completed_urls
from bulk_download.storage import StorageAdapter


def video(ident='one', username='alice'):
    return {'id': ident, 'uploader': {'username': username},
            'playlist': f'https://cdn.hotscope.tv/videos/{ident}/playlist.m3u8'}


def html(objects):
    frame = json.dumps([1, '1:' + json.dumps(objects) + '\n'])
    return f'<script>self.__next_f.push({frame})</script>'


class HotscopeTests(unittest.TestCase):
    def test_user_and_video_input_guards(self):
        for name in ['alice', '@alice', 'https://hotscope.tv/user/alice/']:
            self.assertEqual(user_name(name), 'alice')
        self.assertEqual(video_id('https://hotscope.tv/video/one'), 'one')
        for value in ['https://evil.test/user/alice', '../alice', 'https://hotscope.tv/user/../alice',
                      'https://user:pass@hotscope.tv/user/alice', 'http://hotscope.tv/user/alice']:
            with self.assertRaises(ValueError):
                user_name(value)

    def test_resolver_ignores_related_video_and_preview(self):
        fetcher = Mock()
        fetcher.html.return_value = (html([video('related', 'bob'), video()]), 'https://hotscope.tv/video/one')
        item = resolve_video(fetcher, 'one', 'alice')
        self.assertEqual(item.media_url, video()['playlist'])
        self.assertEqual(item.provider_id, 'one')
        fetcher.html.return_value = (html([{'id': 'one', 'preview': 'preview.mp4'}]), item.page_url)
        with self.assertRaisesRegex(RuntimeError, 'full playlist'):
            resolve_video(fetcher, 'one', 'alice')

    def test_owner_and_playlist_identity_must_match(self):
        for data in [video(username='bob'), {**video(), 'playlist': video('other')['playlist']},
                     {**video(), 'playlist': 'https://evil.test/videos/one/playlist.m3u8'}]:
            fetcher = Mock()
            fetcher.html.return_value = (html([data]), 'https://hotscope.tv/video/one')
            with self.assertRaises(RuntimeError):
                resolve_video(fetcher, 'one', 'alice')

    @patch('bulk_download.hotscope.resolve_video')
    @patch('bulk_download.hotscope._body')
    @patch('bulk_download.hotscope._action', return_value='action')
    def test_paginated_selection_resume_and_missing_ids(self, action, body, resolve):
        fetcher = Mock()
        fetcher.restricted_to.return_value = nullcontext()
        fetcher.html.return_value = ('profile', 'https://hotscope.tv/user/alice')
        body.side_effect = ['1:' + json.dumps({'data': [video()], 'meta': {'next': 2}}),
                            '1:' + json.dumps({'data': [video('two')], 'meta': {'next': None}})]
        found, failures = discover_users(fetcher, ['alice'], {'two', 'missing'}, None, 5,
                                        {'https://hotscope.tv/video/two'})
        self.assertFalse(found)
        resolve.assert_not_called()
        self.assertEqual([f.code for f in failures], ['selection_not_found'])
        self.assertEqual(json.loads(body.call_args.kwargs['content']), ['alice', 2, False])

    @patch('bulk_download.hotscope._body')
    @patch('bulk_download.hotscope._action', return_value='action')
    def test_one_profile_failure_does_not_stop_others(self, action, body):
        fetcher = Mock()
        fetcher.restricted_to.return_value = nullcontext()
        fetcher.html.side_effect = [('p', 'https://hotscope.tv/user/alice'), ('p', 'https://hotscope.tv/user/bob')]
        body.side_effect = ['changed format', '1:' + json.dumps({'data': [], 'meta': {'next': None}})]
        _, failures = discover_users(fetcher, ['alice', 'bob'], set(), None, 5)
        self.assertEqual(len(failures), 1)
        self.assertEqual(fetcher.html.call_count, 2)

    def test_manifest_reads_legacy_and_new_completed_records(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'manifest'
            append(path, {'status': 'complete', 'luluvid_url': 'legacy'})
            append(path, {'status': 'duplicate', 'page_url': 'new'})
            append(path, {'status': 'failed', 'page_url': 'failed'})
            self.assertEqual(completed_urls(path), {'legacy', 'new'})

    @patch('bulk_download.cli.load_config')
    def test_empty_users_file_fails_before_storage(self, config):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'users'
            path.write_text('# no users\n')
            with patch('bulk_download.cli.StorageAdapter') as storage:
                self.assertEqual(main(['run', '--users-file', str(path)]), 2)
                storage.assert_not_called()

    def test_download_upload_and_resume_without_legacy_files(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            config = SimpleNamespace(temp_dir=root, manifest_file=root / 'manifest', http=Mock(),
                                     gcp=SimpleNamespace(prefix='videos', bucket='bucket'))
            fetcher = Mock()
            fetcher.html.return_value = (html([video()]), 'https://hotscope.tv/video/one')
            item = resolve_video(fetcher, 'one', 'alice')
            media = root / 'media'
            media.write_bytes(b'content')
            downloaded = Downloaded(media, 'video/mp4', '.mp4', 7, hashlib.sha256(b'content').hexdigest())
            with patch('bulk_download.cli.load_csek', return_value=b'k'*32), \
                 patch('bulk_download.cli.StorageAdapter') as storage, \
                 patch('bulk_download.cli.Fetcher'), \
                 patch('bulk_download.cli._discover_all', return_value=([item], [])), \
                 patch('bulk_download.cli.download_video', return_value=downloaded) as download:
                storage.return_value.existing.return_value = None
                storage.return_value.upload.return_value.generation = 123
                self.assertEqual(run(config), 0)
                self.assertFalse(media.exists())
                record = json.loads(config.manifest_file.read_text())
                self.assertEqual(record['provider'], 'hotscope')
                self.assertEqual(record['generation'], 123)
                self.assertNotIn('media_url', record)
                self.assertNotIn('luluvid_url', record)
                self.assertEqual(run(config), 0)
                download.assert_called_once()


class StorageTests(unittest.TestCase):
    def test_existing_content_and_encryption_must_match(self):
        adapter = StorageAdapter.__new__(StorageAdapter)
        adapter.csek = b'k' * 32
        adapter.config = SimpleNamespace(chunk_bytes=4)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'media'
            path.write_bytes(b'media')
            properties = {'customerEncryption': {'encryptionAlgorithm': 'AES256',
                'keySha256': base64.b64encode(hashlib.sha256(adapter.csek).digest()).decode()}}
            blob = SimpleNamespace(size=5, content_type='video/mp4',
                crc32c=base64.b64encode(google_crc32c.Checksum(b'media').digest()).decode(), _properties=properties)
            adapter.verify(blob, path, 'video/mp4', 5)
            blob.crc32c = 'wrong'
            with self.assertRaisesRegex(RuntimeError, 'verification_failure'):
                adapter.verify(blob, path, 'video/mp4', 5)
            blob.crc32c = base64.b64encode(google_crc32c.Checksum(b'media').digest()).decode()
            blob._properties = {}
            with self.assertRaisesRegex(RuntimeError, 'verification_failure'):
                adapter.verify(blob, path, 'video/mp4', 5)


class HlsTests(unittest.TestCase):
    @patch('bulk_download.download._read_playlist')
    def test_highest_bandwidth_and_final_url_relative_resolution(self, read):
        read.side_effect = [(['#EXTM3U', '#EXT-X-STREAM-INF:BANDWIDTH=10', 'low.m3u8',
                             '#EXT-X-STREAM-INF:BANDWIDTH=20', 'high.m3u8'], 'https://cdn.hotscope.tv/final/master'),
                            (['media'], 'final')]
        _media_playlist(Mock(), Mock(), 'original')
        self.assertEqual(read.call_args.args[2], 'https://cdn.hotscope.tv/final/high.m3u8')

    @patch('bulk_download.download._media_playlist')
    def test_live_and_unsupported_playlists_fail_before_transfer(self, playlist):
        with tempfile.TemporaryDirectory() as root:
            config = SimpleNamespace(temp_dir=Path(root))
            for lines in [['#EXTM3U', '#EXTINF:1,', 'segment'],
                          ['#EXTM3U', '#EXT-X-MAP:URI="init"', '#EXT-X-ENDLIST']]:
                playlist.return_value = (lines, 'https://cdn.hotscope.tv/p.m3u8')
                with self.assertRaisesRegex(RuntimeError, 'unsupported_video_type'):
                    _download_hls(Mock(), config, 'url', 'id')
                self.assertFalse(list(Path(root).iterdir()))

    @patch('bulk_download.download.subprocess.run')
    def test_truncated_playable_file_is_rejected(self, process):
        process.return_value = SimpleNamespace(returncode=0, stdout=b'out_time_us=1000000\n')
        with self.assertRaisesRegex(RuntimeError, 'verification_failure'):
            _validate_hls(Path('media'), 30)

class GeneratedMediaTests(unittest.TestCase):
    def test_full_hls_transfer_remux_decode_and_size_cap(self):
        import subprocess

        import httpx
        from bulk_download.download import download_video, ffmpeg_executable
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            subprocess.run([
                ffmpeg_executable(), '-hide_banner', '-loglevel', 'error',
                '-f', 'lavfi', '-i', 'testsrc=size=64x64:rate=10',
                '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '3',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                '-f', 'hls', '-hls_time', '1', '-hls_list_size', '0', str(root / 'test.m3u8'),
            ], check=True, capture_output=True, timeout=30)
            fetcher = Mock()
            fetcher.request.side_effect = lambda url, **kw: httpx.Response(
                200, content=(root / url.rsplit('/', 1)[-1]).read_bytes(), request=httpx.Request('GET', url))
            config = SimpleNamespace(temp_dir=root/'tmp', chunk_bytes=4096,
                http=SimpleNamespace(max_video_bytes=1_000_000, max_html_bytes=10000, attempts=2))
            result = download_video(fetcher, config, 'https://cdn.hotscope.tv/test.m3u8', 'generated')
            self.assertGreater(result.size, 0)
            self.assertEqual(list(config.temp_dir.iterdir()), [result.path])
            result.path.unlink()
            config.http.max_video_bytes = 10
            with self.assertRaisesRegex(RuntimeError, 'video_too_large'):
                download_video(fetcher, config, 'https://cdn.hotscope.tv/test.m3u8', 'limited')
            self.assertFalse(list(config.temp_dir.iterdir()))
