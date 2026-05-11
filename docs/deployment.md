---
name: deployment topology — VPS, Tailscale, systemd, Telegram
description: Где живёт продакшен funding-scout, какие сервисы крутятся, как туда попадать, как обновлять. Состояние на 2026-05-05.
type: reference
originSessionId: 72eda0b6-1cd7-4258-885e-893ef3d005a7
---
## VPS — общий с hedge-bot

| Параметр | Значение |
|---|---|
| Provider | AdminVPS Micro |
| OS | Ubuntu 24.04 LTS |
| Spec | 2 vCPU, 4 GB RAM, 30 GB NVMe, Германия |
| Tailscale IP | `100.124.168.63` (основной путь) |
| Public IP | `212.192.22.65` (UFW закрыт, не используется) |
| Hostname | `192439.com` (внутреннее AdminVPS) / `192439` (Tailscale) |

**Делим железо с hedge-bot.** Provider/SSH/recovery идентичны hedge-bot — см. `D:/OneDrive/Projects/hedge-bot/DEPLOY.md` секции "VPS — инфраструктура" и "Как попасть на VPS". Здесь — только то что специфично для funding-scout.

## Где что лежит на VPS

```
/root/funding-scout/                            ← project root (sudo не нужен, всё под root)
├── .env                                         ← секреты (DATABASE_URL, Telegram). 600 perms.
├── .venv/                                       ← Python 3.12 venv с зависимостями
├── data/                                        ← пусто (БД в Postgres, не в SQLite)
├── deploy/systemd/                              ← reference-копии systemd-юнитов
├── scripts/watchdog.sh                          ← shell-watchdog (см. ниже)
├── src/funding_scout/                           ← код приложения
└── ... (остальное как в репо)

/etc/systemd/system/funding-scout-*              ← active systemd unit-файлы (5 штук)
/etc/funding-scout-watchdog.env                  ← TELEGRAM_BOT_TOKEN + CHAT_ID для watchdog'а. 600.
/var/lib/funding-scout-watchdog/last_state       ← антиспам state (OK/FAIL)
```

Postgres: общий инстанс с hedge-bot, отдельная database `funding_scout` + user `funding_scout`.
Listen на `localhost,100.124.168.63` (как у hedge-bot, унаследовано).
`pg_hba.conf` имеет дефолтную строку `host all all 127.0.0.1/32 scram-sha-256` — этого достаточно
для локальных коннектов из snapshot/web сервисов.

## Сервисы (systemd)

| Юнит | Тип | Что делает |
|---|---|---|
| `funding-scout-snapshot.service` | long-running | `funding-scout snapshot --loop 3600` — фетчит DEX → Postgres |
| `funding-scout-web.service` | long-running | Dash UI, bind на `100.124.168.63:8050` (Tailscale-only) |
| `funding-scout-watchdog.service` + `.timer` | oneshot/раз в минуту | 4 проверки + Telegram при смене состояния |
| `funding-scout-daily-report.service` + `.timer` | oneshot/09:00 UTC | топ-10 связок в Telegram |

Лимиты: `MemoryMax=512M` + `CPUQuota=50%` на каждом long-running. Реальное потребление — ~57 MB на snapshot и ~68 MB на web.

## Доступ к UI

```
http://100.124.168.63:8050
```

С лаптопа через Tailscale (если Tailscale-клиент запущен). Не нужен SSL — UFW блокирует наружу, Tailscale сам шифрует.

## Telegram изоляция

Намеренно **отдельный бот от hedge-bot**, но **тот же чат** (chat_id переиспользован).

| | hedge-bot | funding-scout |
|---|---|---|
| Bot | старый бот для hedge-bot | `@funding_scout_bot` (id 8796911306) |
| Chat ID | один и тот же chat_id (берётся из `/etc/hedge-watchdog.env`) | тот же |
| Watchdog env file | `/etc/hedge-watchdog.env` | `/etc/funding-scout-watchdog.env` |
| Daily report | не отправляет | отправляет в 09:00 UTC |

**Изоляция через bot identity** — каждого бота можно мутить отдельно в Telegram. Чат общий чтобы не размывать внимание на 2 канала. Если когда-то надоест шум — легко создать отдельный чат и поменять `FUNDING_SCOUT_TELEGRAM_CHAT_ID` в обоих env-файлах.

**Watchdog логика** (`scripts/watchdog.sh`):
1. Postgres достижим как peer-auth `sudo -u postgres psql -d funding_scout`
2. Snapshot не старше 70 минут (`SELECT MAX(ts) FROM funding_snapshot`)
3. Per-venue freshness — каждый venue фетчился последний час
4. Web UI отвечает на `/_dash-layout`

Алерт **только при смене состояния** OK→FAIL и FAIL→OK, state в `/var/lib/funding-scout-watchdog/last_state`. Не флудит. Disk full **уже мониторит hedge-bot watchdog** — здесь не дублируем.

## Как обновлять (на 2026-05-05 — ручной флоу)

```bash
# с лаптопа
cd D:/Projects/funding-scout
tar --exclude='./.venv' --exclude='./data/*.db*' --exclude='./.env' \
    --exclude='./__pycache__' --exclude='./.pytest_cache' \
    -czf /tmp/funding-scout.tar.gz .
scp /tmp/funding-scout.tar.gz root@100.124.168.63:/tmp/

# на VPS
ssh root@100.124.168.63
cd /root/funding-scout
tar -xzf /tmp/funding-scout.tar.gz
.venv/bin/pip install -e .       # если менялись deps
systemctl restart funding-scout-snapshot funding-scout-web
# если менялись systemd-юниты:
cp deploy/systemd/funding-scout-*.service deploy/systemd/funding-scout-*.timer /etc/systemd/system/
systemctl daemon-reload
```

**TODO:** перевести на git pull через приватный `pa111111/funding-scout` (как у hedge-bot). Сейчас живём на rsync. Это в коротком списке next steps.

## Креды (запиши в KeePass, не коммить!)

| Что | Где |
|---|---|
| DB user / password | `funding_scout` / `7e023d3f5ce0f13be2767390689f3a3f` (live на 2026-05-05) |
| Telegram bot token | `8796911306:AAE-PlJlouTW01TPhF_5fngBHr9-hCgz69U` (live на 2026-05-05) |

Если креды утекут — `ALTER USER funding_scout WITH PASSWORD '...'` + перезаписать `/root/funding-scout/.env`. Telegram токен — пересоздать через @BotFather и обновить в обоих env-файлах.

## How to apply

При проблемах с прод-инстансом или вопросах "где это живёт" — первым делом сюда. Команды диагностики и recovery — в `D:/Projects/funding-scout/DEPLOY.md` (полная версия с сниппетами `journalctl`/`psql`/`systemctl`).
