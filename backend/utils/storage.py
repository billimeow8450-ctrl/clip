"""Durable object storage with a safe local-only development fallback.

Production media must never be stored on a Render instance filesystem.  Supabase
Storage is accessed with the service-role key server-side only; clients continue
to use the application-owned signed download endpoint.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

import httpx


class StorageError(RuntimeError):
    pass


def _config() -> tuple[str, str, str] | None:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "clip-media")
    if not url and not key:
        return None
    if not url or not key:
        # Local development commonly has a public project URL in .env but no
        # server credential. Treat that as local mode; production fails fast
        # through require_production_storage().
        return None
    return url, key, bucket


def using_object_storage() -> bool:
    return _config() is not None


def require_production_storage() -> None:
    if os.getenv("ENVIRONMENT", "development").lower() == "production" and not using_object_storage():
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required in production")


def _headers(key: str) -> dict[str, str]:
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def _object_url(url: str, bucket: str, object_key: str) -> str:
    return f"{url}/storage/v1/object/{quote(bucket, safe='')}/{quote(object_key, safe='/')}"


async def upload_file(object_key: str, source: Path, content_type: str = "application/octet-stream") -> None:
    config = _config()
    if not config:
        return
    url, key, bucket = config
    headers = {**_headers(key), "Content-Type": content_type, "x-upsert": "true"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
        with source.open("rb") as handle:
            response = await client.put(_object_url(url, bucket, object_key), content=handle, headers=headers)
    if response.status_code not in (200, 201):
        raise StorageError(f"Object upload failed ({response.status_code})")


async def download_file(object_key: str, destination: Path) -> None:
    config = _config()
    if not config:
        raise StorageError("Object storage is not configured")
    url, key, bucket = config
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
        async with client.stream("GET", _object_url(url, bucket, object_key), headers=_headers(key)) as response:
            if response.status_code != 200:
                raise StorageError(f"Object download failed ({response.status_code})")
            with destination.open("wb") as handle:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    handle.write(chunk)


async def create_download_url(object_key: str, expires_in: int = 300) -> str:
    config = _config()
    if not config:
        raise StorageError("Object storage is not configured")
    url, key, bucket = config
    endpoint = f"{url}/storage/v1/object/sign/{quote(bucket, safe='')}/{quote(object_key, safe='/')}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(endpoint, json={"expiresIn": expires_in}, headers=_headers(key))
    if response.status_code != 200:
        raise StorageError(f"Could not create download URL ({response.status_code})")
    signed_path = response.json().get("signedURL")
    if not signed_path:
        raise StorageError("Storage did not return a signed download URL")
    return signed_path if signed_path.startswith("http") else f"{url}/storage/v1{signed_path}"


async def delete_file(object_key: str) -> None:
    config = _config()
    if not config:
        return
    url, key, bucket = config
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.delete(_object_url(url, bucket, object_key), headers=_headers(key))
    if response.status_code not in (200, 404):
        raise StorageError(f"Object deletion failed ({response.status_code})")
