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

class ProviderHostTests(unittest.TestCase):
    def test_redirect_cannot_escape_provider_allowlist(self):
        import httpx
        from unittest.mock import patch
        from bulk_download.urls import UnsafeUrl
        with Fetcher(HttpConfig('test', 1, 1, 3, 1, 0, 1024, 1024)) as fetcher:
            fetcher.client.close()
            fetcher.client = httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(302, headers={'Location': 'https://other.example/segment'})
            ))
            with patch('bulk_download.fetch.ensure_public_host'), fetcher.restricted_to({'cdn.hotscope.tv'}):
                with self.assertRaises(UnsafeUrl):
                    fetcher.request('https://cdn.hotscope.tv/segment')
            self.assertIsNone(fetcher.allowed_hosts)
