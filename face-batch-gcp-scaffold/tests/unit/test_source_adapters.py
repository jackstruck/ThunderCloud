import pytest
from worker.source_adapters import resolve_source


def test_direct_https_media_passes_through_canonicalized():
    result = resolve_source("https://MEDIA.example.test/a.mp4?token=x")
    assert result.final_url == "https://media.example.test/a.mp4?token=x"
    assert result.source_adapter == "direct"


def test_luluvid_extracts_static_video_source():
    calls = []

    def fetcher(url):
        calls.append(url)
        return (
            '<html><video><source src="https://cdn.example.test/a.mp4?secret=x"></video></html>',
            url,
        )

    result = resolve_source("https://luluvid.com/watch/abc", fetcher)
    assert result.final_url == "https://cdn.example.test/a.mp4?secret=x"
    assert result.source_adapter == "luluvid"
    assert calls == ["https://luluvid.com/watch/abc"]


def test_justpaste_resolves_one_luluvid_then_static_media():
    responses = iter(
        [
            (
                '<a href="https://luluvid.com/e/abc">video</a>',
                "https://justpaste.it/page",
            ),
            (
                '<meta property="og:video:secure_url" content="https://cdn.example/a.mp4">',
                "https://luluvid.com/e/abc",
            ),
        ]
    )
    result = resolve_source("https://justpaste.it/page", lambda _url: next(responses))
    assert result.final_url == "https://cdn.example/a.mp4"
    assert result.source_adapter == "justpaste"


def test_justpaste_rejects_ambiguous_multi_media_page():
    html = '<a href="https://luluvid.com/a">a</a><a href="https://luluvid.com/b">b</a>'
    with pytest.raises(ValueError, match="exactly one"):
        resolve_source("https://justpaste.it/page", lambda url: (html, url))


def test_adapter_rejects_non_https_media_url():
    html = '<video src="http://cdn.example/a.mp4"></video>'
    with pytest.raises(ValueError, match="no static HTTPS"):
        resolve_source("https://luluvid.com/a", lambda url: (html, url))


def hotscope_html(videos):
    import json
    return '<script>self.__next_f.push(' + json.dumps([1, '1:' + json.dumps({'videos': videos}) + '\n']) + ')</script>'


def test_hotscope_resolves_requested_full_playlist_only():
    page = 'https://hotscope.tv/video/abc'
    html = hotscope_html([
        {'id': 'related', 'playlist': 'https://cdn.hotscope.tv/videos/related/playlist.m3u8'},
        {'id': 'abc', 'preview': 'https://cdn.hotscope.tv/videos/abc/preview.mp4',
         'playlist': 'https://cdn.hotscope.tv/videos/abc/playlist.m3u8'},
    ])
    def fetcher(url, **kwargs):
        assert kwargs['allowed_hosts'] == {'hotscope.tv', 'www.hotscope.tv'}
        return html, url
    result = resolve_source(page, fetcher)
    assert result.source_adapter == 'hotscope'
    assert result.final_url == 'https://cdn.hotscope.tv/videos/abc/playlist.m3u8'
    assert result.page_url == page


@pytest.mark.parametrize('videos', [
    [{'id': 'abc', 'preview': 'https://cdn.hotscope.tv/videos/abc/preview.mp4'}],
    [{'id': 'abc', 'playlist': 'https://cdn.hotscope.tv/videos/other/playlist.m3u8'}],
    [{'id': 'abc', 'playlist': 'https://evil.test/videos/abc/playlist.m3u8'}],
])
def test_hotscope_rejects_previews_and_mismatched_descriptors(videos):
    with pytest.raises(ValueError):
        resolve_source('https://hotscope.tv/video/abc', lambda url, **kw: (hotscope_html(videos), url))


def test_hotscope_rejects_profile_and_redirect_to_another_video():
    with pytest.raises(ValueError, match='video page'):
        resolve_source('https://hotscope.tv/user/alice')
    with pytest.raises(ValueError, match='another video'):
        resolve_source('https://hotscope.tv/video/abc', lambda url, **kw: ('', 'https://hotscope.tv/video/def'))
