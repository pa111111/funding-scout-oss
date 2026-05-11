---
name: exchanges relevant to funding-scout
description: DEX-only list of perp venues to integrate into funding-scout (CEX explicitly out of scope per product_concept.md). Notes on equity perps, wrappers, and unusual funding intervals. Snapshot from posts dated Nov 2025 — Mar 2026; volatile, must re-verify before integration.
type: reference
originSessionId: 72eda0b6-1cd7-4258-885e-893ef3d005a7
---
Список собран из постов @everybodycandoit и страницы fundoor.pro (по состоянию на 2026-05-02). Этот список **протухает быстро** — новые DEX появляются раз в 2–4 недели; перед интеграцией обязательно проверять текущий статус площадки и наличие публичного API.

**Скоуп: только DEX.** CEX (Binance, OKX, Bybit, Gate, KuCoin, Coinex, Bitget и т.д.) — out of scope per product_concept.md. fundoor.pro покрывает 14 CEX, мы намеренно нет.

## Integrated venues (по состоянию на 2026-05-08)

| Venue | API base | Endpoint | Тикеров | Особенности |
|---|---|---|---|---|
| **hyperliquid** ✅ | `api.hyperliquid.xyz` | POST `/info` `metaAndAssetCtxs` | ~183-191 | maker 0.015% / taker 0.045%; pre-market manipulable; OI без разделения long/short |
| **lighter** ✅ | `mainnet.zklighter.elliot.ai` | `/api/v1/orderBookDetails` + `/api/v1/funding-rates` | ~156-160 | 0% maker и taker; есть equity-перпы (NVDA/TSLA/PLTR/HOOD); funding clamp ±0.5%/h |
| **pacifica** ✅ | `api.pacifica.fi` | GET `/api/v1/info/prices` | ~65 | один endpoint всё-в-одном; **широкий equity + commodity набор** (NVDA/TSLA/AAPL/HOOD + XAU/XAG/SP500/GOOGL/AMZN/CRCL/EURUSD); полное покрытие нашей weekend-equity стратегии после Lighter |
| **edgex** ⚠️ | `pro.edgex.exchange` | GET `/getMetaData` + N× GET `/getTicker?contractId=X` | ~54-73 (зависит от блокировок) | **Нет bulk endpoint** — нужно 73 параллельных запроса с MAX_CONCURRENT=10; **региональная блокировка equity** (403 на NVDA/TSLA/AAPL даже из Германии); **rate-bann** при интенсивном тестировании (полные 403 на час-два); самый широкий equity-набор когда работает |

**Lighter funding-rates бонус:** их endpoint возвращает ставки **четырёх бирж сразу** (binance/bybit/hyperliquid/lighter). Можно использовать как sanity-check против собственного HL коннектора, или как proxy на CEX-ставки если когда-то отойдём от DEX-only (нет в скоупе сейчас).

## Известные DEX, не интегрированные

**DEX-перпы (~20 на fundoor):** Hyperliquid ✅, Drift, Orderly, Apex, Lighter ✅, Paradex, Pacifica ✅, Backpack, EdgeX ✅, GRVT, Aster, Carbon, Variational + остальные.

**DEX со spot+perp на одном venue** (для type-6 cash-and-carry): **Hyperliquid (HyperCore)** — основной кандидат. Остальные DEX обычно либо только perp, либо spot и perp в разных протоколах. Расширять список по мере появления интегрированных venues.

**Equity-perp capable** (важно для weekend-стратегии — PLTR, NVDA, TSLA): **Lighter, Pacifica, EdgeX**. Это узкий список — не каждый DEX поддерживает акции. Расширять при появлении новых.

**Wrapper-биржи** (одна ликвидность, разный фронт/funding): **Based.one** = обёртка над Hyperliquid. Полезно для умножения аллокации в single-venue correlated pair.

**Биржи с не-часовым funding interval** (для sniping): **Carbon** упомянут как площадка с расчётом не каждый час → "поле для творчества". Перед использованием каждой новой биржи нужно записывать её funding interval в data-model.

**Известные риски площадок:**
- **Lighter** — жалобы на "напрягается счас" (производительность), миграции пользователей в конце декабря 2025. Withdraw: slow Ethereum free / fast Arbitrum $3. Maker и taker fee = 0% на free-tier (важно для cost model).
- **Hyperliquid** — maker 0.015%, taker 0.045%. Drag-to-modify limit-ордера работает нестабильно. Funding interval 1h. На pre-market оракул = "спрос/предложение на самом HL" → manipulable (кейс Plasma).
- **Backpack** — execution эмпирически плохой: "вроде комиссии те же что HL, но позиция выходит дороже". Никита бросил торговать. **Низкий приоритет в каталоге.**
- **Astиум** — taker 0.1%, рекомендован к избеганию.
- **Hibachi** — RAA-stage proект, дырявые стаканы, маленькие плечи (например 3x на ряде токенов делают капитал неэффективным). Авторы канала фармят сейчас слабо.
- **Carbon, Variational, Paradex, Pacifica** — относительно свежие, низкие/нулевые комиссии, но малая ёмкость и agressive OI лимиты.
- **OI limits** на новых DEX (Variational на NMR) — частая причина невозможности зайти в связку.
- **Pre-market на любом DEX (Lighter MONAT, Hyperliquid Plazma)** — manipulable oracle, в каталоге продукта помечать как "do not trade" даже при высоком APR.
- Bybit-эпизод (KYC source-of-funds freeze автора канала) сохранён только как объяснение, **почему мы выбрали DEX-only**, а не как риск площадки в скоупе.

**How to apply:** при добавлении новой DEX в funding-scout фиксировать в её записи: тип (DEX/wrapper/spot+perp-integrated), funding interval, equity-perp support, наличие публичного API/WebSocket для funding rates, известные OI ограничения, риски (изолированная маржа, оракул-зависимости, контракт-аудиты, депозиты только в нативной сети).
