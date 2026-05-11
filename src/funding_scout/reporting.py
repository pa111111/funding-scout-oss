"""Daily report: топ-N связок в Telegram. Запускается systemd-таймером раз в сутки.

Парадигма: показываем как есть, без фильтров. Сортируем по spread_apr_pct DESC.
Помечаем малую ёмкость badge'ом, чтобы пользователь видел контекст, но не отбрасываем.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .notify.telegram import TelegramNotifier
from .web.data import DEFAULT_CAPITAL_USD, get_latest_setups


def format_daily_report(meta: dict, rows: list[dict], top_n: int = 10) -> str:
    """HTML-сообщение для Telegram.

    Layout (HTML, потому что Telegram parse_mode=HTML):
        <b>funding-scout daily</b> · <i>2026-05-03 12:00 UTC</i>
        Snapshot age: 5 min · venues: HL=191, Lighter=156 · setups: 93

        <b>Top 10 by spread APR</b>
        <pre>...table...</pre>
    """
    if not rows:
        return "<b>funding-scout daily</b>\n\nНет данных в БД."

    snapshot_iso = meta.get("snapshot_iso", "?")
    age_seconds = meta.get("age_seconds") or 0
    age_min = age_seconds // 60
    venue_counts = meta.get("venue_counts", {})
    venue_str = ", ".join(f"{v}={c}" for v, c in sorted(venue_counts.items()))
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    top = sorted(rows, key=lambda r: r["spread_apr_pct"] or 0, reverse=True)[:top_n]

    # Pre-формат таблица. Лимит ширины ~60 символов чтоб мобильный Telegram не ломал.
    lines = [
        f"{'Ticker':<8}{'Long->Short':<22}{'APR%':>8}{'$/d':>7}{'Vol$M':>8}",
        "-" * 53,
    ]
    for r in top:
        ticker = r["ticker"][:7]
        pair = f"{r['long_venue'][:9]}->{r['short_venue'][:9]}"
        apr = r["spread_apr_pct"]
        ev = r["base_ev_usd_per_day"]
        vol = r["min_volume_24h_m_usd"]
        vol_str = f"{vol:.2f}" if vol is not None else "—"
        lines.append(f"{ticker:<8}{pair:<22}{apr:>+8.1f}{ev:>+7.1f}{vol_str:>8}")

    table = "\n".join(lines)

    return (
        f"<b>funding-scout daily</b>  ·  <i>{now_str}</i>\n"
        f"snapshot {snapshot_iso} ({age_min}m ago)  ·  {venue_str}  ·  setups: {meta['setups_count']}\n\n"
        f"<b>Top {top_n} by spread APR</b> (на капитал ${DEFAULT_CAPITAL_USD:,.0f})\n"
        f"<pre>{table}</pre>\n"
        f"<i>Парадигма: показано всё. Vol — min 24h volume среди двух ног, в $M. "
        f"Низкий vol = honeypot-риск, не наш выбор за тебя.</i>"
    )


def send_daily_report(top_n: int = 10) -> bool:
    """Сформировать и отправить дневной отчёт. Возвращает True если отправлено."""
    meta, rows = get_latest_setups()
    text = format_daily_report(meta, rows, top_n=top_n)
    notifier = TelegramNotifier()
    return notifier.send(text, parse_mode="HTML")
