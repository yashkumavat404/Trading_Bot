"""Streamlit dashboard: python main.py dashboard"""
import time
import pandas as pd, streamlit as st
import config
from config import Settings
from database import Database
from risk_manager import day_of

st.set_page_config(page_title="Scalping Bot (PAPER)", layout="wide")
db = Database("data/bot.db")
s = config.from_dict(db.get("settings", {}))
ctl = db.get("control", {"state": "RUNNING"})
risk = db.get("risk_state") or {}
paper = db.get("paper_state") or {"balance": s.initial_balance, "positions": []}
status = db.get("status", {"api": "no data yet"})

st.title("Scalping bot — PAPER / DEMO ONLY")
state = "EMERGENCY HALT" if risk.get("emergency") else ctl.get("state", "RUNNING")
c = st.columns(4)
c[0].metric("State", state); c[1].metric("API / data", status["api"][:40]); c[2].metric("Live trading", "DISABLED")
c[3].metric("Day (UTC)", risk.get("day", "-"))

b1, b2, b3, b4 = st.columns(4)
if b1.button("Pause"): db.set("control", {**ctl, "state": "PAUSED"}); st.rerun()
if b2.button("Resume"): db.set("control", {**ctl, "state": "RUNNING"}); st.rerun()
if b3.button("Stop bot"): db.set("control", {**ctl, "state": "STOPPED"}); st.rerun()
if b4.button("🛑 EMERGENCY STOP", type="primary"):
    r = dict(risk or {}); r["emergency"] = True; db.set("risk_state", r)
    db.set("control", {**ctl, "state": "PAUSED", "emergency": True}); st.rerun()
if risk.get("emergency") and st.button("Clear emergency halt (manual)"):
    r = dict(risk); r["emergency"] = False; db.set("risk_state", r); db.set("control", {**ctl, "emergency": False}); st.rerun()

tr = db.trades(); eq = db.equity()
realized = risk.get("realized", 0.0)
m = st.columns(5)
m[0].metric("Balance", f"${paper['balance']:.2f}")
m[1].metric("Equity", f"${eq.equity.iloc[-1]:.2f}" if len(eq) else "-")
m[2].metric("Realized P&L today", f"${realized:+.2f}", f"target +${risk.get('day_target', s.daily_target):.2f}")
unreal = (eq.equity.iloc[-1] - paper["balance"]) if len(eq) else 0.0
m[3].metric("Unrealized", f"${unreal:+.2f}")
m[4].metric("Loss limit today", f"-${risk.get('day_max_loss', s.max_daily_loss):.2f}")
if len(tr):
    n = tr.net
    dd = (eq.equity.cummax() - eq.equity).max() if len(eq) else 0
    k = st.columns(4)
    k[0].metric("Trades", len(tr)); k[1].metric("Win rate", f"{(n > 0).mean():.0%}")
    k[2].metric("Expectancy", f"${n.mean():+.3f}"); k[3].metric("Max drawdown", f"${dd:.2f}")
    st.caption(f"Total fees paid: ${tr.fees.sum():.3f}")
if len(eq): st.line_chart(eq.set_index("ts").equity)
st.subheader("Open positions (entry / SL / TP)")
st.dataframe(pd.DataFrame(paper["positions"]) if paper["positions"] else pd.DataFrame(columns=["no open positions"]))
st.subheader("Trade history"); st.dataframe(tr.tail(100).iloc[::-1] if len(tr) else tr)
st.subheader("Events / errors"); st.dataframe(db.events(30))

st.subheader("Settings (validated; intraday loosening of limits is deferred to next UTC day)")
with st.form("settings"):
    d = config.to_dict(s); new = {}
    cols = st.columns(3)
    for i, (k, v) in enumerate(d.items()):
        if k == "live_trading": continue
        with cols[i % 3]:
            new[k] = st.number_input(k, value=float(v)) if isinstance(v, float) else \
                st.number_input(k, value=int(v), step=1) if isinstance(v, int) and not isinstance(v, bool) else st.text_input(k, v)
    if st.form_submit_button("Apply"):
        cand = config.from_dict({**d, **new})
        errs = config.validate(cand)
        if errs: st.error("Rejected: " + "; ".join(errs))
        else:
            db.set("settings", config.to_dict(cand))
            st.success("Saved. Limits tighten immediately; loosening takes effect next UTC day. Symbol/timeframe changes need a bot restart.")
time.sleep(0.1)
st.caption("Refresh the page to update.")
