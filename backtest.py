"""Backtesting: costs-aware engine (same session logic as paper mode), OOS split, walk-forward,
parameter sensitivity, Monte Carlo and a random-entry baseline."""
from __future__ import annotations
import itertools
from dataclasses import replace
import numpy as np, pandas as pd
from config import Settings
from database import Database
from paper_trading import PaperSession
from risk_manager import InstrumentSpec
import strategy


def run(df: pd.DataFrame, s: Settings, spec=None, ind: pd.DataFrame | None = None) -> dict:
    ind = strategy.compute(df, s) if ind is None else ind
    sess = PaperSession(Database(":memory:"), s, spec or InstrumentSpec(s.symbol), persist=False)
    for bar, row in zip(df.to_dict("records"), ind.to_dict("records")):
        sess.on_bar(bar, row)
    if sess.broker.positions:
        last = df.iloc[-1]
        sess._closed(sess.broker.flatten(last.close, int(last.ts) + sess.tf_ms, "END"))
    t = pd.DataFrame(sess.trades)
    return dict(trades=t, equity=np.array(sess.equity_curve), metrics=metrics(t, s.initial_balance, sess.equity_curve))


def metrics(t: pd.DataFrame, bal0: float, eq=None) -> dict:
    if t is None or len(t) == 0:
        return dict(trades=0, net=0.0, win_rate=0.0, expectancy=0.0, profit_factor=0.0, max_dd=0.0, avg_win=0.0,
                    avg_loss=0.0, max_consec_losses=0, fees=0.0, gross=0.0)
    n = t.net.values
    w, l = n[n > 0], n[n <= 0]
    cl = mx = 0
    for x in n:
        cl = cl + 1 if x <= 0 else 0; mx = max(mx, cl)
    curve = np.asarray(eq) if eq is not None and len(eq) else bal0 + np.cumsum(n)
    dd = float((np.maximum.accumulate(curve) - curve).max())
    return dict(trades=int(len(n)), net=float(n.sum()), win_rate=float((n > 0).mean()), expectancy=float(n.mean()),
                profit_factor=float(w.sum() / -l.sum()) if l.sum() < 0 else float("inf"), max_dd=dd,
                avg_win=float(w.mean()) if len(w) else 0.0, avg_loss=float(l.mean()) if len(l) else 0.0,
                max_consec_losses=mx, fees=float(t.fees.sum()), gross=float(t.gross.sum()))


def oos_split(df, s, frac=0.7):
    k = int(len(df) * frac)
    return run(df.iloc[:k].reset_index(drop=True), s)["metrics"], run(df.iloc[k:].reset_index(drop=True), s)["metrics"]


GRID = dict(ema_slow=[15, 21], sl_atr_mult=[1.0, 1.5, 2.0], tp_atr_mult=[1.5, 2.25, 3.0])


def sensitivity(df, s, grid=GRID) -> pd.DataFrame:
    rows = []
    for combo in itertools.product(*grid.values()):
        p = dict(zip(grid, combo)); m = run(df, replace(s, **p))["metrics"]
        rows.append({**p, "trades": m["trades"], "net": m["net"], "expectancy": m["expectancy"], "max_dd": m["max_dd"]})
    return pd.DataFrame(rows)


def walk_forward(df, s, train=6000, test=2000, grid=GRID) -> dict:
    """Pick params on train window (max net, min 10 trades), evaluate on the following unseen window."""
    outs, start = [], 0
    while start + train + test <= len(df):
        tr, te = df.iloc[start:start + train].reset_index(drop=True), df.iloc[start + train:start + train + test].reset_index(drop=True)
        sens = sensitivity(tr, s, grid); sens = sens[sens.trades >= 10]
        if len(sens):
            best = sens.sort_values("net", ascending=False).iloc[0]
            p = {k: type(getattr(s, k))(best[k]) for k in grid}
            outs.append(run(te, replace(s, **p))["trades"])
        start += test
    t = pd.concat(outs) if outs else pd.DataFrame()
    return dict(windows=len(outs), metrics=metrics(t, s.initial_balance))


def monte_carlo(nets, bal0, n=2000, seed=1) -> dict:
    nets = np.asarray(nets, float)
    if len(nets) < 5: return {}
    rng = np.random.default_rng(seed)
    end, dds = [], []
    for _ in range(n):
        c = bal0 + np.cumsum(rng.choice(nets, len(nets), replace=True))
        c = np.r_[bal0, c]; end.append(c[-1]); dds.append((np.maximum.accumulate(c) - c).max())
    end, dds = np.array(end), np.array(dds)
    return dict(end_p5=float(np.percentile(end, 5)), end_p50=float(np.percentile(end, 50)), end_p95=float(np.percentile(end, 95)),
                dd_p50=float(np.percentile(dds, 50)), dd_p95=float(np.percentile(dds, 95)),
                p_loss=float((end < bal0).mean()), p_dd_over_50pct=float((dds > 0.5 * bal0).mean()))


def baseline(df, s, seeds=10) -> dict:
    """Random-direction entries, same exits/sizing/costs/risk rules, matched to the strategy's trade rate."""
    rate = max((strategy.compute(df, s).signal != 0).mean(), 1e-3)
    ms = [run(df, s, ind=strategy.random_signals(df, s, sd, rate))["metrics"] for sd in range(seeds)]
    return dict(net_mean=float(np.mean([m["net"] for m in ms])), expectancy_mean=float(np.mean([m["expectancy"] for m in ms])),
                trades_mean=float(np.mean([m["trades"] for m in ms])))


def report(df, s, source: str) -> str:
    full = run(df, s); m = full["metrics"]
    is_m, oos_m = oos_split(df, s)
    mc = monte_carlo(full["trades"].net.values, s.initial_balance) if m["trades"] >= 5 else {}
    bl = baseline(df, s)
    wf = walk_forward(df, s, train=max(len(df) // 3, 1500), test=max(len(df) // 6, 500))
    sens = sensitivity(df, s)
    f = lambda d: "\n".join(f"- {k}: {v:.4f}" if isinstance(v, float) else f"- {k}: {v}" for k, v in d.items())
    return f"""# Backtest report
**Data source: {source}** ({len(df)} bars, {s.timeframe_min}m). {'SYNTHETIC random-walk data: NOT evidence of profitability.' if 'SYNTHETIC' in source else ''}
Costs included: taker fee {s.taker_fee_rate:.5f}/side, spread {s.paper_spread_bps}bp, slippage {s.slippage_bps}bp/side, funding {s.funding_rate_8h}/8h. Risk/trade ${s.risk_per_trade}, daily loss cap ${s.max_daily_loss}, daily target ${s.daily_target}.

## Full period
{f(m)}
## In-sample (70%)
{f(is_m)}
## Out-of-sample (30%)
{f(oos_m)}
## Walk-forward ({wf['windows']} windows, params chosen on train only)
{f(wf['metrics'])}
## Random-entry baseline (same exits/costs/risk, mean of 10 seeds)
{f(bl)}
## Monte Carlo (bootstrap of trade P&L, 2000 runs)
{f(mc) if mc else 'too few trades'}
## Parameter sensitivity (net P&L)
{sens.round(3).to_markdown(index=False) if hasattr(sens,'to_markdown') and _has_tab() else sens.round(3).to_string(index=False)}
"""


def _has_tab():
    try:
        import tabulate  # noqa
        return True
    except ImportError:
        return False
