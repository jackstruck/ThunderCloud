"""Browser-only fixture tests; every network request is intercepted locally."""
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import sync_playwright

SCRIPT = (Path(__file__).parents[2] / 'scripts/export-search-links.js').read_text()
ORIGIN = 'https://media.example.test'


def document(*links):
    return '<html><input name="query" value="sample"><main>' + ''.join(
        f'<a href="{link}">link</a>' for link in links
    ) + '</main></html>'


@pytest.fixture
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


def setup(browser, fixtures, *, page_number=1, options=None):
    page = browser.new_page(accept_downloads=True)
    calls = []

    def handle(route):
        url = urlsplit(route.request.url)
        if url.hostname != 'media.example.test' or url.path != '/search':
            raise AssertionError('Unexpected request: ' + route.request.url)
        number = int(parse_qs(url.query).get('page', ['1'])[0])
        calls.append(number)
        response = fixtures[number]
        if isinstance(response, dict):
            route.fulfill(**response)
        else:
            route.fulfill(status=200, content_type='text/html', body=response)

    page.route('**/*', handle)
    page.goto(f'{ORIGIN}/search?query=sample&page={page_number}')
    calls.clear()
    page.evaluate('(options) => window.videoLinkExportOptions = options',
                  {'pauseMs': 0, 'download': False, **(options or {})})
    return page, calls


def test_pagination_starts_at_one_deduplicates_and_preserves_format(browser, tmp_path):
    page, calls = setup(browser, {
        1: document('/video/a', '/video/a?tracking=1', '/search?query=sample&page=2',
                    '/search?query=other&page=9', '/search?query=sample&page=-1',
                    'https://other.example/video/no', 'https://[invalid',
                    '/search?query=sample&page=2&sort=different'),
        2: document('/video/a/', '/video/b', '/search?query=sample&page=1',
                    '/search?query=sample&page=3'),
        3: document('/video/c', '/search?query=sample&page=2'),
    }, page_number=3, options={'download': True})
    with page.expect_download() as downloaded:
        result = page.evaluate(SCRIPT)
    assert calls == [1, 2, 3]
    assert result['complete']
    assert result['urls'] == [f'{ORIGIN}/video/{v}' for v in ['a', 'b', 'c']]
    assert downloaded.value.suggested_filename == 'video-links.txt'
    output = tmp_path / 'urls.txt'
    downloaded.value.save_as(output)
    assert output.read_text() == '\n'.join(result['urls']) + '\n'
    page.close()


def test_http_failure_exports_partial_results(browser):
    page, calls = setup(browser, {
        1: document('/video/a', '/search?query=sample&page=2'),
        2: {'status': 503, 'content_type': 'text/html', 'body': 'temporarily unavailable'},
    }, options={'download': True})
    with page.expect_download() as downloaded:
        result = page.evaluate(SCRIPT)
    assert not result['complete']
    assert result['pages'] == 1
    assert result['urls'] == [f'{ORIGIN}/video/a']
    assert '503' in result['error']
    assert downloaded.value.suggested_filename == 'video-links-partial.txt'
    assert not page.evaluate('window.videoLinkExport.running')
    page.close()


def test_page_limit_never_claims_full_completion(browser):
    page, calls = setup(browser, {
        1: document('/video/a', '/search?query=sample&page=2'),
        2: document('/video/b'),
    }, options={'maxPages': 1})
    result = page.evaluate(SCRIPT)
    assert calls == [1]
    assert not result['complete']
    assert 'page limit' in result['error']
    page.close()


def test_stop_during_pause_keeps_collected_urls(browser):
    page, calls = setup(browser, {
        1: document('/video/a', '/search?query=sample&page=2'),
        2: document('/video/b'),
    }, options={'pauseMs': 60000})
    page.evaluate('window.exportPromise = ' + SCRIPT + '; undefined')
    page.wait_for_function('window.videoLinkExport.pages === 1')
    page.evaluate('window.videoLinkExport.stop()')
    result = page.evaluate('window.exportPromise')
    assert result['error'] == 'Stopped by operator.'
    assert result['urls'] == [f'{ORIGIN}/video/a']
    assert calls == [1]
    page.close()


@pytest.mark.parametrize('response', [
    {'status': 200, 'content_type': 'application/json', 'body': '{}'},
    {'status': 200, 'content_type': 'text/html', 'body': '<html>Challenge</html>'},
    {'status': 302, 'headers': {'location': '/search?query=other'}, 'body': ''},
])
def test_unexpected_pages_are_not_reported_as_complete(browser, response):
    page, calls = setup(browser, {1: document()})
    page.route('**/*', lambda route: route.fulfill(**response))
    result = page.evaluate(SCRIPT)
    assert not result['complete']
    assert result['pages'] == 0
    assert result['urls'] == []
    page.close()


def test_empty_results_complete_without_download(browser):
    page, calls = setup(browser, {1: document()})
    result = page.evaluate(SCRIPT)
    assert result['complete'] and result['urls'] == [] and calls == [1]
    page.close()
