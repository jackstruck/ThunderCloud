from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit


class UnsafeUrl(ValueError):
    pass


HOSTS = {
    "heylink": {"heylink.me", "www.heylink.me"},
    "justpaste": {"justpaste.it", "www.justpaste.it"},
    "luluvid": {"luluvid.com", "www.luluvid.com", "luluvdoo.com", "www.luluvdoo.com"},
}


def canonicalize(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrl(f"invalid URL: {exc}") from exc
    if parts.scheme.lower() != "https" or not host:
        raise UnsafeUrl("URL must use HTTPS and include a hostname")
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrl("embedded URL credentials are not allowed")
    if port not in (None, 443):
        raise UnsafeUrl("non-default ports are not allowed")
    netloc = host if port is None else f"{host}:443"
    return urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))


def require_stage(url: str, stage: str) -> str:
    canonical = canonicalize(url)
    if urlsplit(canonical).hostname not in HOSTS[stage]:
        raise UnsafeUrl(f"URL host is not allowed for {stage}")
    return canonical


def luluvid_fetch_url(url: str) -> str:
    """Use LuluStream's public file page while preserving the input's identity.

    JustPaste also publishes luluvdoo.com links. The same file IDs are served by
    luluvid.com, whose pages expose the static player used by this downloader.
    This is only a transport URL; manifests and deterministic UIDs retain the
    original input URL.
    """
    canonical = require_stage(url, "luluvid")
    parts = urlsplit(canonical)
    if parts.hostname in {"luluvdoo.com", "www.luluvdoo.com"}:
        return urlunsplit(("https", "luluvid.com", parts.path, parts.query, ""))
    return canonical


def ensure_public_host(url: str) -> None:
    host = urlsplit(canonicalize(url)).hostname
    assert host is not None
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrl(f"cannot resolve host: {host}") from exc
    if not answers:
        raise UnsafeUrl(f"host has no addresses: {host}")
    for answer in answers:
        ip = ipaddress.ip_address(answer[4][0])
        if not ip.is_global:
            raise UnsafeUrl(f"host resolves to a non-public address: {host}")
