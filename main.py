"""Entry point: python main.py {backtest|paper|dashboard}. Paper/demo only; live trading does not exist here."""
import argparse, subprocess, sys, time
import config, market_data as md, strategy, backtest as bt
from database import Database
from paper_trading import PaperSession
from risk_manager import InstrumentSpec


def load_settings(db):
    s = config.from_dict(db.get("settings", {}))
    errs = config.validate(s)
    if errs: raise SystemExit("invalid settings: " + "; ".join(errs))
    return s


def get_data(s, bars, seed):
    try:
        df = md.fetch_history(s.symbol, s.timeframe_min, bars)
        ok, why = md.validate(df, s.timeframe_min, check_stale=False)
        if not ok: raise ValueError(why)
        return df, f"Bybit public klines {s.symbol}"
    except Exception as e:
        print(f"[warn] real data unavailable ({e}); using SYNTHETIC data")
        return md.synthetic(bars, seed=seed, tf_min=s.timeframe_min), "SYNTHETIC random walk (real data unavailable)"


def cmd_backtest(a):
    db = Database(a.db); s = load_settings(db)
    df, src = get_data(s, a.bars, a.seed)
    txt = bt.report(df, s, src)
    open("data/reports/backtest_report.md", "w").write(txt); print(txt)


def cmd_paper(a):
    log = config.setup_logging(); db = Database(a.db); s = load_settings(db)
    sess = PaperSession(db, s, InstrumentSpec(s.symbol))
    tf = s.timeframe_min * 60_000
    db.set("control", {**db.get("control", {}), "state": "RUNNING"})
    if a.replay:  # fast simulated feed through the full live code path
        df = md.synthetic(a.replay, seed=a.seed, tf_min=s.timeframe_min); ind = strategy.compute(df, s)
        for b, r in zip(df.to_dict("records"), ind.to_dict("records")):
            sess.on_bar(b, r)
        print(f"replay done: {len(sess.trades)} trades, balance {sess.broker.balance:.4f}, rejects {sess.rejects}"); return
    while True:
        ctl = db.get("control", {})
        if ctl.get("state") == "STOPPED": log.info("stopped by control"); break
        try:
            ns = config.from_dict(db.get("settings", {}))
            if not config.validate(ns): sess.rm.apply_settings(ns)
            df = md.closed_only(md.fetch_klines(s.symbol, s.timeframe_min, 300), s.timeframe_min)
            ok, why = md.validate(df, s.timeframe_min, stale_factor=s.stale_factor)
            db.set("status", {"api": "OK" if ok else f"DATA REJECTED: {why}", "ts": int(time.time() * 1000)})
            if ok:
                ind = strategy.compute(df, s); last = db.get("last_bar_ts", 0)
                for b, r in zip(df.to_dict("records"), ind.to_dict("records")):
                    if b["ts"] > last:
                        if last: sess.on_bar(b, r)  # first run only sets the cursor (no trading on history)
                        last = b["ts"]; db.set("last_bar_ts", last)
            else:
                log.warning("skipping cycle: %s", why); db.event("WARN", f"data rejected: {why}")
        except Exception as e:  # never trade on uncertain state
            db.set("status", {"api": f"ERROR: {e}", "ts": int(time.time() * 1000)}); db.event("ERROR", str(e)); log.error("%s", e)
        time.sleep(10)


def main():
    p = argparse.ArgumentParser(); p.add_argument("cmd", choices=["backtest", "paper", "dashboard"])
    p.add_argument("--db", default="data/bot.db"); p.add_argument("--bars", type=int, default=20000)
    p.add_argument("--seed", type=int, default=7); p.add_argument("--replay", type=int, default=0)
    a = p.parse_args()
    if a.cmd == "dashboard": subprocess.run([sys.executable, "-m", "streamlit", "run", "dashboard.py"])
    else: {"backtest": cmd_backtest, "paper": cmd_paper}[a.cmd](a)


if __name__ == "__main__":
    main()
