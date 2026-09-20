from __future__ import annotations

import pytest
from fastapi import HTTPException
from backend.utils.security import validate_source_url, is_safe_ip


def test_is_safe_ip_blocks_private_and_loopback():
    assert is_safe_ip("127.0.0.1") is False
    assert is_safe_ip("127.0.1.1") is False
    assert is_safe_ip("::1") is False
    assert is_safe_ip("10.0.0.1") is False
    assert is_safe_ip("172.16.0.1") is False
    assert is_safe_ip("192.168.1.1") is False
    assert is_safe_ip("169.254.169.254") is False  # AWS / Cloud metadata service
    assert is_safe_ip("0.0.0.0") is False

    # Public IP must be safe
    assert is_safe_ip("8.8.8.8") is True
    assert is_safe_ip("1.1.1.1") is True


def test_validate_source_url_allows_valid_video_sources():
    # YouTube video URLs
    assert "youtube.com" in validate_source_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert "youtu.be" in validate_source_url("https://youtu.be/dQw4w9WgXcQ")

    # Local upload path
    assert validate_source_url("/api/files/up_abc123.mp4") == "/api/files/up_abc123.mp4"
    assert validate_source_url("up_abc123.mp4") == "up_abc123.mp4"


def test_validate_source_url_blocks_ssrf_and_invalid_schemes():
    # Disallowed scheme
    with pytest.raises(HTTPException) as exc1:
        validate_source_url("file:///etc/passwd")
    assert exc1.value.status_code == 400
    assert "protocol" in exc1.value.detail.lower()

    # Localhost / internal attack
    with pytest.raises(HTTPException) as exc2:
        validate_source_url("http://localhost:8000/internal-api")
    assert exc2.value.status_code == 400

    # AWS metadata service IP or custom host
    with pytest.raises(HTTPException) as exc3:
        validate_source_url("http://169.254.169.254/latest/meta-data")
    assert exc3.value.status_code == 400

    # Disallowed third-party domains
    with pytest.raises(HTTPException) as exc4:
        validate_source_url("https://malicious-site.com/exploit.mp4")
    assert exc4.value.status_code == 400
    assert "not supported" in exc4.value.detail.lower()
