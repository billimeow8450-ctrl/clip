from __future__ import annotations

from enum import Enum


class ErrorKind(str, Enum):
    AUTH = "auth"
    HTTP_403 = "http_403"
    PO_TOKEN = "po_token"
    EJS = "ejs"
    GEO = "geo"
    PRIVATE = "private"
    FORMAT = "format"
    NETWORK = "network"
    PROVIDER_MISMATCH = "provider_mismatch"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MEDIA_INVALID = "media_invalid"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class MediaTransportError(RuntimeError):
    def __init__(self, message: str, *, kind: ErrorKind = ErrorKind.UNKNOWN, route: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.route = route


class TransportCancelled(MediaTransportError):
    def __init__(self, message: str = "Media transport cancelled") -> None:
        super().__init__(message, kind=ErrorKind.CANCELLED)


class ProviderVersionMismatch(MediaTransportError):
    def __init__(self, message: str) -> None:
        super().__init__(message, kind=ErrorKind.PROVIDER_MISMATCH, route="mweb_bgutil")


def classify_error(text: str) -> ErrorKind:
    value = (text or "").lower()
    if not value:
        return ErrorKind.UNKNOWN

    if "provider plugin and the http server are on different versions" in value or "version mismatch" in value:
        return ErrorKind.PROVIDER_MISMATCH
    if "sign in to confirm you're not a bot" in value or "sign in to confirm you’re not a bot" in value:
        return ErrorKind.AUTH
    if "login required" in value or "members-only" in value or "members only" in value:
        return ErrorKind.AUTH
    if "private video" in value or "this video is private" in value:
        return ErrorKind.PRIVATE
    if "http error 403" in value or "403 forbidden" in value or "status code: 403" in value:
        return ErrorKind.HTTP_403
    if "po token" in value or "po-token" in value or "pot provider" in value or "bgutil" in value and "error" in value:
        return ErrorKind.PO_TOKEN
    if "challenge solving failed" in value or "js challenge" in value or "jsc:" in value and "error" in value:
        return ErrorKind.EJS
    if "not available in your country" in value or "geo" in value and "restricted" in value:
        return ErrorKind.GEO
    if "requested format is not available" in value or "no video formats found" in value or "no downloadable formats" in value:
        return ErrorKind.FORMAT
    if any(token in value for token in (
        "timed out", "timeout", "connection reset", "connection refused", "temporary failure",
        "network is unreachable", "remote end closed", "unable to download webpage", "tls",
        "name or service not known", "connection aborted",
    )):
        return ErrorKind.NETWORK
    return ErrorKind.UNKNOWN


def is_fast_fallback_error(kind: ErrorKind) -> bool:
    return kind in {
        ErrorKind.AUTH,
        ErrorKind.HTTP_403,
        ErrorKind.PO_TOKEN,
        ErrorKind.EJS,
        ErrorKind.PROVIDER_MISMATCH,
        ErrorKind.PROVIDER_UNAVAILABLE,
        ErrorKind.GEO,
    }
