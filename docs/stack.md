---
name: tech stack — что выбрано и почему
description: Стек funding-scout в актуальном состоянии — Python 3.12 vanilla + Dash + dash-ag-grid + Postgres/SQLite через SQLAlchemy + httpx + systemd. Зафиксировано после деплоя v0.1 на VPS 2026-05-05.
type: project
originSessionId: 72eda0b6-1cd7-4258-885e-893ef3d005a7
---
## Стек

| Слой | Выбор | Почему именно это |
|---|---|---|
| Язык | **Python 3.12** vanilla (НЕ Anaconda) | стандарт индустрии для квант/funding-arb (pandas/numpy/statsmodels), легко деплоится на любой Linux VPS через `python3.12 -m venv` |
| Зависимости | **pip + venv** (не uv пока) | uv был бы быстрее, но pip уже стоит и работает на VPS, на стороне юзера тоже |
| ORM / DB layer | **SQLAlchemy 2.0** | dialect-aware UPSERT через `sqlite_insert` / `pg_insert`, один код работает с SQLite (локально) и Postgres (VPS) через env-var `DATABASE_URL` |
| Локальная БД | **SQLite** в `data/funding-scout.db` | нулевая инфра, сразу работает |
| Прод БД | **Postgres 16** | ставится отдельная database `funding_scout` + user `funding_scout` в общий postgres-инстанс с hedge-bot (peer-auth для админ-задач, scram-sha-256 для приложения) |
| HTTP client | **httpx 0.28** | async-нативный, MockTransport для unit-тестов, уже работает с двумя коннекторами |
| Конфиг | **pydantic-settings** | env-driven, `.env` файл, типизированно, fallback на дефолт |
| Логи | **structlog** | structured json/console, совместим с journald через systemd |
| CLI | **click** | стандарт, autohelp |
| Веб-UI | **Dash 4.1 + dash-ag-grid 35 + dash-bootstrap-components 2** | Streamlit рассмотрен и отклонён: будут multi-page (scan/history/postmortem/capital), drill-down с реалистичной таблицей 50–200 строк с risk-бэйджами, AG-Grid даёт настоящий спредшит — Streamlit `st.dataframe` болезненно. См. подробное обсуждение в session 2026-05-03 |
| Тесты | **pytest + pytest-asyncio + httpx.MockTransport** | 93 теста зелёных, никаких живых API в дефолтном CI |
| Деплой | **systemd unit'ы + tarball через scp** | git-репо ещё не создан, деплой делается ручным rsync — план перевести на git pull когда заведём приватный github |

## Что НЕ выбираем (на будущее, чтобы не возвращаться к спору)

- **Не Anaconda** — heavy, не для продакшена, peer-Anaconda на VPS = боль. Может быть полезна локально для Jupyter но не для продакшена.
- **Не Streamlit** — rerun-everything модель ломается на multi-page + drill-down + complex tables.
- **Не FastAPI** — Dash сам поднимает Flask внутри, для нашего скейла достаточно. FastAPI понадобится если когда-то будем делать публичное REST API для других клиентов.
- **Не Rust/Go** — Python-экосистема для квант-домена сильнее, скорость не bottleneck (раз в час snapshot, 1 сек CPU).
- **Не Streamlit с миграцией позже** — миграция дорогая, лучше сразу Dash.

## How to apply

При вопросах "какой стек выбрать для X" — посмотреть сюда первым делом. Если нужна новая библиотека — проверить что она работает с asyncio (для коннекторов), хорошо мокается (для тестов), и не тащит heavy deps типа pandas в snapshot-loop (pandas нужен будет в analytics/backtester, но не в hot path).
