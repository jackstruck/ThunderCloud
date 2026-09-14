from __future__ import annotations

import hashlib
import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

MEDIA_TYPES = {"image/jpeg", "image/png", "video/mp4"}
REDIRECTS = {301, 302, 303, 307, 308}


@dataclass(frozen=True)
class FetchResult:
    final_url: str
    content_type: str
    data: bytes
    sha256: str


def public_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = []
        for record in records:
            value = ipaddress.ip_address(record[4][0])
            if value not in addresses:
                addresses.append(value)
    if not addresses:
        raise ValueError("source host did not resolve")
    for address in addresses:
        effective = (
            address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
        )
        if effective is not None or not address.is_global:
            raise ValueError("source host resolves to a non-public address")
    return tuple(str(address) for address in addresses)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS connection whose peer IP is already DNS-validated by the caller."""

    def __init__(self, host: str, port: int, address: str, timeout: float):
        super().__init__(
            host, port=port, timeout=timeout, context=ssl.create_default_context()
        )
        self._address = address

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _content_type(value: str | None, allowed: set[str]) -> str:
    content_type = (value or "").split(";", 1)[0].strip().lower()
    if content_type not in allowed:
        raise ValueError("response has an unsupported content type")
    return content_type


def _validate_signature(content_type: str, data: bytes) -> None:
    valid = (
        (content_type == "image/jpeg" and data.startswith(b"\xff\xd8\xff"))
        or (content_type == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
        or (content_type == "video/mp4" and len(data) >= 12 and data[4:8] == b"ftyp")
    )
    if not valid:
        raise ValueError("response media signature does not match Content-Type")


def fetch_resource(
    url: str,
    *,
    max_bytes: int,
    allowed_types: set[str],
    validate_signature: bool,
    timeout: float = 20,
    max_redirects: int = 4,
    allowed_hosts: set[str] | None = None,
    referer: str | None = None,
    resolver=public_addresses,
    connection_factory=PinnedHTTPSConnection,
) -> FetchResult:
    """Fetch HTTPS media with connection-time DNS pinning on every hop."""
    current = url
    for redirect_count in range(max_redirects + 1):
        parsed = urlsplit(current)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("media URL must be credential-free HTTPS")
        if allowed_hosts is not None and (
            parsed.hostname.lower().rstrip(".") not in allowed_hosts
            or parsed.port not in {None, 443}
        ):
            raise ValueError("source host is not allowed for this adapter")
        port = parsed.port or 443
        addresses = resolver(parsed.hostname, port)
        last_error = None
        response = None
        connection = None
        # Each retry re-runs resolution so a changed or rebound answer is validated.
        for attempt in range(2):
            if attempt:
                addresses = resolver(parsed.hostname, port)
            try:
                connection = connection_factory(
                    parsed.hostname, port, addresses[attempt % len(addresses)], timeout
                )
                path = parsed.path or "/"
                if parsed.query:
                    path += "?" + parsed.query
                connection.request(
                    "GET",
                    path,
                    headers={
                        "Host": parsed.netloc,
                        "Accept": ",".join(sorted(allowed_types)),
                        "User-Agent": "BulkDownloader/1.0",
                        **({"Referer": referer} if referer else {}),
                    },
                )
                response = connection.getresponse()
                break
            except (OSError, TimeoutError, http.client.HTTPException) as error:
                last_error = error
                if connection is not None:
                    connection.close()
        if response is None:
            raise RuntimeError("media fetch connection failed") from last_error
        try:
            if response.getheader("cf-mitigated") == "challenge":
                raise ValueError("source requires an interactive challenge")
            if response.status in {401, 403, 407}:
                raise ValueError("source requires authentication")
            if response.status in REDIRECTS:
                if redirect_count == max_redirects:
                    raise ValueError("source exceeded redirect limit")
                location = response.getheader("Location")
                if not location:
                    raise ValueError("redirect omitted Location")
                current = urljoin(current, location)
                continue
            if response.status != 200:
                raise ValueError("source returned an unsupported HTTP status")
            content_type = _content_type(
                response.getheader("Content-Type"), allowed_types
            )
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > max_bytes):
                raise ValueError("source exceeds configured byte limit")
            data = response.read(max_bytes + 1)
            if not data or len(data) > max_bytes:
                raise ValueError("source is empty or exceeds configured byte limit")
            if length is not None and len(data) != int(length):
                raise RuntimeError("source response was incomplete")
            if validate_signature:
                _validate_signature(content_type, data)
            return FetchResult(
                current, content_type, data, hashlib.sha256(data).hexdigest()
            )
        except (OSError, http.client.HTTPException) as error:
            raise RuntimeError("source transfer failed") from error
        finally:
            connection.close()
    raise AssertionError("redirect loop ended unexpectedly")


def fetch_media(url: str, *, max_bytes: int, **kwargs) -> FetchResult:
    return fetch_resource(
        url,
        max_bytes=max_bytes,
        allowed_types=MEDIA_TYPES,
        validate_signature=True,
        **kwargs,
    )


def fetch_html(url: str, *, max_bytes: int = 2_000_000, **kwargs) -> tuple[str, str]:
    fetched = fetch_resource(
        url,
        max_bytes=max_bytes,
        allowed_types={"text/html", "application/xhtml+xml"},
        validate_signature=False,
        **kwargs,
    )
    return fetched.data.decode("utf-8", errors="replace"), fetched.final_url
