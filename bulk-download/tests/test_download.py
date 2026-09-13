import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import httpx

from bulk_download.download import _write_hls_segment
from bulk_download.fetch import FetchError


def response(chunks, error=None, headers=None, status=200):
    def body(_chunk_bytes):
        yield from chunks
        if error is not None:
            raise error
    return SimpleNamespace(iter_bytes=body, close=Mock(), headers=headers or {}, status_code=status)


class SegmentRetryTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            http=SimpleNamespace(attempts=2, max_video_bytes=10), chunk_bytes=4
        )
        self.handle = io.BytesIO(b"done")
        self.handle.seek(4)

    def test_interrupted_segment_is_replaced_without_duplicate_bytes(self):
        partial = response([b"12345"], httpx.RemoteProtocolError("incomplete body"))
        complete = response([b"1234", b"56"])
        fetcher = Mock()
        fetcher.request.side_effect = [partial, complete]
        size = _write_hls_segment(fetcher, self.config, "https://cdn.example/segment", self.handle, 4)
        self.assertEqual(size, 6)
        self.assertEqual(self.handle.getvalue(), b"done123456")
        fetcher._sleep.assert_called_once_with(0)
        partial.close.assert_called_once()
        complete.close.assert_called_once()

    def test_exhausted_retries_preserve_only_preceding_complete_segments(self):
        fetcher = Mock()
        responses = [response([b"bad"], httpx.ReadError("interrupted")) for _ in range(2)]
        fetcher.request.side_effect = responses
        with self.assertRaises(httpx.ReadError):
            _write_hls_segment(fetcher, self.config, "https://cdn.example/segment", self.handle, 4)
        self.assertEqual(self.handle.getvalue(), b"done")
        self.assertEqual(fetcher.request.call_count, 2)
        for item in responses:
            item.close.assert_called_once()

    def test_size_limit_and_access_challenges_are_not_retried(self):
        fetcher = Mock()
        oversized = response([b"1234567"])
        fetcher.request.return_value = oversized
        with self.assertRaisesRegex(RuntimeError, "video_too_large"):
            _write_hls_segment(fetcher, self.config, "https://cdn.example/segment", self.handle, 4)
        self.assertEqual(self.handle.getvalue(), b"done")
        fetcher._sleep.assert_not_called()
        oversized.close.assert_called_once()
        fetcher.reset_mock()
        fetcher.request.side_effect = FetchError("access_challenge", "challenge")
        with self.assertRaises(FetchError):
            _write_hls_segment(fetcher, self.config, "https://cdn.example/segment", self.handle, 4)
        fetcher.request.assert_called_once()
        fetcher._sleep.assert_not_called()

    def test_range_recovery_keeps_only_verified_bytes_across_range_retries(self):
        headers = {"etag": '"version-1"', "content-length": "6", "accept-ranges": "bytes"}
        partials = [response([b"bad"], httpx.ReadError("cut off"), headers) for _ in range(2)]
        first = response([b"1234"], headers={"etag": '"version-1"', "content-range": "bytes 0-3/6"}, status=206)
        tail_headers = {"etag": '"version-1"', "content-range": "bytes 4-5/6"}
        failed_tail = response([b"5"], httpx.ReadError("cut off"), tail_headers, 206)
        tail = response([b"56"], headers=tail_headers, status=206)
        fetcher = Mock()
        fetcher.request.side_effect = [*partials, first, failed_tail, tail]
        size = _write_hls_segment(fetcher, self.config, "https://cdn.example/segment", self.handle, 4)
        self.assertEqual(size, 6)
        self.assertEqual(self.handle.getvalue(), b"done123456")
        self.assertEqual(fetcher.request.call_args_list[-1].kwargs["headers"],
                         {"Range": "bytes=4-5", "If-Range": '"version-1"'})
        for item in [*partials, first, failed_tail, tail]:
            item.close.assert_called_once()

    def test_range_recovery_refuses_changed_content_or_ignored_ranges(self):
        headers = {"etag": '"version-1"', "content-length": "6", "accept-ranges": "bytes"}
        self.config.http.attempts = 1
        for status, etag, content_range in [
            (206, '"changed"', "bytes 0-3/6"),
            (200, '"version-1"', "bytes 0-3/6"),
            (206, '"version-1"', "bytes 1-4/6"),
        ]:
            with self.subTest(status=status, etag=etag, content_range=content_range):
                fetcher = Mock()
                fetcher.request.side_effect = [
                    response([b"bad"], httpx.ReadError("cut off"), headers),
                    response([b"1234"], headers={"etag": etag, "content-range": content_range}, status=status),
                ]
                with self.assertRaisesRegex(RuntimeError, "verification_failure"):
                    _write_hls_segment(fetcher, self.config, "https://cdn.example/segment", self.handle, 4)
                self.assertEqual(self.handle.getvalue(), b"done")


if __name__ == "__main__":
    unittest.main()
