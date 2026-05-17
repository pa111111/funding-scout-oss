"""Daily report: топ-N связок в Telegram. Запускается systemd-таймером раз в сутки.

В Telegram (в отличие от Web UI) фильтруем связки по min 24h volume: для $5k
капитала слиппедж на vol < $1M делает связку нерентабельной (см. user_framework.md).
Web UI остаётся «парадигма transparent disclosure» — показывает всё.

В шапке и футере сообщения явно проставляется пометка про активный фильтр, чтобы
пользователь видел что это отфильтрованная выборка, а не весь рынок.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .notify.telegram import TelegramNotifier
from .web.data import DEFAULT_CAPITAL_USD, get_latest_setups

# Минимальный 24h volume (min среди двух ног связки) для попадания в Telegram-отчёт.
# Порог под капитал ~$5k: на vol < $1M slippage > 2% за сторону, экономика ломается.
# Для других порогов передавай min_volume_usd параметром в format_daily_report.
TELEGRAM_MIN_VOLUME_USD = 1_000_000.0


def _filter_by_volume(
    rows: list[dict],
    min_volume_usd: float,
) -> list[dict]:
    """Оставить только связки с min_volume_24h_m_usd ≥ порога.

    min_volume_24h_m_usd хранится в миллионах ($M), порог переводим в те же единицы.
    None volume трактуется как «нет уверенности про размер» → отфильтровывается
    (не пропускаем в Telegram, лучше пропустить лишнюю связку чем показать honeypot).
    """
    min_vol_m = min_volume_usd / 1_000_000.0
    return [r for r in rows if (r.get("min_volume_24h_m_usd") or 0) >= min_vol_m]


def _format_filter_badge(min_volume_usd: float | None) -> str:
    """Текст пометки про активный фильтр. Пустая строка если фильтра нет."""
    if min_volume_usd is None:
        return ""
    return f"filter: 24h vol ≥ ${min_volume_usd / 1_000_000:.0f}M"


def format_daily_report(
    meta: dict,
    rows: list[dict],
    top_n: int = 10,
    min_volume_usd: float | None = TELEGRAM_MIN_VOLUME_USD,
) -> str:
    """HTML-сообщение для Telegram.

    Layout (HTML, потому что Telegram parse_mode=HTML):
        <b>funding-scout daily</b> · <i>2026-05-03 12:00 UTC</i> · filter: 24h vol ≥ $1M
        snapshot ... · venues: ... · setups: 324 (after filter: 87)

        <b>Top 10 by spread APR</b>
        <pre>...table...</pre>
        <i>Filter: 24h vol ≥ $1M ...</i>

    Передай `min_volume_usd=None` чтобы получить отчёт без фильтра (полный список).
    """
    if not rows:
        return "<b>funding-scout daily</b>\n\nНет данных в БД."

    snapshot_iso = meta.get("snapshot_iso", "?")
    age_seconds = meta.get("age_seconds") or 0
    age_min = age_seconds // 60
    venue_counts = meta.get("venue_counts", {})
    venue_str = ", ".join(f"{v}={c}" for v, c in sorted(venue_counts.items()))
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    filter_badge = _format_filter_badge(min_volume_usd)
    if min_volume_usd is not None:
        filtered = _filter_by_volume(rows, min_volume_usd)
    else:
        filtered = rows

    setups_str = (
        f"setups: {meta['setups_count']} (after filter: {len(filtered)})"
        if min_volume_usd is not None
        else f"setups: {meta['setups_count']}"
    )
    header_suffix = f"  ·  <i>{filter_badge}</i>" if filter_badge else ""
    header = (
        f"<b>funding-scout daily</b>  ·  <i>{now_str}</i>{header_suffix}\n"
        f"snapshot {snapshot_iso} ({age_min}m ago)  ·  {venue_str}  ·  {setups_str}"
    )

    if not filtered:
        return (
            f"{header}\n\n"
            f"Нет связок проходящих фильтр <b>{filter_badge}</b>. "
            f"Полный список без фильтра — в Web UI."
        )

    top = sorted(filtered, key=lambda r: r["spread_apr_pct"] or 0, reverse=True)[:top_n]

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

    footer = (
        f"<i>Filter: 24h vol ≥ ${min_volume_usd / 1_000_000:.0f}M "
        f"(под капитал ~${DEFAULT_CAPITAL_USD:,.0f}). "
        f"Полный список без фильтра — в Web UI.</i>"
        if min_volume_usd is not None
        else (
            f"<i>Парадигма: показано всё. Vol — min 24h volume среди двух ног, в $M. "
            f"Низкий vol = honeypot-риск, не наш выбор за тебя.</i>"
        )
    )

    return (
        f"{header}\n\n"
        f"<b>Top {top_n} by spread APR</b> (на капитал ${DEFAULT_CAPITAL_USD:,.0f})\n"
        f"<pre>{table}</pre>\n"
        f"{footer}"
    )


def send_daily_report(
    top_n: int = 10,
    min_volume_usd: float | None = TELEGRAM_MIN_VOLUME_USD,
) -> bool:
    """Сформировать и отправить дневной отчёт. Возвращает True если отправлено."""
    meta, rows = get_latest_setups()
    text = format_daily_report(meta, rows, top_n=top_n, min_volume_usd=min_volume_usd)
    notifier = TelegramNotifier()
    return notifier.send(text, parse_mode="HTML")
