# funding-scout

DEX-only funding-rate arbitrage scout. EV-калькулятор связок, не сканер ставок.

Контекст и принципы — см. `docs/product_concept.md` и `docs/MEMORY.md`.

## Stack

- Python 3.12, venv + pip (или uv)
- SQLAlchemy 2 + SQLite (локально) / Postgres (VPS) через `DATABASE_URL`
- httpx async для коннекторов
- Dash + dash-ag-grid + Plotly для UI (придёт в v0.2)

## Setup (Windows)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

## Setup (Linux / VPS)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Use

```bash
funding-scout init                  # создать схему БД
funding-scout snapshot              # снять один снапшот со всех коннекторов
funding-scout snapshot --loop 3600  # цикл с интервалом в секундах (для systemd)
```

## Configuration

Env vars (можно через `.env` файл в корне):

| Var               | Default                              | Notes                          |
|-------------------|--------------------------------------|--------------------------------|
| `DATABASE_URL`    | `sqlite:///data/funding-scout.db`    | postgres URL для VPS           |
| `LOG_LEVEL`       | `INFO`                               | `DEBUG` для трассировки запросов |
| `HYPERLIQUID_API` | `https://api.hyperliquid.xyz`        | переопределить при необходимости |

## Layout

```
src/funding_scout/
├── connectors/      # DEX-адаптеры (Hyperliquid, Lighter, ...)
├── storage/         # SQLAlchemy models + engine
├── snapshot/        # snapshot loop runner
├── config.py        # env-driven settings
└── cli.py           # CLI entry point
data/                # SQLite (gitignored)
docs/                # зеркало системной памяти Claude
scripts/             # вспомогательные shell-скрипты
```
