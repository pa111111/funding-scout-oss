---
name: MVP v0.1 — что построено и что нет
description: Точный snapshot реализации funding-scout по состоянию на 2026-05-11. Чтобы новая сессия не тратила время выясняя что есть в коде.
type: project
originSessionId: 72eda0b6-1cd7-4258-885e-893ef3d005a7
---
## Что построено и работает на проде

| Слой | Что есть | Файлы |
|---|---|---|
| **Storage** | SQLAlchemy 2 + SQLite/Postgres через `DATABASE_URL`. Schema `funding_snapshot` с composite PK (ts, venue, ticker), JSON-поле `raw` для бэкфила. Dialect-aware UPSERT. | `src/funding_scout/storage/` |
| **Коннекторы (4)** | HyperliquidConnector ✓, LighterConnector ✓, PacificaConnector ✓, EdgeXConnector ⚠️ (intermittent due to региональных блокировок) | `src/funding_scout/connectors/` |
| **Снапшот-loop** | `funding-scout snapshot --loop 3600` через systemd. Параллельный fetch, изоляция per-connector (падение одного не валит остальных). | `src/funding_scout/snapshot/runner.py` |
| **Cross-DEX same-ticker детектор** | Берёт latest snapshot, группирует по тикеру, для каждой пары venues создаёт Setup. Long-нога = меньше funding rate. | `src/funding_scout/detectors/cross_dex_same_ticker.py` |
| **EV-движок** | base EV, round-trip cost per venue (HL 0.06%, Lighter 0%, остальные 0.10% default), min_profitable_hours. **Без штрафа за риск в EV** (парадигма). | `src/funding_scout/ev/` |
| **Web UI** | Dash + dash-ag-grid с sortable/filterable таблицей сетапов. Bind на Tailscale IP `100.124.168.63:8050`. 13 колонок включая spread APR, EV $/day, min hold, vol $M. | `src/funding_scout/web/` |
| **Telegram notifier + daily-report** | Bot `@funding_scout_bot` (отдельный от hedge-bot, общий chat_id). Daily report 09:00 UTC топ-10 связок. | `src/funding_scout/notify/`, `src/funding_scout/reporting.py` |
| **Watchdog** | systemd timer раз в минуту, 4 проверки (Postgres достижим, snapshot freshness, per-venue freshness, web alive). Telegram алерт только при смене состояния. | `scripts/watchdog.sh` |
| **CLI** | 6 команд: `init`, `snapshot`, `scan`, `web`, `status`, `daily-report` | `src/funding_scout/cli.py` |
| **Тесты** | 119 mocked зелёных + 5 E2E gated (env `FUNDING_SCOUT_E2E=1`). Покрытие: каждый коннектор отдельно, runner с моками, storage, CLI через CliRunner, EV арифметика, детектор, web data layer, telegram, reporting. | `tests/` |

## Состояние БД на проде на 2026-05-08 12:47 UTC (последняя точка которую я лично видел)

```
edgex       7 snapshots / 367 строк (intermittent)
hyperliquid 84 snapshots / 15 428 строк
lighter     84 snapshots / 13 285 строк
pacifica    8 snapshots / 520 строк
TOTAL       ~30k строк, ~12 MB
```

База копит автоматически — за день +24 snapshots × ~462 ticks = ~11k строк/день. Год: ~4 GB. Не Big Data, SQLite/Postgres справится без проблем.

## Что **намеренно не сделано** в v0.1

Это **не** "забыли" — это сознательные отказы:

- **Auto-execution / order routing / приватные ключи** — manual flow по решению пользователя, $5k на связку (см. execution_model.md). Возможно когда-то в v0.5+.
- **Honeypot-фильтр по умолчанию** — парадигма transparent risk disclosure (product_concept.md). Пользователь сам фильтрует через UI.
- **Sniping детектор (type 4)** — руками не успеть за минуты, требует executor. Тип сохранён в таксономии для контекста.
- **CEX интеграция** — DEX-only намеренно.

## Что **в roadmap но не построено** (по приоритету для будущей сессии)

| Приоритет | Фича | Зачем | Estimate |
|---|---|---|---|
| 🔴 высокий | **Stability column + chart drill-down** | На реальных данных видно что cross-DEX same-ticker НЕ стабильный carry, а windows 4-12h (см. data_observations.md). Без этой колонки пользователь не отличит. | 2-3 часа |
| 🔴 высокий | **Capital input + slippage column** | Текущий UI не учитывает что юзер может зайти с $1000 а не $5000 — слиппедж пропорционален размеру. Правильная формула: `slippage % ≈ order_size / hourly_volume × k`. | 2 часа |
| 🟡 средний | **Backfill HL fundingHistory** | HL отдаёт `/info {"type":"fundingHistory"}` за месяцы вглубь. Один проход и у нас 3 месяца HL-данных для backtest. Lighter — надо проверить, есть ли историч endpoint. | 1 час |
| 🟡 средний | **Tier 2 коннекторы (Paradex, Drift)** | Расширяет triangulation, больше equity-проверок. См. exchange_universe.md. | 2 часа на двоих |
| 🟡 средний | **Auto-refresh страницы Dash** через `dcc.Interval` каждую минуту | Удобство, не нажимать F5. | 30 мин |
| 🟢 низкий | **GitHub приватное репо** `pa111111/funding-scout` | Заменить ручной `scp` на `git pull` для обновлений. | 30 мин |
| 🟢 низкий | **Postmortem ledger** — журнал сделок expected vs actual | Учить пользователя на своих данных. Нужно через 2-4 недели после реального использования. | 4-6 часов |
| 🟢 низкий | **Backtest engine** | После HL backfill можно гонять стратегии на 3 месяцах HL-данных. Зависит от Capital+slippage и Stability. | 4-6 часов |
| 🟢 низкий | **Real β/σ/ADL метрики** | Сейчас в risk_framework.md описаны как концепции. Нужна неделя+ истории для нормальной регрессии. | 4-8 часов |

## Хвост от деплоя (мелочи)

- **Sync hook memory→docs** — скрипт `scripts/sync-memory-to-docs.sh` написан, но в `.claude/settings.local.json` не зарегистрирован. Pipe-тестировал — работает на forward-slash путях. После каждого edit'а памяти приходится делать `cp` руками. Доделать когда не лень: настроить PostToolUse hook.
- **Telegram chat_id** общий с hedge-bot (читается из `/etc/hedge-watchdog.env`). Если когда-то надоест шум — создать отдельный чат.
- **EdgeX rate-bann риск** — при интенсивном E2E с лаптопа можно поймать 403 на час-два. На VPS работает стабильно с частичным failure на equity (regional block).

## How to apply

Когда новая сессия Claude спросит "что у нас есть в проде" — посмотреть сюда первым делом, потом `D:/Projects/funding-scout/DEPLOY.md` и `D:/Projects/funding-scout/docs/MEMORY.md`. Не переоткрывать решения из этого списка без явной причины.
