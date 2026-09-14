from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import imageio_ffmpeg

from .config import Config
from .fetch import Fetcher
from .urls import canonicalize

EXTENSIONS = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}


def ffmpeg_executable() -> str:
    return os.environ.get("IMAGEIO_FFMPEG_EXE") or shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


@dataclass(frozen=True)
class Downloaded:
    path: Path
    content_type: str
    extension: str
    size: int
    sha256: str


def download_video(fetcher: Fetcher, config: Config, url: str, uid: str) -> Downloaded:
    if urlsplit(url).path.lower().endswith(".m3u8"):
        return _download_hls(fetcher, config, url, uid)
    response = fetcher.request(url, stream=True)
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type not in config.allowed_content_types or content_type not in EXTENSIONS:
        response.close()
        raise RuntimeError("unsupported_video_type")
    config.temp_dir.mkdir(parents=True, exist_ok=True)
    path = config.temp_dir / f"{uid}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("xb") as handle:
            for chunk in response.iter_bytes(config.chunk_bytes):
                size += len(chunk)
                if size > config.http.max_video_bytes:
                    raise RuntimeError("video_too_large")
                handle.write(chunk)
                digest.update(chunk)
            if size == 0:
                raise RuntimeError("download_failure")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        response.close()
    return Downloaded(path, content_type, EXTENSIONS[content_type], size, digest.hexdigest())


def _read_playlist(fetcher: Fetcher, config: Config, url: str) -> tuple[list[str], str]:
    response = fetcher.request(url, stream=True)
    try:
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > config.http.max_html_bytes:
                raise RuntimeError("download_failure")
            chunks.append(chunk)
        text = b"".join(chunks).decode("utf-8", errors="strict")
        if not text.lstrip().startswith("#EXTM3U"):
            raise RuntimeError("download_failure")
        return [line.strip() for line in text.splitlines() if line.strip()], str(response.url)
    finally:
        response.close()


def _media_playlist(fetcher: Fetcher, config: Config, url: str) -> tuple[list[str], str]:
    lines, final_url = _read_playlist(fetcher, config, url)
    if any(line.startswith("#EXT-X-MEDIA:") and 'TYPE=AUDIO' in line for line in lines):
        raise RuntimeError("unsupported_video_type")
    variants = []
    for index, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF:"):
            bandwidth = re.search(r"(?:[:,])BANDWIDTH=(\d+)", line)
            if bandwidth is None or index + 1 >= len(lines) or lines[index + 1].startswith("#"):
                raise RuntimeError("download_failure")
            variants.append((int(bandwidth.group(1)), lines[index + 1]))
    if variants:
        # Highest advertised bandwidth, URL as a stable tie breaker.
        _, candidate = max(variants)
        return _read_playlist(fetcher, config, canonicalize(urljoin(final_url, candidate)))
    return lines, final_url


def _write_hls_ranges(fetcher, config, url, handle, completed_bytes, size, etag):
    """Recover a segment using exact ranges pinned to a strong entity tag."""
    if completed_bytes + size > config.http.max_video_bytes:
        raise RuntimeError("video_too_large")
    chunk_size = min(config.chunk_bytes, 64 * 1024)
    for offset in range(0, size, chunk_size):
        end = min(offset + chunk_size, size) - 1
        start = handle.tell()
        for attempt in range(config.http.attempts):
            try:
                response = fetcher.request(
                    url, stream=True,
                    headers={"Range": f"bytes={offset}-{end}", "If-Range": etag},
                )
                try:
                    if (
                        response.status_code != 206
                        or response.headers.get("etag") != etag
                        or response.headers.get("content-range", "").strip()
                        != f"bytes {offset}-{end}/{size}"
                    ):
                        raise RuntimeError("verification_failure")
                    received = 0
                    for chunk in response.iter_bytes(min(config.chunk_bytes, 65536)):
                        received += len(chunk)
                        if received > end - offset + 1:
                            raise RuntimeError("verification_failure")
                        handle.write(chunk)
                    if received != end - offset + 1:
                        raise httpx.RemoteProtocolError("incomplete HLS byte range")
                finally:
                    response.close()
                break
            except httpx.TransportError:
                handle.seek(start)
                handle.truncate()
                if attempt + 1 == config.http.attempts:
                    raise
                fetcher._sleep(attempt)
    return size


