# Scalping bot (PAPER / DEMO ONLY)

Live trading does not exist in this code base: `live_trading` fails validation and the exchange adapter refuses any host except `api-demo.bybit.com`.

## Setup
```
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env            # only needed for the Bybit demo adapter
python -m pytest -q             # 20 tests
python main.py backtest         # writes data/reports/backtest_report.md (real klines if reachable, else SYNTHETIC)
python main.py paper            # live public data, simulated fills (needs internet to api.bybit.com)
python main.py paper --replay 6000   # offline: synthetic feed through the same code path
python main.py dashboard        # Streamlit: state, P&L, positions, settings, emergency stop
```
Run `paper` and `dashboard` in separate terminals; they communicate through `data/bot.db`.

## Platform choice (Phase 1) — partially researched
Selected: **Bybit v5 demo trading** (REST host `https://api-demo.bybit.com`; public market data from the mainnet host; orders via REST only; demo orders kept 7 days; not every endpoint exists in demo). Verified from Bybit's docs: https://bybit-exchange.github.io/docs/v5/demo
**Not done:** official-doc review of cTrader Open API, MT5 brokers, Exness, Binance, OANDA, Kraken; no review of Freqtrade/Hummingbot/Jesse or video content. The choice rests on Bybit's documented demo API plus crypto perps supporting small sizes and high leverage; it is not a verified ranking. Regional eligibility for any real account is unchecked.

## Strategy
EMA9/EMA21 cross + RSI14 (>50 long, <50 short) + ATR14 volatility band, on closed candles. Exits: SL 1.5×ATR, TP 2.25×ATR (both mandatory). Entry at bar close. Size = permitted risk / (stop distance + round-trip cost per unit), floored to lot step, capped by margin; never rounded up. Costs: taker fee, spread, slippage, funding (charged to both sides). If SL and TP both fall inside one bar, SL is assumed first; gaps fill at the open.

## Risk controls (all tested unless marked)
- Daily loss counts unrealized P&L; hitting it flattens positions and blocks entries. New-trade size is cut to the remaining loss budget.
- Daily target stops new entries (realized P&L). Cooldown after a loss; stop for the day after 4 consecutive losses.
- Risk state is persisted in SQLite: restart on the same UTC day cannot reset losses. Emergency halt persists across restarts and days until cleared manually.
- Settings changes are validated; intraday they can only tighten limits, loosening applies next UTC day.
- Duplicate-signal and duplicate-order-ID prevention; stale/gapped/NaN data rejected.
- Exchange adapter: bounded retries, idempotent `orderLinkId`, SL/TP required on every order, unresolved order -> `OrderStateUncertain` blocks further orders. **Mock-tested only.**
- Limits are not guarantees: stops can slip or gap, so a day can lose more than the cap.

## Status
| Item | State |
|---|---|
| Indicators, sizing, risk manager, paper broker, backtester, SQLite | implemented, unit-tested |
| Backtest on real market data | **unverified** (sandbox could not reach Bybit) |
| Bybit demo adapter against the real demo host | **unverified**, not wired into `main.py` |
| Position reconciliation (`position_manager.reconcile`) | implemented as a pure function, unit-tested; not yet driven by a live loop |
| Dashboard | boots (HTTP 200); controls and settings form not exercised by automated UI tests |
| Live paper loop against real feed | **unverified** |
| Funding model, instrument limits (min qty 0.001 BTC) | assumptions; check `/v5/market/instruments-info` |

## Results so far
Synthetic random-walk data (20k bars, 5m): net about -$9.5 on a $10 balance; fees $7.4 vs gross -$2.2; the random-entry baseline did about the same. That is expected with no real edge and ~14 bp round-trip costs; it says nothing about real markets. At $10 with $0.50 risk, fees are a large share of the expected move; do not expect the +$5/day target to be reachable.
