#!/usr/bin/env bash
# funding-scout watchdog — здоровьем чек, шлёт Telegram при сбоях.
#
# Запускается systemd-таймером funding-scout-watchdog.timer раз в минуту.
# Креды берутся из /etc/funding-scout-watchdog.env (закладывается через EnvironmentFile в .service):
#   FUNDING_SCOUT_TELEGRAM_BOT_TOKEN=...
#   FUNDING_SCOUT_TELEGRAM_CHAT_ID=...
#
# Антиспам: state в /var/lib/funding-scout-watchdog/last_state. Алертим только при
# смене состояния OK→FAIL и FAIL→OK, чтобы не флудить каждую минуту во время инцидента.
#
# Что проверяем:
#   1. snapshot freshness — последний ts в БД не старше 70 минут
#   2. web UI отвечает — curl /_dash-layout
#   3. postgres достижим — psql -c 'SELECT 1'
#   4. свежесть per-venue — каждый venue фетчился последний час
#
# Disk-full мониторинг здесь не делаем — оставлено на инфраструктурный watchdog.

set -u

STATE_DIR="/var/lib/funding-scout-watchdog"
STATE_FILE="$STATE_DIR/last_state"
mkdir -p "$STATE_DIR"

PSQL_DB="${FUNDING_SCOUT_DB_NAME:-funding_scout}"
WEB_URL="${FUNDING_SCOUT_WEB_URL:-http://127.0.0.1:8050}"
SNAPSHOT_MAX_AGE=4200   # 70 minutes
VENUE_MAX_AGE=4200       # 70 minutes per venue

# psql wrapper. Используем peer-auth через postgres-юзера — не нужен пароль
# в watchdog.env. Watchdog запускается systemd как root, sudo -u postgres работает без TTY.
psql_q() {
    sudo -u postgres psql -d "$PSQL_DB" -t -A -q "$@" 2>&1
}

fail_reasons=()

# 1. Postgres reachable?
if ! psql_q -c "SELECT 1" >/dev/null 2>&1; then
    fail_reasons+=("postgres unreachable as $PSQL_USER@$PSQL_DB")
else
    # 2. Snapshot freshness
    LAST_TS=$(psql_q -c "SELECT COALESCE(MAX(ts), 0) FROM funding_snapshot" 2>/dev/null)
    if [ -z "$LAST_TS" ] || [ "$LAST_TS" = "0" ]; then
        fail_reasons+=("no snapshots in DB yet")
    else
        NOW_TS=$(date +%s)
        AGE=$((NOW_TS - LAST_TS))
        if [ "$AGE" -gt "$SNAPSHOT_MAX_AGE" ]; then
            fail_reasons+=("snapshot stale: ${AGE}s old (max ${SNAPSHOT_MAX_AGE}s)")
        fi

        # 4. Per-venue freshness
        STALE_VENUES=$(psql_q -c "SELECT venue FROM funding_snapshot GROUP BY venue HAVING MAX(ts) < $((NOW_TS - VENUE_MAX_AGE))" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
        if [ -n "$STALE_VENUES" ]; then
            fail_reasons+=("venues stale: $STALE_VENUES")
        fi
    fi
fi

# 3. Web UI alive?
if ! curl -fs -o /dev/null --max-time 5 "$WEB_URL/_dash-layout"; then
    fail_reasons+=("web UI not responding at $WEB_URL")
fi

# Compute new state
if [ "${#fail_reasons[@]}" -eq 0 ]; then
    NEW_STATE="OK"
else
    NEW_STATE="FAIL"
fi

OLD_STATE="$(cat "$STATE_FILE" 2>/dev/null || echo NEW)"
echo "$NEW_STATE" > "$STATE_FILE"

# Send Telegram only on state change
if [ "$NEW_STATE" = "$OLD_STATE" ]; then
    exit 0
fi

if [ -z "${FUNDING_SCOUT_TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${FUNDING_SCOUT_TELEGRAM_CHAT_ID:-}" ]; then
    # Креды не настроены — лог и выход. Не падаем.
    echo "[watchdog] state $OLD_STATE -> $NEW_STATE but no Telegram creds, skip alert"
    exit 0
fi

if [ "$NEW_STATE" = "FAIL" ]; then
    REASONS_TEXT=$(printf '%s\n' "${fail_reasons[@]}" | sed 's/^/• /')
    MSG="<b>🔴 funding-scout DOWN</b>
$(date -u +%Y-%m-%dT%H:%M:%SZ)

${REASONS_TEXT}"
else
    MSG="<b>✅ funding-scout recovered</b>
$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi

curl -fs --max-time 10 \
    -X POST "https://api.telegram.org/bot${FUNDING_SCOUT_TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${FUNDING_SCOUT_TELEGRAM_CHAT_ID}" \
    -d "parse_mode=HTML" \
    -d "disable_web_page_preview=true" \
    --data-urlencode "text=${MSG}" \
    > /dev/null || echo "[watchdog] telegram send failed"

exit 0