def _write_hls_segment(fetcher: Fetcher, config: Config, url: str, handle, completed_bytes: int) -> int:
    """Retry interrupted bodies without retaining bytes from a partial segment."""
    start = handle.tell()
    range_reference = None
    for attempt in range(config.http.attempts):
        try:
            response = fetcher.request(url, stream=True)
            try:
                etag = response.headers.get("etag", "")
                length = response.headers.get("content-length", "")
                if (
                    response.status_code == 200
                    and response.headers.get("accept-ranges", "").lower() == "bytes"
                    and etag.startswith('"') and etag.endswith('"')
                    and length.isdecimal() and int(length) > 0
                ):
                    range_reference = (int(length), etag)
                size = 0
                for chunk in response.iter_bytes(config.chunk_bytes):
                    size += len(chunk)
                    if completed_bytes + size > config.http.max_video_bytes:
                        raise RuntimeError("video_too_large")
                    handle.write(chunk)
                if size == 0:
                    raise RuntimeError("download_failure")
                return size
            finally:
                response.close()
        except httpx.TransportError:
            handle.seek(start)
            handle.truncate()
            if attempt + 1 == config.http.attempts:
                if range_reference is not None:
                    return _write_hls_ranges(
                        fetcher, config, url, handle, completed_bytes, *range_reference
                    )
                raise
            fetcher._sleep(attempt)
    raise RuntimeError("download_failure")


def _download_hls(fetcher: Fetcher, config: Config, url: str, uid: str) -> Downloaded:
    config.temp_dir.mkdir(parents=True, exist_ok=True)
    path = config.temp_dir / f"{uid}.part"
    transport = config.temp_dir / f"{uid}.segments.ts"
    if path.exists() or transport.exists():
        raise FileExistsError(path if path.exists() else transport)
    lines, playlist_url = _media_playlist(fetcher, config, url)
    if any(line.startswith(("#EXT-X-KEY", "#EXT-X-MAP", "#EXT-X-BYTERANGE", "#EXT-X-STREAM-INF", "#EXT-X-DISCONTINUITY", "#EXT-X-GAP")) for line in lines):
        raise RuntimeError("unsupported_video_type")
    if "#EXT-X-ENDLIST" not in lines:
        raise RuntimeError("unsupported_video_type")
    durations = [float(line.split(":", 1)[1].split(",", 1)[0])
                 for line in lines if line.startswith("#EXTINF:")]
    segments = [canonicalize(urljoin(playlist_url, line)) for line in lines if not line.startswith("#")]
    if not segments or len(segments) > 10_000 or len(durations) != len(segments) or any(not math.isfinite(d) or d <= 0 for d in durations):
        raise RuntimeError("download_failure")
    downloaded_bytes = 0
    try:
        with transport.open("xb") as handle:
            for index, segment_url in enumerate(segments, 1):
                if shutil.disk_usage(config.temp_dir).free < 64 * 1024 * 1024 + config.chunk_bytes:
                    raise RuntimeError("insufficient_disk_space")
                downloaded_bytes += _write_hls_segment(
                    fetcher, config, segment_url, handle, downloaded_bytes
                )
                if index % 10 == 0 or index == len(segments):
                    print(f"[download] uid={uid} segments={index}/{len(segments)}", flush=True)
            handle.flush()
            os.fsync(handle.fileno())
        if shutil.disk_usage(config.temp_dir).free < downloaded_bytes + 64 * 1024 * 1024:
            raise RuntimeError("insufficient_disk_space")
        command = [
            ffmpeg_executable(),
            "-nostdin", "-hide_banner", "-loglevel", "error",
            "-protocol_whitelist", "file,pipe", "-i", str(transport),
            "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
            "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart",
            "-f", "mp4", str(path),
        ]
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, timeout=6 * 60 * 60,
            check=False,
        )
        if result.returncode != 0 or not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("download_failure")
        size = path.stat().st_size
        if size > config.http.max_video_bytes:
            raise RuntimeError("video_too_large")
        _validate_hls(path, sum(durations))
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(config.chunk_bytes), b""):
                digest.update(chunk)
        transport.unlink(missing_ok=True)
        return Downloaded(path, "video/mp4", ".mp4", size, digest.hexdigest())
    except Exception:
        path.unlink(missing_ok=True)
        transport.unlink(missing_ok=True)
        raise


def _validate_hls(path: Path, expected_duration: float) -> None:
    """Decode the completed file and compare its duration with the finite playlist."""
    result = subprocess.run(
        [ffmpeg_executable(), "-nostdin", "-hide_banner", "-v", "error",
         "-xerror", "-protocol_whitelist", "file,pipe", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
         "-progress", "pipe:1", "-f", "null", "-"],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=6 * 60 * 60, check=False,
    )
    times = re.findall(rb"out_time_us=(\d+)", result.stdout)
    duration = int(times[-1]) / 1_000_000 if times else 0
    if result.returncode or duration <= 0 or abs(duration - expected_duration) > max(2, expected_duration * 0.02):
        raise RuntimeError("verification_failure")
