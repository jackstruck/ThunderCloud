from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import imageio_ffmpeg

from .config import Config
from .fetch import Fetcher
from .urls import canonicalize


EXTENSIONS = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}


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
    for index, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            for candidate in lines[index + 1:]:
                if not candidate.startswith("#"):
                    variant = canonicalize(urljoin(final_url, candidate))
                    return _read_playlist(fetcher, config, variant)
    return lines, final_url


def _download_hls(fetcher: Fetcher, config: Config, url: str, uid: str) -> Downloaded:
    config.temp_dir.mkdir(parents=True, exist_ok=True)
    path = config.temp_dir / f"{uid}.part"
    transport = config.temp_dir / f"{uid}.segments.ts"
    if path.exists() or transport.exists():
        raise FileExistsError(path if path.exists() else transport)
    lines, playlist_url = _media_playlist(fetcher, config, url)
    if any(line.startswith(("#EXT-X-KEY", "#EXT-X-MAP", "#EXT-X-BYTERANGE")) for line in lines):
        raise RuntimeError("unsupported_video_type")
    segments = [canonicalize(urljoin(playlist_url, line)) for line in lines if not line.startswith("#")]
    if not segments or len(segments) > 10_000:
        raise RuntimeError("download_failure")
    downloaded_bytes = 0
    try:
        with transport.open("xb") as handle:
            for index, segment_url in enumerate(segments, 1):
                response = fetcher.request(segment_url, stream=True)
                try:
                    for chunk in response.iter_bytes(config.chunk_bytes):
                        downloaded_bytes += len(chunk)
                        if downloaded_bytes > config.http.max_video_bytes:
                            raise RuntimeError("video_too_large")
                        handle.write(chunk)
                finally:
                    response.close()
                if index % 10 == 0 or index == len(segments):
                    print(f"[download] uid={uid} segments={index}/{len(segments)}", flush=True)
            handle.flush()
            os.fsync(handle.fileno())
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", str(transport),
            "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
            "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart",
            "-fs", str(config.http.max_video_bytes), "-f", "mp4", str(path),
        ]
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, timeout=6 * 60 * 60
        )
        if result.returncode != 0 or not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("download_failure")
        size = path.stat().st_size
        if size > config.http.max_video_bytes:
            raise RuntimeError("video_too_large")
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
