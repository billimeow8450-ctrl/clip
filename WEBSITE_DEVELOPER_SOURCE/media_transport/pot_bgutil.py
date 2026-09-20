from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib import metadata
from typing import Optional

from .errors import ErrorKind, MediaTransportError, ProviderVersionMismatch


@dataclass(frozen=True)
class BgUtilStatus:
    ok: bool
    plugin_version: str
    server_version: str
    base_url: str
    detail: str = ""


_CACHE: dict[str, tuple[float, BgUtilStatus]] = {}


def _major(version: str) -> Optional[int]:
    try:
        return int((version or "").split(".", 1)[0])
    except (TypeError, ValueError):
        return None


def plugin_version() -> str:
    try:
        return metadata.version("bgutil-ytdlp-pot-provider")
    except metadata.PackageNotFoundError:
        return ""


def server_version(base_url: str, timeout: float = 2.0) -> str:
    url = base_url.rstrip("/") + "/ping"
    request = urllib.request.Request(url, headers={"User-Agent": "editingbot-v78-media-transport/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise MediaTransportError(
            f"BgUtil HTTP provider is unavailable at {base_url}: {exc}",
            kind=ErrorKind.PROVIDER_UNAVAILABLE,
            route="mweb_bgutil",
        ) from exc
    return str(raw.get("version") or "").strip()


def check_compatibility(base_url: str, *, timeout: float = 2.0, cache_seconds: float = 30.0) -> BgUtilStatus:
    now = time.monotonic()
    cached = _CACHE.get(base_url)
    if cached and now - cached[0] <= cache_seconds:
        return cached[1]

    plugin = plugin_version()
    if not plugin:
        status = BgUtilStatus(False, "", "", base_url, "Python BgUtil provider plugin is not installed")
        _CACHE[base_url] = (now, status)
        return status

    try:
        server = server_version(base_url, timeout=timeout)
    except MediaTransportError as exc:
        status = BgUtilStatus(False, plugin, "", base_url, str(exc))
        _CACHE[base_url] = (now, status)
        return status

    pm = _major(plugin)
    sm = _major(server)
    if pm is None or sm is None:
        status = BgUtilStatus(False, plugin, server, base_url, "Could not parse provider versions")
    elif pm != sm:
        status = BgUtilStatus(
            False,
            plugin,
            server,
            base_url,
            f"BgUtil major-version mismatch: plugin {plugin}, HTTP server {server}",
        )
    elif plugin != server:
        # The upstream provider has had incompatibilities even across minor releases.
        # Treat any mismatch as unavailable rather than silently gambling on compatibility.
        status = BgUtilStatus(
            False,
            plugin,
            server,
            base_url,
            f"BgUtil version mismatch: plugin {plugin}, HTTP server {server}; exact match required",
        )
    else:
        status = BgUtilStatus(True, plugin, server, base_url, "compatible")

    _CACHE[base_url] = (now, status)
    return status


def require_compatible(base_url: str, *, timeout: float = 2.0) -> BgUtilStatus:
    status = check_compatibility(base_url, timeout=timeout, cache_seconds=5.0)
    if status.ok:
        return status
    if status.plugin_version and status.server_version and _major(status.plugin_version) != _major(status.server_version):
        raise ProviderVersionMismatch(status.detail)
    raise MediaTransportError(
        status.detail or "BgUtil provider unavailable",
        kind=(ErrorKind.PROVIDER_MISMATCH if status.server_version else ErrorKind.PROVIDER_UNAVAILABLE),
        route="mweb_bgutil",
    )
