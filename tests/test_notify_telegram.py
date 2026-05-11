"""Тесты Telegram нотифаера через httpx.MockTransport.

Цель — гарантировать что:
- При отсутствии env-переменных send() возвращает False (не падает)
- При ошибке HTTP — log + return False (не валим snapshot-loop / report)
- Корректные креды → 200 → True
"""

from __future__ import annotations

import httpx
import pytest

from funding_scout.notify.telegram import TelegramNotifier


def test_not_configured_returns_false_silently(monkeypatch):
    """Без env переменных send() не должен падать — только лог-warning."""
    monkeypatch.delenv("FUNDING_SCOUT_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("FUNDING_SCOUT_TELEGRAM_CHAT_ID", raising=False)
    n = TelegramNotifier()
    assert not n.configured
    assert n.send("hello") is False


def test_partial_config_not_configured(monkeypatch):
    monkeypatch.setenv("FUNDING_SCOUT_TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.delenv("FUNDING_SCOUT_TELEGRAM_CHAT_ID", raising=False)
    n = TelegramNotifier()
    assert not n.configured
    assert n.send("hello") is False


def test_send_success(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        # Path должен содержать токен и /sendMessage
        assert "/bot123:abc/sendMessage" in str(request.url)
        captured["body"] = request.content
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": 42}}
        )

    transport = httpx.MockTransport(handler)
    # patch'им httpx.post чтобы он использовал наш transport
    real_post = httpx.post

    def fake_post(url, **kwargs):
        with httpx.Client(transport=transport) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(httpx, "post", fake_post)

    n = TelegramNotifier(token="123:abc", chat_id="999")
    assert n.configured
    assert n.send("hi <b>world</b>") is True
    assert captured["body"] is not None  # тело запроса дошло


def test_send_http_error_returns_false(monkeypatch):
    def handler(request):
        return httpx.Response(401, json={"ok": False, "description": "unauthorized"})

    transport = httpx.MockTransport(handler)

    def fake_post(url, **kwargs):
        with httpx.Client(transport=transport) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(httpx, "post", fake_post)

    n = TelegramNotifier(token="bad", chat_id="999")
    assert n.send("hi") is False  # не падаем — только лог


def test_send_network_error_returns_false(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("simulated network down")

    transport = httpx.MockTransport(handler)

    def fake_post(url, **kwargs):
        with httpx.Client(transport=transport) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(httpx, "post", fake_post)

    n = TelegramNotifier(token="123:abc", chat_id="999")
    assert n.send("hi") is False


def test_explicit_args_beat_env(monkeypatch):
    """Если передали token/chat_id напрямую — env игнорируется."""
    monkeypatch.setenv("FUNDING_SCOUT_TELEGRAM_BOT_TOKEN", "from-env")
    monkeypatch.setenv("FUNDING_SCOUT_TELEGRAM_CHAT_ID", "from-env")
    n = TelegramNotifier(token="explicit", chat_id="999")
    assert n.token == "explicit"
    assert n.chat_id == "999"
