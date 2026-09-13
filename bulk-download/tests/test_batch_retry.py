import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('batch', Path(__file__).parents[1] / 'scripts/run_luluvid_batch.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)

class RetryTests(unittest.TestCase):
    def test_filtered_retry_checkpoints_success_then_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'manifest.jsonl'
            urls = ['https://luluvid.com/a', 'https://luluvid.com/b', 'https://luluvid.com/c']
            manifest.write_text(''.join(json.dumps({'luluvid_url': u, 'status': 'failed', 'error_code': code}) + '\n' for u, code in zip(urls, ['HTTP 403', 'HTTP 403', 'HTTP 500'])))
            config = NS(manifest_file=manifest, luluvid_file=root/'inputs', http=Mock(), gcp=NS(prefix='videos', bucket='bucket'))
            args = NS(config='unused', partition='all', reverse=False, error_code=['HTTP 403'], limit=None, delay=0)
            storage = Mock()
            storage.bucket.soft_delete_policy.retention_duration_seconds = 0
            storage.existing.return_value = None
            storage.upload.return_value.generation = 1
            media = root/'media.mp4'
            media.write_bytes(b'video')
            downloaded = NS(path=media, sha256='abc', extension='.mp4', content_type='video/mp4', size=5)
            snapshots = []
            discover = Mock(side_effect=[([NS(uid='a', luluvid_url=urls[0], media_url='media', media_referer=None)], []), RuntimeError('HTTP 403')])
            with patch.multiple(batch, arguments=Mock(return_value=args), load_config=Mock(return_value=config), load_csek=Mock(), StorageAdapter=Mock(return_value=storage), load_inputs=Mock(return_value=urls), discover_luluvid=discover, download_video=Mock(return_value=downloaded)), patch.object(batch, 'Fetcher'):
                code = batch.main(checkpoint=lambda: snapshots.append(manifest.read_text()))
            self.assertEqual(code, 1)
            self.assertEqual(discover.call_count, 2)
            self.assertEqual(len(snapshots), 2)
            self.assertEqual(json.loads(snapshots[0].splitlines()[-1])['status'], 'complete')
            self.assertEqual(json.loads(snapshots[1].splitlines()[-1])['error_code'], 'HTTP 403')
