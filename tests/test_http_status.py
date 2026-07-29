# SPDX-License-Identifier: MIT
"""HTTP status -> monitor status mapping (_status_from_http)."""
from __future__ import annotations

from usage_monitor_app.core import _status_from_http


def test_success_codes_default_ok():
    for code in (200, 201, 204, 299):
        assert _status_from_http(code, {}) == ("ok", None)


def test_success_custom_default():
    assert _status_from_http(200, {}, default="unknown") == ("unknown", None)


def test_402_quota_exhausted():
    status, msg = _status_from_http(402, {"error": "insufficient credits"})
    assert status == "quota_exhausted"
    assert msg == "insufficient credits"


def test_429_rate_limited():
    status, msg = _status_from_http(429, {"message": "slow down"})
    assert status == "rate_limited"
    assert msg == "slow down"


def test_auth_errors_map_to_error():
    for code in (401, 403):
        status, msg = _status_from_http(code, {"msg": "denied"})
        assert status == "error"
        assert msg == "denied"


def test_other_errors_map_to_warning():
    for code in (400, 404, 500, 503):
        status, _ = _status_from_http(code, {})
        assert status == "warning"


def test_message_fallback_to_http_code():
    status, msg = _status_from_http(500, None)
    assert status == "warning"
    assert msg == "HTTP 500"


def test_message_from_nested_error_dict():
    _, msg = _status_from_http(400, {"error": {"message": "bad request", "code": "x"}})
    assert msg == "bad request"


def test_message_from_detail_key():
    _, msg = _status_from_http(422, {"detail": "unprocessable"})
    assert msg == "unprocessable"


def test_message_truncated():
    _, msg = _status_from_http(500, {"error": "x" * 500})
    assert len(msg) == 240
