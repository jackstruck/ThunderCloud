from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from .secure_fetch import fetch_html

_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass(frozen=True)
class ResolvedSource:
    final_url: str
    source_adapter: str


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors: list[str] = []
        self.media: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self.anchors.append(values["href"])
        if tag in {"video", "source"} and values.get("src"):
            self.media.append(values["src"])
        if (
            tag == "meta"
            and values.get("property")
            in {
                "og:video",
                "og:video:secure_url",
            }
            and values.get("content")
        ):
            self.media.append(values["content"])


def _canonical_https(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("adapter produced an invalid HTTPS URL")
    host = parsed.hostname.rstrip(".").lower()
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    return urlunsplit(("https", host + port, parsed.path or "/", parsed.query, ""))


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").rstrip(".").lower()


def _luluvid_links(html: str, base_url: str) -> list[str]:
    parser = _Links()
    parser.feed(html)
    result = []
    for raw in parser.anchors:
        absolute = urljoin(base_url, raw)
        wrapped = urlsplit(absolute)
        if wrapped.hostname in {
            "justpaste.it",
            "www.justpaste.it",
        } and wrapped.path.startswith("/redirect/"):
            absolute = unquote(wrapped.path.rsplit("/", 1)[-1])
        try:
            canonical = _canonical_https(absolute)
        except ValueError:
            continue
        if _host(canonical) in {"luluvid.com", "www.luluvid.com"}:
            result.append(canonical)
    return list(dict.fromkeys(result))


def _base_number(value: str, radix: int) -> int:
    result = 0
    for character in value:
        digit = _DIGITS.find(character)
        if digit < 0 or digit >= radix:
            raise ValueError("invalid packed-script token")
        result = result * radix + digit
    return result


def _unpack_player(html: str) -> str | None:
    match = re.search(
        r"}\('((?:\\.|[^'])*)',(\d+),(\d+),'((?:\\.|[^'])*)'\.split\('\|'\)",
        html,
        re.DOTALL,
    )
    if not match:
        return None
    payload = bytes(match.group(1), "utf-8").decode("unicode_escape")
    radix, count = int(match.group(2)), int(match.group(3))
    words = bytes(match.group(4), "utf-8").decode("unicode_escape").split("|")
    if radix < 2 or radix > len(_DIGITS) or count > 10_000 or len(payload) > 1_000_000:
        return None

    def replace(token: re.Match[str]) -> str:
        try:
            index = _base_number(token.group(0), radix)
        except ValueError:
            return token.group(0)
        return words[index] if index < len(words) and words[index] else token.group(0)

    return re.sub(r"\b[0-9A-Za-z]+\b", replace, payload)


def _media_link(html: str, base_url: str) -> str:
    parser = _Links()
    parser.feed(html)
    candidates = list(parser.media)
    if packed := _unpack_player(html):
        match = re.search(
            r"file\s*:\s*['\"](https://[^'\"]+)['\"]", packed, re.IGNORECASE
        )
        if match:
            candidates.append(match.group(1))
    for candidate in candidates:
        try:
            return _canonical_https(urljoin(base_url, candidate))
        except ValueError:
            continue
    raise ValueError("Luluvid page has no static HTTPS media URL")


def resolve_source(url: str, html_fetcher=fetch_html) -> ResolvedSource:
    canonical = _canonical_https(url)
    host = _host(canonical)
    if host in {"justpaste.it", "www.justpaste.it"}:
        html, final_page = html_fetcher(canonical)
        links = _luluvid_links(html, final_page)
        if len(links) != 1:
            raise ValueError(
                "JustPaste page must resolve to exactly one Luluvid source"
            )
        canonical = links[0]
        host = _host(canonical)
        adapter = "justpaste"
    elif host in {"luluvid.com", "www.luluvid.com"}:
        adapter = "luluvid"
    else:
        return ResolvedSource(canonical, "direct")
    html, final_page = html_fetcher(canonical)
    return ResolvedSource(_media_link(html, final_page), adapter)
