# DEPLOY.md

VPS-инфраструктура и команды для funding-scout. По образцу hedge-bot, но
полностью изолированный stack — отдельная БД, отдельный Telegram, отдельные systemd-юниты.

## VPS — где живёт

Делим железо с hedge-bot. **Provider/host/SSH/recovery идентичны** — см. `hedge-bot/DEPLOY.md`
секции "VPS — инфраструктура" и "Как попасть на VPS". Здесь только то что специфично для funding-scout.

| Что | Где |
|---|---|
| **Project root** | `/root/funding-scout/` |
| **Venv** | `/root/funding-scout/.venv/` (Python 3.12 системный) |
| **Логи** | `journalctl -u funding-scout-snapshot` (и аналогично остальные) |
| **БД** | Postgres 16 (тот же инстанс что у hedge-bot), database `funding_scout`, user `funding_scout` |
| **Web UI** | `http://100.124.168.63:8050` (Tailscale-only, UFW наружу закрыт) |
| **Telegram** | отдельный бот от hedge-bot, см. ниже |

## Что развёрнуто

| Юнит | Тип | Что делает |
|------|-----|------------|
| `funding-scout-snapshot.service` | long-running | `funding-scout snapshot --loop 3600` — фетчит DEX → пишет в Postgres |
| `funding-scout-web.service` | long-running | Dash UI на `100.124.168.63:8050` |
| `funding-scout-watchdog.service` + `.timer` | oneshot/раз в минуту | Проверяет snapshot freshness / web alive / postgres / per-venue. Telegram alert при смене состояния. |
| `funding-scout-daily-report.service` + `.timer` | oneshot/09:00 UTC | Топ-10 связок в Telegram |

Все юниты — `MemoryMax=512M` + `CPUQuota=50%`, реально едят <300 MB на двоих и <1% CPU.

## First-time install (на чистом VPS)

```bash
ssh root@100.124.168.63

# 1. Клон репо (deploy-key уже настроен от hedge-bot, можно переиспользовать или сделать отдельный)
cd /root
git clone git@github.com:pa111111/funding-scout.git
cd funding-scout

# 2. Venv + deps
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

# 3. Postgres database + user (через postgres-юзера)
sudo -u postgres psql <<'EOF'
CREATE USER funding_scout WITH PASSWORD 'CHANGEME_STRONG_PASSWORD';
CREATE DATABASE funding_scout OWNER funding_scout;
EOF

# Проверить что funding_scout user может коннектиться по tailnet (если нужно с лаптопа):
# Добавить в /etc/postgresql/16/main/pg_hba.conf:
#   host  funding_scout  funding_scout  100.0.0.0/8  scram-sha-256
# И systemctl reload postgresql

# 4. .env с секретами (НЕ в git!)
cp .env.example .env
chmod 600 .env
nano .env
# заполни:
#   DATABASE_URL=postgresql+psycopg://funding_scout:CHANGEME_STRONG_PASSWORD@localhost:5432/funding_scout
#   FUNDING_SCOUT_TELEGRAM_BOT_TOKEN=<новый бот>
#   FUNDING_SCOUT_TELEGRAM_CHAT_ID=<новый чат>

# 5. Создаём схему БД
.venv/bin/funding-scout init

# 6. Watchdog креды (только токен и chat_id, для systemd-юнита watchdog'а)
sudo tee /etc/funding-scout-watchdog.env > /dev/null <<EOF
FUNDING_SCOUT_TELEGRAM_BOT_TOKEN=<тот же токен>
FUNDING_SCOUT_TELEGRAM_CHAT_ID=<тот же chat_id>
FUNDING_SCOUT_DB_USER=funding_scout
FUNDING_SCOUT_DB_PASSWORD=<тот же пароль что в .env>
FUNDING_SCOUT_DB_NAME=funding_scout
FUNDING_SCOUT_WEB_URL=http://100.124.168.63:8050
EOF
sudo chmod 600 /etc/funding-scout-watchdog.env
sudo mkdir -p /var/lib/funding-scout-watchdog

# 7. Watchdog скрипт executable
chmod +x /root/funding-scout/scripts/watchdog.sh

# 8. Systemd units
sudo cp deploy/systemd/funding-scout-*.service deploy/systemd/funding-scout-*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# 9. Первый прогон snapshot вручную, чтобы убедиться что всё ок
.venv/bin/funding-scout snapshot
.venv/bin/funding-scout status

# 10. Включаем юниты
sudo systemctl enable --now funding-scout-snapshot.service
sudo systemctl enable --now funding-scout-web.service
sudo systemctl enable --now funding-scout-watchdog.timer
sudo systemctl enable --now funding-scout-daily-report.timer

# 11. Проверь что всё живо
systemctl status funding-scout-snapshot
systemctl status funding-scout-web
systemctl list-timers | grep funding-scout
curl -s http://100.124.168.63:8050/_dash-layout | head -c 200
```

