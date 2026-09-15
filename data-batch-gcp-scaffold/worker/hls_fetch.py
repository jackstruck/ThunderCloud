"""Acquire finite Hotscope MPEG-TS HLS through the DNS-pinned HTTPS transport."""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

from .secure_fetch import FetchResult, fetch_resource

PLAYLIST_TYPES = {"application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl", "audio/x-mpegurl", "text/plain", "application/octet-stream"}
SEGMENT_TYPES = {"video/mp2t", "application/octet-stream"}
CDN_HOSTS = {"cdn.hotscope.tv"}


def fetch_hotscope_video(url: str, *, page_url: str, max_bytes: int,
                         resource_fetcher=fetch_resource) -> FetchResult:
    deadline = time.monotonic() + 180

    def get(target, limit, types):
        if time.monotonic() >= deadline:
            raise RuntimeError("HLS acquisition exceeded its time budget")
        return resource_fetcher(target, max_bytes=limit, allowed_types=types,
                                validate_signature=False, allowed_hosts=CDN_HOSTS,
                                referer=page_url, timeout=min(20, max(1, deadline - time.monotonic())))

    def playlist(target):
        result = get(target, 2_000_000, PLAYLIST_TYPES)
        lines = [line.strip() for line in result.data.decode("utf-8").splitlines() if line.strip()]
        if not lines or lines[0] != "#EXTM3U":
            raise ValueError("HLS response is not a playlist")
        return lines, result.final_url

    lines, final_url = playlist(url)
    if any(line.startswith("#EXT-X-MEDIA:") and "TYPE=AUDIO" in line for line in lines):
        raise ValueError("Separate HLS audio renditions are unsupported")
    variants = []
    for index, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF:"):
            match = re.search(r"(?:[:,])BANDWIDTH=(\d+)", line)
            if not match or index + 1 >= len(lines) or lines[index + 1].startswith("#"):
                raise ValueError("Malformed HLS rendition")
            variants.append((int(match[1]), lines[index + 1]))
    if variants:
        lines, final_url = playlist(urljoin(final_url, max(variants)[1]))
    unsupported = ("#EXT-X-KEY", "#EXT-X-MAP", "#EXT-X-BYTERANGE", "#EXT-X-DISCONTINUITY",
                   "#EXT-X-GAP", "#EXT-X-STREAM-INF", "#EXT-X-MEDIA:")
    if "#EXT-X-ENDLIST" not in lines or any(line.startswith(unsupported) for line in lines):
        raise ValueError("Unsupported or non-finite HLS playlist")
    segments = [urljoin(final_url, line) for line in lines if not line.startswith("#")]
    durations = [float(line.split(":", 1)[1].split(",", 1)[0]) for line in lines if line.startswith("#EXTINF:")]
    if (not segments or len(segments) > 10000 or len(segments) != len(durations)
            or any(not math.isfinite(d) or d <= 0 for d in durations)):
        raise ValueError("Malformed HLS segment list")
    expected_duration = sum(durations)
    if expected_duration > 900:
        raise ValueError("HLS video exceeds the 15 minute acquisition limit")
    try:
        with tempfile.TemporaryDirectory(prefix="source-hls-") as directory:
            transport, output = Path(directory) / "source.ts", Path(directory) / "source.mp4"
            size = 0
            with transport.open("xb") as handle:
                for segment in segments:
                    if size >= max_bytes:
                        raise ValueError("HLS source exceeds configured byte limit")
                    result = get(segment, max_bytes - size, SEGMENT_TYPES)
                    if not result.data or result.data[0] != 0x47 or len(result.data) % 188:
                        raise ValueError("HLS segment is not MPEG-TS")
                    size += len(result.data)
                    handle.write(result.data)
                del result
            if shutil.disk_usage(directory).free < size + 64 * 1024 * 1024:
                raise RuntimeError("Insufficient temporary space for HLS remux")
            _command([
                "ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-xerror",
                "-protocol_whitelist", "file,pipe", "-threads", "1", "-i", str(transport),
                "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-bsf:a", "aac_adtstoasc",
                "-movflags", "+faststart", str(output),
            ], deadline)
            transport.unlink()
            if not output.is_file() or not 0 < output.stat().st_size <= max_bytes:
                raise ValueError("Remuxed HLS source exceeds configured byte limit or is empty")
            _validate(output, expected_duration, deadline)
            data = output.read_bytes()
            if len(data) < 12 or data[4:8] != b"ftyp":
                raise ValueError("Remux did not produce MP4")
            return FetchResult(page_url, "video/mp4", data, hashlib.sha256(data).hexdigest())
    except OSError as error:
        raise RuntimeError("HLS acquisition requires FFmpeg and writable scratch storage") from error


def _command(command, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("HLS acquisition exceeded its time budget")
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=min(120, remaining), check=False)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("HLS media validation timed out") from error
    if result.returncode:
        raise ValueError("HLS media conversion or validation failed")
    return result.stdout


def _validate(path, expected_duration, deadline):
    probe = json.loads(_command([
        "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams",
        "-show_format", "-of", "json", str(path),
    ], deadline))
    if not any(stream.get("codec_type") == "video" for stream in probe.get("streams", [])):
        raise ValueError("HLS output has no video")
    duration = float(probe.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0 or abs(duration - expected_duration) > max(2, expected_duration * .02):
        raise ValueError("HLS output duration does not match the full playlist")
    _command([
        "ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-xerror",
        "-protocol_whitelist", "file,pipe", "-threads", "1", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a:0?", "-threads", "1", "-f", "null", "-",
    ], deadline)
