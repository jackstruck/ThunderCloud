from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .fetch import Fetcher
from .resolvers import anchor_links, player_referer, video_url
from .urls import UnsafeUrl, luluvid_fetch_url, require_stage


@dataclass(frozen=True)
class Discovery:
    heylink_url: str | None
    justpaste_url: str | None
    luluvid_url: str
    media_url: str
    uid: str
    media_referer: str | None = None


class DiscoveryFailure(RuntimeError):
    def __init__(self, code: str, message: str, context: dict[str, str] | None = None):
        super().__init__(message)
        self.code = code
        self.context = context or {}


def load_inputs(path: Path, stage: str) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DiscoveryFailure("invalid_input_url", f"cannot read input file: {exc}") from exc
    result: list[str] = []
    for number, raw in enumerate(lines, 1):
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        try:
            result.append(require_stage(value, stage))
        except UnsafeUrl as exc:
            raise DiscoveryFailure("invalid_input_url", f"line {number}: {exc}") from exc
    return list(dict.fromkeys(result))


def discover(fetcher: Fetcher, heylink_url: str) -> tuple[list[Discovery], list[DiscoveryFailure]]:
    html, final_heylink = fetcher.html(heylink_url)
    justpaste_urls = anchor_links(html, final_heylink, "justpaste")
    if not justpaste_urls:
        return [], [DiscoveryFailure("no_justpaste_link", "no static JustPaste link", {"heylink_url": heylink_url})]
    found: list[Discovery] = []
    failures: list[DiscoveryFailure] = []
    seen_luluvid: set[str] = set()
    for justpaste in justpaste_urls:
        html, final_justpaste = fetcher.html(justpaste)
        luluvid_urls = anchor_links(html, final_justpaste, "luluvid")
        if not luluvid_urls:
            failures.append(DiscoveryFailure(
                "no_luluvid_link", "no static Luluvid link",
                {"heylink_url": heylink_url, "justpaste_url": justpaste},
            ))
        for luluvid in luluvid_urls:
            if luluvid in seen_luluvid:
                continue
            seen_luluvid.add(luluvid)
            html, final_luluvid = fetcher.html(luluvid_fetch_url(luluvid))
            media = video_url(html, final_luluvid)
            if media is None:
                failures.append(DiscoveryFailure(
                    "no_static_video_url", "no static video URL",
                    {"heylink_url": heylink_url, "justpaste_url": justpaste, "luluvid_url": luluvid},
                ))
                continue
            uid = str(uuid.uuid5(uuid.NAMESPACE_URL, luluvid))
            found.append(Discovery(heylink_url, justpaste, luluvid, media, uid, player_referer(html, final_luluvid)))
    return found, failures


def discover_justpaste(fetcher: Fetcher, justpaste_url: str) -> tuple[list[Discovery], list[DiscoveryFailure]]:
    html, final_justpaste = fetcher.html(justpaste_url)
    luluvid_urls = anchor_links(html, final_justpaste, "luluvid")
    if not luluvid_urls:
        return [], [DiscoveryFailure(
            "no_luluvid_link", "no static Luluvid link", {"justpaste_url": justpaste_url}
        )]
    found: list[Discovery] = []
    failures: list[DiscoveryFailure] = []
    for luluvid in luluvid_urls:
        html, final_luluvid = fetcher.html(luluvid_fetch_url(luluvid))
        media = video_url(html, final_luluvid)
        if media is None:
            failures.append(DiscoveryFailure(
                "no_static_video_url", "no static video URL",
                {"justpaste_url": justpaste_url, "luluvid_url": luluvid},
            ))
            continue
        uid = str(uuid.uuid5(uuid.NAMESPACE_URL, luluvid))
        found.append(Discovery(None, justpaste_url, luluvid, media, uid, player_referer(html, final_luluvid)))
    return found, failures


def discover_luluvid(fetcher: Fetcher, luluvid_url: str) -> tuple[list[Discovery], list[DiscoveryFailure]]:
    html, final_luluvid = fetcher.html(luluvid_fetch_url(luluvid_url))
    media = video_url(html, final_luluvid)
    if media is None:
        return [], [DiscoveryFailure(
            "no_static_video_url", "no static video URL", {"luluvid_url": luluvid_url}
        )]
    uid = str(uuid.uuid5(uuid.NAMESPACE_URL, luluvid_url))
    return [Discovery(None, None, luluvid_url, media, uid, player_referer(html, final_luluvid))], []