## Update (git pull + restart)

```bash
ssh root@100.124.168.63
cd /root/funding-scout
git pull
.venv/bin/pip install -e .       # если менялись зависимости
sudo systemctl restart funding-scout-snapshot funding-scout-web
# Если менялись systemd-юниты:
sudo cp deploy/systemd/funding-scout-*.service deploy/systemd/funding-scout-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

## Доступ к UI с лаптопа

С запущенным Tailscale-клиентом просто открой:

```
http://100.124.168.63:8050
```

Нет SSL — это Tailscale-only сервис, weak зашифрован VPN'ом и UFW его наружу не пускает.

Если **наружу** хочешь когда-нибудь — придётся nginx + Let's Encrypt + UFW open 443.
В v0.1 этого не делаем, парадигма "internal product".

## Команды диагностики

```bash
# Логи snapshot (последние 100 строк)
journalctl -u funding-scout-snapshot -n 100 --no-pager

# Live-логи snapshot
journalctl -u funding-scout-snapshot -f

# Статус всех юнитов одной командой
systemctl status 'funding-scout-*' --no-pager

# Когда был последний прогон watchdog'а
systemctl list-timers funding-scout-watchdog.timer

# Состояние БД
sudo -u postgres psql funding_scout -c "
SELECT venue, COUNT(*) AS rows, COUNT(DISTINCT ticker) AS tickers, MAX(ts) AS last_ts,
       NOW() - to_timestamp(MAX(ts)) AS age
FROM funding_snapshot GROUP BY venue;
"

# Размер БД
sudo -u postgres psql -c "SELECT pg_size_pretty(pg_database_size('funding_scout'));"

# Топ-10 связок прямо с VPS (без UI)
cd /root/funding-scout && .venv/bin/funding-scout scan
```

## Telegram — настройка нового бота

**Не используй hedge-bot токен!** Изоляция важна — иначе сигналы перекрываются.

1. В Telegram открой [@BotFather](https://t.me/BotFather)
2. `/newbot` → имя на выбор (например `funding_scout_alerts_bot`)
3. Получи токен типа `1234567890:AAA...`
4. Создай **новый** чат для funding-scout (личный или группа). Напиши там что-то.
5. Узнай `chat_id`:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
   В JSON найди `"chat":{"id":...}`.
6. Прямо проверь работу с лаптопа:
   ```bash
   curl -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
        -d "chat_id=<CHAT_ID>" -d "text=funding-scout test"
   ```
7. Закидай в `.env` и `/etc/funding-scout-watchdog.env` оба значения.

## Что мониторит watchdog (раз в минуту)

| Проверка | Условие FAIL |
|---|---|
| Postgres достижим как `funding_scout@funding_scout` | `psql` падает |
| Snapshot не старше 70 минут | `MAX(ts) < now() - 70min` |
| Per-venue freshness | какой-то venue не фетчился >70 минут |
| Web UI отвечает | `curl /_dash-layout` !=200 |

Алерт **только при смене состояния** (OK→FAIL и FAIL→OK), state в
`/var/lib/funding-scout-watchdog/last_state`. Не спамит.

Disk full **уже мониторит hedge-bot watchdog** — здесь не дублируем.

## Recovery

| Симптом | Что делать |
|---|---|
| `journalctl -u funding-scout-snapshot` показывает 5xx от HL/Lighter | Это редко, обычно сами поднимаются. Если >1h — смотреть `curl https://api.hyperliquid.xyz/info` напрямую. |
| Watchdog шлёт "snapshot stale" | `systemctl restart funding-scout-snapshot`. Если повторяется — смотреть журнал на ошибки парсинга (биржа сменила формат API). |
| Web 502/connection refused | `systemctl restart funding-scout-web`. Если процесс падает — `journalctl -u funding-scout-web -n 200`. |
| БД переполнила диск | пока далеко: 500 MB/год для 2 venues. Когда подойдёт — добавить retention в Alembic-миграцию (DELETE WHERE ts < now() - 1y). |
| Postgres не стартует | проблема общая с hedge-bot — см. `hedge-bot/DEPLOY.md`. |

## Куда пойти если совсем сломалось

- VNC через AdminVPS панель (см. `hedge-bot/DEPLOY.md` секция "Recovery")
- Снести всё `funding-scout-*` и переинсталлить — данные в Postgres переживут (или не переживут — backup БД важнее, см. ниже)

## Backup

В v0.1 backup'а нет. Когда наберётся месяц истории — добавим pg_dump раз в сутки в `/var/backups/funding-scout/` с ротацией 7 дней. Пока тренируемся, потери не критичны.
