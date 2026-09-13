from __future__ import annotations

import email.utils
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Self
from urllib.parse import urljoin

import httpx

from .config import HttpConfig
from .urls import UnsafeUrl, canonicalize, ensure_public_host


class FetchError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_TRANSIENT = {429, 500, 502, 503, 504}
_REDIRECTS = {301, 302, 303, 307, 308}


class Fetcher:
    def __init__(self, config: HttpConfig):
        self.config = config
        self.client = httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(config.read_timeout_seconds, connect=config.connect_timeout_seconds),
            headers={"User-Agent": config.user_agent, "Accept-Encoding": "identity"},
            trust_env=False,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def referring_to(self, page_url: str):
        """Keep a media request chain associated with its public player page."""
        previous = self.client.headers.get("Referer")
        self.client.headers["Referer"] = canonicalize(page_url)
        try:
            yield
        finally:
            if previous is None:
                self.client.headers.pop("Referer", None)
            else:
                self.client.headers["Referer"] = previous

    def _sleep(self, attempt: int, response: httpx.Response | None = None) -> None:
        delay = self.config.backoff_seconds * (2 ** attempt)
        if response is not None and response.headers.get("Retry-After"):
            value = response.headers["Retry-After"].strip()
            try:
                delay = max(delay, float(value))
            except ValueError:
                try:
                    when = email.utils.parsedate_to_datetime(value)
                    delay = max(delay, (when - datetime.now(UTC)).total_seconds())
                except (TypeError, ValueError):
                    pass
        time.sleep(max(0, delay))

    def _request_once(
        self, url: str, stream: bool, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        current = canonicalize(url)
        for redirects in range(self.config.max_redirects + 1):
            ensure_public_host(current)
            request = self.client.build_request("GET", current, headers=headers)
            response = self.client.send(request, stream=stream)
            if response.status_code not in _REDIRECTS:
                return response
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise FetchError("http_failure", "redirect response has no Location")
            if redirects == self.config.max_redirects:
                raise FetchError("http_failure", "too many redirects")
            current = canonicalize(urljoin(current, location))
        raise FetchError("http_failure", "too many redirects")

    def request(
        self, url: str, *, stream: bool = False, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self.config.attempts):
            try:
                response = self._request_once(url, stream, headers)
                if response.headers.get("cf-mitigated", "").lower() == "challenge":
                    response.close()
                    raise FetchError("access_challenge", "site requires an interactive access challenge")
                if response.status_code in _TRANSIENT and attempt + 1 < self.config.attempts:
                    self._sleep(attempt, response)
                    response.close()
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last = exc
                if attempt + 1 < self.config.attempts:
                    self._sleep(attempt)
                    continue
            except httpx.HTTPStatusError as exc:
                raise FetchError("http_failure", f"HTTP {exc.response.status_code}") from exc
            except UnsafeUrl:
                raise
        raise FetchError("http_failure", f"request failed: {type(last).__name__}") from last

    def html(self, url: str) -> tuple[str, str]:
        response = self.request(url, stream=True)
        try:
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if media_type not in {"text/html", "application/xhtml+xml"}:
                raise FetchError("http_failure", f"expected HTML, received {media_type or 'unknown type'}")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > self.config.max_html_bytes:
                    raise FetchError("html_too_large", "HTML response exceeds configured limit")
                chunks.append(chunk)
            body = b"".join(chunks)
            encoding = response.encoding or "utf-8"
            return body.decode(encoding, errors="replace"), canonicalize(str(response.url))
        finally:
            response.close()
