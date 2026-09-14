"""Public Hotscope user discovery; parse data without executing page JavaScript."""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .fetch import Fetcher, FetchError
from .pipeline import DiscoveryFailure
from .urls import UnsafeUrl, canonicalize

PAGE_HOSTS = {"hotscope.tv", "www.hotscope.tv"}
HOSTS = PAGE_HOSTS | {"cdn.hotscope.tv"}
TOKEN = re.compile(r"[A-Za-z0-9_-]+")
USER = re.compile(r"[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class HotscopeDiscovery:
    page_url: str
    requested_url: str
    provider_id: str
    username: str
    media_url: str
    uid: str
    media_referer: str
    provider: str = "hotscope"
    format_version: int = 2


def user_name(value: str) -> str:
    value = value.strip()
    if "://" in value:
        parts = urlsplit(canonicalize(value))
        if parts.hostname not in PAGE_HOSTS or parts.query or not parts.path.startswith("/user/"):
            raise ValueError("expected a Hotscope /user/<username> URL")
        value = parts.path.removeprefix("/user/").rstrip("/")
    else:
        value = value.removeprefix("@")
    if not USER.fullmatch(value) or value in {".", ".."}:
        raise ValueError("invalid Hotscope username")
    return value


def video_id(value: str) -> str:
    value = value.strip()
    if "://" in value:
        parts = urlsplit(canonicalize(value))
        if parts.hostname not in PAGE_HOSTS or not parts.path.startswith("/video/"):
            raise ValueError("expected a Hotscope /video/<id> URL")
        value = parts.path.removeprefix("/video/").rstrip("/")
    if not TOKEN.fullmatch(value):
        raise ValueError("invalid Hotscope video ID")
    return value


def read_list(path: str | None) -> list[str]:
    if path is None:
        return []
    try:
        return [line.strip() for line in Path(path).read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    except OSError as exc:
        raise ValueError(f"cannot read selection file: {path}") from exc


def _body(fetcher: Fetcher, url: str, **kwargs) -> str:
    response = fetcher.request(url, stream=True, **kwargs)
    try:
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > fetcher.config.max_html_bytes:
                raise FetchError("response_too_large", "provider response exceeds configured limit")
        return data.decode("utf-8")
    finally:
        response.close()


def _rows(text: str):
    for line in text.splitlines():
        _, sep, payload = line.partition(":")
        if sep:
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                continue


def _objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def _page_objects(html: str):
    # Flight strings can be split across script tags; assemble before parsing rows.
    chunks = []
    for script in BeautifulSoup(html, "html.parser").find_all("script"):
        text = script.string or ""
        match = re.fullmatch(r"self\.__next_f\.push\((.*)\);?", text, re.DOTALL)
        if match:
            try:
                frame = json.loads(match.group(1))
                if frame[0] == 1 and isinstance(frame[1], str):
                    chunks.append(frame[1])
            except (ValueError, IndexError, TypeError):
                continue
    for row in _rows("".join(chunks)):
        yield from _objects(row)


def _action(fetcher: Fetcher, html: str, page: str) -> str:
    # The public profile uses a read-only Next.js server action. Resolve its
    # build-specific identifier from shipped scripts instead of pinning a hash.
    scripts = BeautifulSoup(html, "html.parser").find_all("script", src=True)
    for script in scripts[:60]:
        url = canonicalize(urljoin(page, script["src"]))
        parts = urlsplit(url)
        if parts.hostname not in PAGE_HOSTS or not parts.path.startswith("/_next/static/chunks/"):
            continue
        body = _body(fetcher, url)
        match = re.search(
            r'createServerReference\)\("([a-f0-9]{40,64})",[^;]{0,250}?"fetchUserVideos"\)', body
        )
        if match:
            return match.group(1)
    raise FetchError("unsupported_profile", "public user listing action was not found")


def resolve_video(fetcher: Fetcher, ident: str, username: str) -> HotscopeDiscovery:
    page = f"https://hotscope.tv/video/{ident}"
    html, final = fetcher.html(page)
    if video_id(final) != ident:
        raise FetchError("identity_mismatch", "detail page redirected to another video")
    candidates = [obj for obj in _page_objects(html) if obj.get("id") == ident and "playlist" in obj]
    if not candidates:
        raise FetchError("no_full_video", "requested video's full playlist was not found")
    playlists = set()
    for obj in candidates:
        if obj.get("uploader", {}).get("username", "").casefold() != username.casefold():
            raise FetchError("identity_mismatch", "video does not belong to the selected user")
        media = canonicalize(obj["playlist"])
        parts = urlsplit(media)
        if parts.hostname != "cdn.hotscope.tv" or parts.path != f"/videos/{ident}/playlist.m3u8":
            raise FetchError("no_full_video", "unexpected full-video descriptor")
        playlists.add(media)
    if len(playlists) != 1:
        raise FetchError("identity_mismatch", "conflicting playback descriptors")
    return HotscopeDiscovery(page, page, ident, username, playlists.pop(),
                             str(uuid.uuid5(uuid.NAMESPACE_URL, f"hotscope:video:{ident}")), page)


def discover_users(fetcher: Fetcher, users: list[str], selected: set[str],
                   max_videos: int | None, max_pages: int, done: set[str] | None = None):
    found, failures = [], []
    seen, matched = set(), set()
    done = done or set()
    with fetcher.restricted_to(HOSTS):
        for username in users:
            page = f"https://hotscope.tv/user/{username}"
            context = {"provider": "hotscope", "username": username, "requested_url": page}
            try:
                html, final = fetcher.html(page)
                if user_name(final).casefold() != username.casefold():
                    raise FetchError("identity_mismatch", "profile redirected to another user")
                action = _action(fetcher, html, final)
                current, count, visited = 1, 0, set()
                while current is not None:
                    if current in visited or len(visited) >= max_pages:
                        raise FetchError("pagination_limit", "profile pagination did not finish within the page limit")
                    visited.add(current)
                    body = _body(fetcher, final, method="POST",
                                 content=json.dumps([username, current, False]).encode(),
                                 headers={"Next-Action": action, "Content-Type": "text/plain;charset=UTF-8",
                                          "Accept": "text/x-component"})
                    batches = [row for row in _rows(body) if isinstance(row, dict) and "data" in row and "meta" in row]
                    if len(batches) != 1 or not isinstance(batches[0]["data"], list):
                        raise FetchError("unsupported_profile", "unrecognized public user listing")
                    batch = batches[0]
                    for obj in batch["data"]:
                        ident = video_id(obj["id"])
                        if obj.get("uploader", {}).get("username", "").casefold() != username.casefold():
                            raise FetchError("identity_mismatch", "listing contains another user's video")
                        if selected and ident not in selected:
                            continue
                        matched.add(ident)
                        if ident in seen:
                            continue
                        seen.add(ident)
                        count += 1
                        video_page = f"https://hotscope.tv/video/{ident}"
                        if video_page not in done:
                            try:
                                found.append(resolve_video(fetcher, ident, username))
                            except (FetchError, UnsafeUrl, ValueError, TypeError, KeyError, AttributeError) as exc:
                                failures.append(DiscoveryFailure(getattr(exc, "code", "invalid_provider_data"),
                                    "could not resolve selected video", {**context, "page_url": video_page, "provider_id": ident}))
                        if max_videos is not None and count >= max_videos:
                            break
                    if max_videos is not None and count >= max_videos:
                        break
                    current = batch["meta"]["next"]
                    if current is not None and (type(current) is not int or current <= 0):
                        raise FetchError("unsupported_profile", "invalid pagination cursor")
            except (FetchError, UnsafeUrl, ValueError, TypeError, KeyError, AttributeError) as exc:
                failures.append(DiscoveryFailure(getattr(exc, "code", "invalid_provider_data"),
                                                "could not finish user discovery", context))
    for ident in sorted(selected - matched):
        failures.append(DiscoveryFailure("selection_not_found", "selected video was not found for the supplied users",
                                        {"provider": "hotscope", "provider_id": ident}))
    return found, failures
