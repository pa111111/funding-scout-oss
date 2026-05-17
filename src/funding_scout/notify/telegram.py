"""Минимальный Telegram-нотифаер.

Если переменные окружения не заданы — функция ВПИСЫВАЕТ WARNING в лог и тихо
пропускает отправку, чтобы отсутствие кредов не валило snapshot-loop / daily
report.

Параметры:
- `FUNDING_SCOUT_TELEGRAM_BOT_TOKEN` — токен от @BotFather
- `FUNDING_SCOUT_TELEGRAM_CHAT_ID` — chat id

Кладём в `.env` файл проекта или в `/etc/funding-scout-watchdog.env` для systemd-юнитов.
"""

from __future__ import annotations

import logging
import os

import httpx
import structlog

# httpx логирует каждый POST/GET на INFO-уровне с ПОЛНЫМ URL запроса. Для Telegram
# API это означает утечку bot-токена в systemd journal — токен сидит прямо в path:
# `/bot{TOKEN}/sendMessage`. Поднимаем порог логгера httpx до WARNING, чтобы request
# не светились в журнале. Ошибки (4xx/5xx) останутся видны — они логируются на
# WARNING/ERROR уровнях независимо. Подавляем на уровне модуля при импорте — это
# affects только httpx info-логи, своих логов структуры не трогает.
logging.getLogger("httpx").setLevel(logging.WARNING)

log = structlog.get_logger()

TELEGRAM_API_BASE = "https://api.telegram.org"
ENV_TOKEN = "FUNDING_SCOUT_TELEGRAM_BOT_TOKEN"
ENV_CHAT_ID = "FUNDING_SCOUT_TELEGRAM_CHAT_ID"


class TelegramNotifier:
    """Тонкий wrapper. send() возвращает True если отправлено, False если skip/error."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        timeout: float = 10.0,
    ):
        self.token = token or os.environ.get(ENV_TOKEN)
        self.chat_id = chat_id or os.environ.get(ENV_CHAT_ID)
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, parse_mode: str = "HTML", disable_preview: bool = True) -> bool:
        if not self.configured:
            log.warning(
                "telegram_not_configured",
                missing_token=not self.token,
                missing_chat_id=not self.chat_id,
                hint=f"set {ENV_TOKEN} and {ENV_CHAT_ID} in env or .env file",
            )
            return False
        try:
            r = httpx.post(
                f"{TELEGRAM_API_BASE}/bot{self.token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": disable_preview,
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            return True
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            log.error("telegram_send_failed", error=str(e))
            return False


def send_message(text: str, **kwargs) -> bool:
    """Удобная функция, использует креды из env. Возвращает успех."""
    return TelegramNotifier().send(text, **kwargs)
