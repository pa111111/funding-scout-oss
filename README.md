# funding-scout

DEX-only funding-rate arbitrage scout. EV-калькулятор связок, не сканер ставок.

Большинство funding-сканеров ранжируют сетапы по голой ставке. funding-scout считает **полный EV** связки — учитывая round-trip cost, spread на входе/выходе, ожидаемое время удержания и friction tax на переброску капитала. См. [`docs/concept.md`](docs/concept.md) для парадигмы и [`docs/strategies.md`](docs/strategies.md) для таксономии 6 типов связок.

## Stack

- Python 3.12, venv + pip
- SQLAlchemy 2 + SQLite (локально) / Postgres (прод) через `DATABASE_URL`
- httpx async для коннекторов
- Dash + dash-ag-grid + Plotly для UI

Подробнее — [`docs/stack.md`](docs/stack.md).

## Поддерживаемые DEX

| Venue | Status | Особенности |
|---|---|---|
| Hyperliquid | ✅ | maker 0.015% / taker 0.045%, hourly funding |
| Lighter | ✅ | 0% fees на free-tier, equity-перпы, funding clamp ±0.5%/h |
| Pacifica | ✅ | широкий equity+commodity набор, hourly funding |
| EdgeX | ⚠️ | широкий equity, нет bulk endpoint (N параллельных запросов) |

Список расширяется. См. [`docs/exchanges.md`](docs/exchanges.md).

## Setup

```bash
# Linux / macOS
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

```powershell
# Windows
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

## Use

```bash
funding-scout init                  # создать схему БД
funding-scout snapshot              # снять один снапшот со всех коннекторов
funding-scout snapshot --loop 3600  # цикл с интервалом в секундах (для systemd)
funding-scout scan                  # топ-10 связок (cross-DEX same-ticker) в консоль
funding-scout web --host 0.0.0.0 --port 8050    # Dash UI с фильтрами и сортировкой
funding-scout status                # счётчики по venues в БД
```

## Configuration

Env vars (можно через `.env` файл в корне):

| Var               | Default                              | Notes                          |
|-------------------|--------------------------------------|--------------------------------|
| `DATABASE_URL`    | `sqlite:///data/funding-scout.db`    | postgres URL для прода         |
| `LOG_LEVEL`       | `INFO`                               | `DEBUG` для трассировки запросов |
| `HYPERLIQUID_API` | `https://api.hyperliquid.xyz`        | переопределить при необходимости |

Telegram-нотификации опциональны: `FUNDING_SCOUT_TELEGRAM_BOT_TOKEN` + `FUNDING_SCOUT_TELEGRAM_CHAT_ID`.

## Layout

```
src/funding_scout/
├── connectors/      # DEX-адаптеры (Hyperliquid, Lighter, Pacifica, EdgeX)
├── detectors/       # генераторы сетапов (cross-DEX same-ticker)
├── ev/              # EV-арифметика + cost model
├── storage/         # SQLAlchemy models + engine
├── snapshot/        # snapshot loop runner
├── notify/          # Telegram
├── web/             # Dash UI
├── reporting.py     # daily-report
├── config.py        # env-driven settings
└── cli.py           # CLI entry point
docs/                # framework и таксономия
deploy/systemd/      # reference systemd-юниты (paths под /root/funding-scout — adjust)
scripts/             # watchdog.sh
tests/               # pytest, 119 mocked + 5 E2E (gated FUNDING_SCOUT_E2E=1)
```

## License

MIT — см. [LICENSE](LICENSE).
