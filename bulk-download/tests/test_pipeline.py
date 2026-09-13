import unittest
import uuid
from unittest.mock import Mock

from bulk_download.pipeline import discover_luluvid
from bulk_download.resolvers import anchor_links
from bulk_download.urls import UnsafeUrl, luluvid_fetch_url


class LuluvidAliasTests(unittest.TestCase):
    def test_wrapped_alias_keeps_original_attribution(self):
        html = '<a href="/redirect/page/https%3A%2F%2Fluluvdoo.com%2Fl29123c02ayy">Video</a>'
        self.assertEqual(
            anchor_links(html, "https://justpaste.it/page", "luluvid"),
            ["https://luluvdoo.com/l29123c02ayy"],
        )

    def test_public_file_route_does_not_change_manifest_identity(self):
        original = "https://luluvdoo.com/l29123c02ayy"
        public_page = "https://luluvid.com/l29123c02ayy"
        fetcher = Mock()
        fetcher.html.return_value = (
            '<video src="https://media.example/video.mp4"></video>', public_page
        )
        found, failed = discover_luluvid(fetcher, original)
        fetcher.html.assert_called_once_with(public_page)
        self.assertFalse(failed)
        self.assertEqual(found[0].luluvid_url, original)
        self.assertEqual(found[0].uid, str(uuid.uuid5(uuid.NAMESPACE_URL, original)))

    def test_existing_identity_and_host_guards_are_unchanged(self):
        original = "https://luluvid.com/l29123c02ayy"
        self.assertEqual(luluvid_fetch_url(original), original)
        for url in [
            "https://luluvdoo.com.evil.example/file",
            "https://user:password@luluvdoo.com/file",
            "http://luluvdoo.com/file",
        ]:
            with self.assertRaises(UnsafeUrl):
                luluvid_fetch_url(url)


if __name__ == "__main__":
    unittest.main()

class PlayerRefererTests(unittest.TestCase):
    def test_matching_embed_origin_is_used(self):
        from bulk_download.resolvers import player_referer
        self.assertEqual(player_referer('<iframe src="https://luluvdo.com/e/abc"></iframe>', 'https://luluvid.com/abc'), 'https://luluvdo.com/')

    def test_unrelated_frames_do_not_change_referrer(self):
        from bulk_download.resolvers import player_referer
        page = 'https://luluvid.com/abc'
        for embed in ('https://ads.example/e/abc', 'https://luluvdo.com/e/other', 'http://luluvdo.com/e/abc'):
            self.assertEqual(player_referer(f'<iframe src="{embed}"></iframe>', page), page)
