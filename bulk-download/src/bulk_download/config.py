from __future__ import annotations

import base64
import binascii
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class GcpConfig:
    project: str
    bucket: str
    prefix: str
    csek_file: Path


@dataclass(frozen=True)
class HttpConfig:
    user_agent: str
    connect_timeout_seconds: float
    read_timeout_seconds: float
    max_redirects: int
    attempts: int
    backoff_seconds: float
    max_html_bytes: int
    max_video_bytes: int


@dataclass(frozen=True)
class Config:
    path: Path
    gcp: GcpConfig
    input_file: Path
    justpaste_file: Path
    luluvid_file: Path
    temp_dir: Path
    manifest_file: Path
    http: HttpConfig
    chunk_bytes: int
    allowed_content_types: tuple[str, ...]


_SCHEMA = {
    "gcp": {"project", "bucket", "prefix", "csek_file"},
    "input": {"heylink_file", "justpaste_file", "luluvid_file"},
    "local": {"temp_dir", "manifest_file"},
    "http": {
        "user_agent", "connect_timeout_seconds", "read_timeout_seconds",
        "max_redirects", "attempts", "backoff_seconds", "max_html_bytes",
        "max_video_bytes",
    },
    "download": {"chunk_bytes", "allowed_content_types"},
}


def _resolve(base: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{label} must be a non-empty string")
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (base / candidate).resolve()


def load_config(path_value: str | Path) -> Config:
    path = Path(path_value).resolve()
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read config: {exc}") from exc
    if set(raw) != set(_SCHEMA):
        raise ConfigError(f"config sections must be exactly {sorted(_SCHEMA)}")
    for section, keys in _SCHEMA.items():
        if not isinstance(raw[section], dict) or set(raw[section]) != keys:
            raise ConfigError(f"[{section}] keys must be exactly {sorted(keys)}")
    base = path.parent
    gcp, inp, local, http, download = (
        raw["gcp"], raw["input"], raw["local"], raw["http"], raw["download"]
    )
    csek_path = Path(gcp["csek_file"])
    if not csek_path.is_absolute():
        raise ConfigError("gcp.csek_file must be an absolute path")
    positive = {
        "connect_timeout_seconds": http["connect_timeout_seconds"],
        "read_timeout_seconds": http["read_timeout_seconds"],
        "max_redirects": http["max_redirects"], "attempts": http["attempts"],
        "backoff_seconds": http["backoff_seconds"],
        "max_html_bytes": http["max_html_bytes"],
        "max_video_bytes": http["max_video_bytes"],
        "chunk_bytes": download["chunk_bytes"],
    }
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0 for v in positive.values()):
        raise ConfigError("timeout, attempt, redirect, and byte settings must be positive numbers")
    allowed = download["allowed_content_types"]
    if not isinstance(allowed, list) or not allowed or not all(isinstance(v, str) for v in allowed):
        raise ConfigError("download.allowed_content_types must be a non-empty string array")
    strings = [gcp["project"], gcp["bucket"], gcp["prefix"], http["user_agent"]]
    if not all(isinstance(v, str) and v.strip() for v in strings):
        raise ConfigError("GCP names, prefix, and user agent must be non-empty strings")
    return Config(
        path=path,
        gcp=GcpConfig(gcp["project"], gcp["bucket"], gcp["prefix"].strip("/"), csek_path),
        input_file=_resolve(base, inp["heylink_file"], "input.heylink_file"),
        justpaste_file=_resolve(base, inp["justpaste_file"], "input.justpaste_file"),
        luluvid_file=_resolve(base, inp["luluvid_file"], "input.luluvid_file"),
        temp_dir=_resolve(base, local["temp_dir"], "local.temp_dir"),
        manifest_file=_resolve(base, local["manifest_file"], "local.manifest_file"),
        http=HttpConfig(**http),
        chunk_bytes=int(download["chunk_bytes"]),
        allowed_content_types=tuple(v.lower() for v in allowed),
    )


def load_csek(config: Config) -> bytes:
    try:
        encoded = config.gcp.csek_file.read_text(encoding="ascii").strip()
        key = base64.b64decode(encoded, validate=True)
    except (OSError, UnicodeError, binascii.Error) as exc:
        raise ConfigError(f"cannot read a valid Base64 CSEK file: {exc}") from exc
    if len(key) != 32:
        raise ConfigError("CSEK must decode to exactly 32 bytes")
    return key
