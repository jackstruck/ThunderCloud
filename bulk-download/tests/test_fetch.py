import unittest
from bulk_download.config import HttpConfig
from bulk_download.fetch import Fetcher

class ReferrerTests(unittest.TestCase):
    def test_media_chain_preserves_range_headers_and_restores_context(self):
        with Fetcher(HttpConfig('test', 1, 1, 3, 1, 0, 1024, 1024)) as fetcher:
            with self.assertRaises(RuntimeError):
                with fetcher.referring_to('https://luluvid.com/example'):
                    request=fetcher.client.build_request('GET','https://cdn.example/segment',headers={'Range':'bytes=0-99'})
                    self.assertEqual(request.headers['Referer'],'https://luluvid.com/example')
                    self.assertEqual(request.headers['Range'],'bytes=0-99')
                    raise RuntimeError('interrupted')
            self.assertNotIn('Referer',fetcher.client.headers)
