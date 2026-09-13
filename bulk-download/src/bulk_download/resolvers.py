from __future__ import annotations

import re
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from .urls import UnsafeUrl, canonicalize, require_stage


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def anchor_links(html: str, base_url: str, stage: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(base_url, str(anchor["href"]))
        if stage == "luluvid":
            wrapped = urlsplit(absolute)
            if wrapped.hostname in {"justpaste.it", "www.justpaste.it"} and wrapped.path.startswith("/redirect/"):
                encoded_target = wrapped.path.rsplit("/", 1)[-1]
                absolute = unquote(encoded_target)
        try:
            links.append(require_stage(absolute, stage))
        except (UnsafeUrl, ValueError):
            continue
    return _unique(links)


def video_url(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str | None] = []
    video = soup.find("video")
    candidates.append(video.get("src") if video else None)
    if video:
        candidates.extend(source.get("src") for source in video.find_all("source"))
    for prop in ("og:video:secure_url", "og:video"):
        meta = soup.find("meta", attrs={"property": prop})
        candidates.append(meta.get("content") if meta else None)
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return canonicalize(urljoin(base_url, str(candidate)))
        except UnsafeUrl:
            continue
    packed = _unpack_player(html)
    if packed:
        match = re.search(
            r"sources\s*:\s*\[\s*\{\s*file\s*:\s*['\"](https://[^'\"]+\.m3u8(?:\?[^'\"]*)?)['\"]",
            packed,
            re.IGNORECASE,
        )
        if match:
            try:
                return canonicalize(match.group(1))
            except UnsafeUrl:
                pass
    return None


def player_referer(html: str, page_url: str) -> str:
    """Use the matching provider embed's origin, as browser playback does."""
    page = urlsplit(canonicalize(page_url))
    file_id = page.path.rstrip('/').rsplit('/', 1)[-1]
    for frame in BeautifulSoup(html, 'html.parser').find_all('iframe', src=True):
        try:
            embed = urlsplit(canonicalize(urljoin(page_url, str(frame['src']))))
        except UnsafeUrl:
            continue
        if (
            embed.hostname in {'luluvdo.com', 'luluvid.com', 'luluvdoo.com'}
            and embed.path.rstrip('/') == f'/e/{file_id}'
        ):
            return f'https://{embed.netloc}/'
    return canonicalize(page_url)


_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _base_number(value: str, radix: int) -> int:
    result = 0
    for character in value:
        digit = _DIGITS.find(character)
        if digit < 0 or digit >= radix:
            raise ValueError("invalid packed-script token")
        result = result * radix + digit
    return result


def _unpack_player(html: str) -> str | None:
    """Decode the standard packed JWPlayer setup without executing JavaScript."""
    match = re.search(
        r"}\('((?:\\.|[^'])*)',(\d+),(\d+),'((?:\\.|[^'])*)'\.split\('\|'\)",
        html,
        re.DOTALL,
    )
    if not match:
        return None
    payload = bytes(match.group(1), "utf-8").decode("unicode_escape")
    radix = int(match.group(2))
    count = int(match.group(3))
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
