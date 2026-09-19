"""Tests for network capture redaction."""

from __future__ import annotations

from automation.src.ui_regression.capture import REDACTED, redact_value


def test_redact_value_removes_credentials_and_bearer_tokens():
    payload = {
        "username": "u1_auto",
        "password": "123456",
        "access_token": "secret-token",
        "headers": {
            "Authorization": "Bearer abc.def.ghi",
            "Cookie": "sid=secret",
        },
        "items": [{"refresh_token": "refresh-secret"}],
    }

    redacted = redact_value(payload)

    assert redacted["username"] == "u1_auto"
    assert redacted["password"] == REDACTED
    assert redacted["access_token"] == REDACTED
    assert redacted["headers"]["Cookie"] == REDACTED
    assert redacted["items"][0]["refresh_token"] == REDACTED
