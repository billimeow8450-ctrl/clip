from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable

from .manifest import BrokerConfig, RouteSpec


def resolve_ytdlp(config: BrokerConfig) -> str:
    if config.ytdlp_bin and Path(config.ytdlp_bin).is_file():
        return config.ytdlp_bin
    sibling = Path(sys.executable).with_name("yt-dlp")
    if sibling.is_file():
        return str(sibling)
    found = shutil.which("yt-dlp")
    if found:
        return found
    raise FileNotFoundError("yt-dlp executable was not found")


def resolve_deno(config: BrokerConfig) -> str:
    candidates = [
        config.deno_path,
        os.getenv("DENO_BIN", ""),
        shutil.which("deno") or "",
        "/usr/local/bin/deno",
    ]
    for value in candidates:
        if value and Path(value).is_file():
            return str(Path(value).resolve())
    return ""


def common_args(config: BrokerConfig, route: RouteSpec) -> list[str]:
    args = [
        resolve_ytdlp(config),
        "--no-playlist",
        "--newline",
        "--retries", str(config.retries),
        "--fragment-retries", str(config.fragment_retries),
        "--extractor-retries", str(config.retries),
        "--socket-timeout", str(config.socket_timeout),
        "--concurrent-fragments", str(max(1, config.concurrent_fragments)),
        "--no-cache-dir",
    ]
    if config.force_ipv4:
        args.append("--force-ipv4")
    if config.http_chunk_size > 0:
        args.extend(["--http-chunk-size", str(config.http_chunk_size)])

    if config.ejs_enabled:
        deno = resolve_deno(config)
        if deno:
            args.extend(["--js-runtimes", f"deno:{deno}"])

    if route.proxy:
        args.extend(["--proxy", route.proxy])

    if route.player_client:
        args.extend(["--extractor-args", f"youtube:player_client={route.player_client}"])

    if route.require_bgutil:
        args.extend([
            "--extractor-args",
            f"youtubepot-bgutilhttp:base_url={config.bgutil_base_url.rstrip('/')}",
        ])

    return args


def add_auth_args(args: list[str], route: RouteSpec, cookie_file: Path | None) -> None:
    if route.use_cookies and cookie_file and cookie_file.is_file():
        args.extend(["--cookies", str(cookie_file)])


def build_extract_command(config: BrokerConfig, route: RouteSpec, url: str, cookie_file: Path | None) -> list[str]:
    args = common_args(config, route)
    add_auth_args(args, route, cookie_file)
    args.extend(["--dump-single-json", "--skip-download", "--ignore-no-formats-error", url])
    return args


def build_download_command(
    config: BrokerConfig,
    route: RouteSpec,
    url: str,
    outtmpl: str,
    selector: str,
    container: str,
    cookie_file: Path | None,
    *,
    start: float | None = None,
    end: float | None = None,
) -> list[str]:
    args = common_args(config, route)
    add_auth_args(args, route, cookie_file)
    args.extend(["-f", selector, "--merge-output-format", container, "-o", outtmpl])
    if start is not None and end is not None:
        args.extend([
            "--download-sections", f"*{float(start):.3f}-{float(end):.3f}",
            "--force-keyframes-at-cuts",
        ])
    args.append(url)
    return args


def build_audio_command(
    config: BrokerConfig,
    route: RouteSpec,
    url: str,
    outtmpl: str,
    cookie_file: Path | None,
    *,
    audio_quality: str,
) -> list[str]:
    args = common_args(config, route)
    add_auth_args(args, route, cookie_file)
    args.extend([
        "-f", "bestaudio/best",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", audio_quality,
        "-o", outtmpl,
        url,
    ])
    return args


def build_subtitle_command(
    config: BrokerConfig,
    route: RouteSpec,
    url: str,
    outtmpl: str,
    language: str,
    cookie_file: Path | None,
) -> list[str]:
    args = common_args(config, route)
    add_auth_args(args, route, cookie_file)
    args.extend([
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", language,
        "--sub-format", "vtt/best",
        "-o", outtmpl,
        url,
    ])
    return args
