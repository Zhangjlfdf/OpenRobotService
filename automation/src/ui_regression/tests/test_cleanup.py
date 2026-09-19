"""Tests for best-effort test-data cleanup."""

from __future__ import annotations

import json

import httpx

from automation.src.ui_regression.cleanup import CleanupManager


def test_cleanup_deletes_ticket_and_conversation():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/auth/login":
            username = json.loads(request.content)["username"]
            return httpx.Response(200, json={"access_token": f"token-{username}"})
        if request.url.path == "/api/tasks/837":
            assert request.headers["authorization"] == "Bearer token-admin"
            return httpx.Response(204)
        if request.url.path == "/api/call/conversations/11":
            assert request.headers["authorization"] == "Bearer token-u1_auto"
            return httpx.Response(204)
        return httpx.Response(404)

    client = httpx.Client(
        base_url="http://backend.test",
        transport=httpx.MockTransport(handler),
    )
    manager = CleanupManager(
        "http://backend.test",
        admin_username="admin",
        admin_password="admin-pass",
        u1_username="u1_auto",
        u1_password="u1-pass",
        client=client,
    )

    result = manager.cleanup(ticket_id=837, conversation_id=11)

    assert result.ok is True
    assert result.deleted == ["工单", "会话"]
    assert ("DELETE", "/api/tasks/837") in calls
    assert ("DELETE", "/api/call/conversations/11") in calls


def test_cleanup_failure_returns_warning_without_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"access_token": "token"})
        return httpx.Response(500, text="boom")

    client = httpx.Client(
        base_url="http://backend.test",
        transport=httpx.MockTransport(handler),
    )
    manager = CleanupManager(
        "http://backend.test",
        admin_username="admin",
        admin_password="admin-pass",
        client=client,
    )

    result = manager.cleanup(ticket_id=837)

    assert result.ok is False
    assert result.deleted == []
    assert result.warnings[0].resource == "工单"
    assert "HTTP 500" in result.warnings[0].message


def test_cleanup_missing_credentials_is_a_warning():
    manager = CleanupManager("http://backend.test")
    result = manager.cleanup(ticket_id=837, conversation_id=11)

    assert result.ok is False
    assert [warning.resource for warning in result.warnings] == ["工单", "会话"]
