import hashlib
import subprocess

import pytest
from worker.hls_fetch import fetch_hotscope_video
from worker.secure_fetch import FetchResult

PAGE = 'https://hotscope.tv/video/abc'
MASTER = 'https://cdn.hotscope.tv/videos/abc/playlist.m3u8'


def test_generated_hls_acquires_complete_mp4_and_selects_highest_bandwidth(tmp_path):
    subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=64x64:rate=10',
        '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '3', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-f', 'hls', '-hls_time', '1',
        '-hls_list_size', '0', str(tmp_path / 'high.m3u8'),
    ], check=True, capture_output=True, timeout=30)
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        assert kwargs['allowed_hosts'] == {'cdn.hotscope.tv'}
        assert kwargs['referer'] == PAGE
        if url == MASTER:
            data = b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2\nhigh.m3u8\n'
        else:
            data = (tmp_path / url.rsplit('/', 1)[-1]).read_bytes()
        if len(data) > kwargs['max_bytes']:
            raise ValueError('byte limit')
        return FetchResult(url, 'application/octet-stream', data, '')
    result = fetch_hotscope_video(MASTER, page_url=PAGE, max_bytes=1_000_000, resource_fetcher=fetch)
    assert result.data[4:8] == b'ftyp'
    assert result.sha256 == hashlib.sha256(result.data).hexdigest()
    assert result.content_type == 'video/mp4'
    assert result.final_url == PAGE
    assert not any('low.m3u8' in call for call in calls)
    with pytest.raises(ValueError, match='byte limit'):
        fetch_hotscope_video(MASTER, page_url=PAGE, max_bytes=10, resource_fetcher=fetch)


@pytest.mark.parametrize('playlist', [
    '#EXTM3U\n#EXTINF:1,\nsegment.ts\n',
    '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXT-X-ENDLIST',
    '#EXTM3U\n#EXT-X-MAP:URI="init"\n#EXT-X-ENDLIST',
    '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,URI="audio"',
    '#EXTM3U\n#EXTINF:901,\nsegment.ts\n#EXT-X-ENDLIST',
])
def test_unsupported_playlists_fail_before_segments(playlist):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return FetchResult(url, 'text/plain', playlist.encode(), '')
    with pytest.raises(ValueError):
        fetch_hotscope_video(MASTER, page_url=PAGE, max_bytes=100, resource_fetcher=fetch)
    assert calls == [MASTER]


def test_duration_mismatch_rejects_playable_but_truncated_output(monkeypatch):
    from worker import hls_fetch
    monkeypatch.setattr(hls_fetch, '_command', lambda *args: b'{"streams":[{"codec_type":"video"}],"format":{"duration":"1"}}')
    with pytest.raises(ValueError, match='duration'):
        hls_fetch._validate('source.mp4', 30, 1000)
