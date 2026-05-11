---
name: funding-scout product concept
description: Core product idea — EV-calculator for funding-rate arbitrage связки, not just a funding rate scanner. Differentiator vs fundoor.pro.
type: project
originSessionId: 72eda0b6-1cd7-4258-885e-893ef3d005a7
---
`funding-scout` (working dir `D:\Projects\funding-scout`, empty as of 2026-05-02) is intended as a tool for funding-rate arbitrage on crypto perps.

**Скоуп: только DEX-перпы.** CEX (Binance, OKX, Bybit, Gate, KuCoin и т.п.) **вне продукта** — решение пользователя от 2026-05-02. Причины: KYC/source-of-funds freeze риск (тот эпизод с Bybit у автора канала), предпочтение self-custody, и именно на DEX живут самые жирные/недоарбитражённые связки (новые перп-площадки, equity weekend, sniping). При предложениях добавить CEX — напомнить про это решение и обсудить, не изменился ли контекст.

**Внутренний продукт, ручное исполнение, ~$5k на связку.** Решение от 2026-05-02. Импликации:
- Никакого автоисполнения / order routing / приватных ключей в MVP. Продукт = ассистент, который показывает "куда пойти и зачем", решение и руки — за пользователем.
- $5k на связку → ёмкость почти никогда не лимит, можно ранжировать по чистому EV без головной боли с market impact.
- Sniping (type 4) выпадает из MVP — руками за минуты не успеть. Тип сохраняется в таксономии для контекста.
- Главный приоритет MVP: single-venue correlated pair, cash-and-carry на Hyperliquid, equity-weekend cross-DEX. Они либо без переброски капитала, либо окно достаточно широкое чтобы ручной флоу успел.
- Появляется first-class концепт **execution friction** (см. ниже).

**Парадигма: transparent risk disclosure, не paternalistic filter.** Решение от 2026-05-02. Это фундаментальное позиционирование, не косметика:
- Продукт **выводит риски, не фильтрует по ним**. Pre-market, связки с β<0.5, sim_flash_crash_loss=−40% — всё показывается. Ничего не выпадает из листа автоматически на основании риск-метрик.
- EV **не штрафуется за риск внутри числа**. Base EV (spread + carry − round-trip − friction) и risk panel — две независимые оси. Пользователь сам сравнивает со своим personal risk envelope.
- Sim-scenarios (flash-crash 10.10.2025, ADL hit, oracle manipulation, asymmetric close) показываются как отдельные строки с цифрами потерь, не как gates.
- Hard-filters существуют только как **saved user filter profiles** (например "не показывать pre-market" — мой выбор, не дефолт продукта).
- Каждое риск-число несёт **confidence label**: `измерено / оценка / проксировано`. Без этого риск-числа = шум.
- Inline-чек-лист открытия (изолированная маржа, equal contracts, hybrid limit/market, market stops, плечо 2–3x) остаётся — это операционные правила, не риск-фильтр. Если пользователь решил зайти, пусть зайдёт правильно.
- Если в обсуждении возникнет соблазн "давай защитим пользователя от X" — напомнить про эту парадигму и обсудить, действительно ли нужен hard-filter, или лучше label + sortable метрика.

Аналогия: scout = разведчик. Показывает что есть, чем пахнет, какие следы. Решение идти или нет — за оператором.

The core insight that shapes it:

**A funding-rate scanner that only shows top APRs is a commodity (fundoor.pro already does it across CEX+DEX). The actual edge is showing complete trade EV — and делать это только по DEX-вселенной острее, чем смешивать с CEX.**

Each связка должна выводиться как триплет + capacity:
- **Spread на входе** (текущее расхождение цены между биржами / тикерами)
- **Spread на выходе** (ожидаемая mean-revert точка)
- **Funding carry APR** × ожидаемое время удержания
- **Capacity / OI limit** — ёмкость до того, как связка сожрётся MM
- **Тип связки** (см. arbitrage_strategies.md)
- **Min profitable holding period** = round-trip cost / funding APR. Round-trip обычно 0.20–0.28% (вход + выход с комиссиями обеих ног); на funding APR 20% годовых это ~25 периодов 8h-фандинга, на APR 400% — менее одного периода. Это поле сразу отвечает "стоит ли вообще лезть на этот период удержания". Источник чисел round-trip — третьесторонний отчёт от 22.05.2026, числа разумны но требуют поверки реальными комиссиями конкретных бирж.
- **Execution friction tax** — % от ожидаемого EV, который съест мобилизация капитала к нужным venues (bridge fee + время + слиппедж свопа + газ). Если капитал уже на нужных DEX → friction = 0. Под ручной флоу и $5k на связку это ключевая метрика, которая делит топ на "🟢 без переброски" (заходим сразу) и "🟡 с переброской" (стоит ли мобилизация капитала). fundoor и аналоги этого не считают — это и есть differentiator продукта при ручном исполнении.

**Why:** автор fundoor.pro (@everybodycandoit на Telegram) сам в постах считает руками: "Вход +0.61% / Выход −1.00% / Funding +383% APR". Его собственный сервис эту арифметику не делает — это видимый gap. Альфа = время обнаружения связки + правильная оценка EV. Топ-funding без spread-контекста дает много false positives (высокий funding часто компенсируется широким спредом не в твою пользу).

**How to apply:** при проектировании UI/data model выводить EV целиком, а не только funding. Если пользователь предложит "просто список ставок как у fundoor" — напомнить, что это не диффренциатор и направить в сторону EV-калькулятора связок. Equity-perp связки в выходные и single-venue correlated pairs — два самых очевидных high-value кейса (см. arbitrage_strategies.md).

<!-- HOOK_TEST_1777743328 -->
<!-- HOOK_TEST_BS_1777743329 -->
<!-- HOOK_TEST_SHOULD_NOT_SYNC_1777743330 -->
<!-- SYNC_TEST_1777743424760164400 -->
<!-- SYNC_BS_1777743425463973500 -->
<!-- SYNC_NEG_1777743426015584600 -->
