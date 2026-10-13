"""Simulated execution (PaperBroker) and the bar-by-bar trading session shared by paper mode and backtests."""
from __future__ import annotations
from config import Settings
from database import Database
from risk_manager import RiskManager, InstrumentSpec

MS_8H = 8 * 3600 * 1000


class DuplicateOrder(Exception): ...


class PaperBroker:
    def __init__(self, s: Settings, state: dict | None = None):
        self.s = s
        st = state or {}
        self.balance = st.get("balance", s.initial_balance)
        self.positions: list[dict] = st.get("positions", [])
        self.seen: set = set(st.get("seen", []))

    def to_state(self):
        return dict(balance=self.balance, positions=self.positions, seen=sorted(self.seen)[-500:])

    # prices: candle values are treated as mid
    def bid(self, p): return p * (1 - self.s.paper_spread_bps / 2e4)
    def ask(self, p): return p * (1 + self.s.paper_spread_bps / 2e4)

    def fill_price(self, side, mid):
        slip = mid * self.s.slippage_bps / 1e4
        return self.ask(mid) + slip if side == 1 else self.bid(mid) - slip

    def margin_used(self): return sum(p["qty"] * p["entry"] / self.s.leverage for p in self.positions)
    def unrealized(self, mid):
        return sum((self.bid(mid) - p["entry"]) * p["qty"] if p["side"] == 1 else (p["entry"] - self.ask(mid)) * p["qty"]
                   for p in self.positions)
    def equity(self, mid): return self.balance + self.unrealized(mid)
    def free_margin(self, mid): return self.equity(mid) - self.margin_used()
    def open_risk(self): return sum(p["risk"] for p in self.positions)

    def open(self, client_id, side, qty, sl, tp, mid, ts, risk):
        if client_id in self.seen: raise DuplicateOrder(client_id)
        self.seen.add(client_id)
        fill = self.fill_price(side, mid)
        fee = qty * fill * self.s.taker_fee_rate
        self.balance -= fee
        pos = dict(client_id=client_id, side=side, qty=qty, entry=fill, sl=sl, tp=tp, open_ts=ts, entry_fee=fee, risk=risk)
        self.positions.append(pos)
        return pos

    def _close(self, p, fill, ts, reason):
        gross = (fill - p["entry"]) * p["qty"] * p["side"]
        fee_x = p["qty"] * fill * self.s.taker_fee_rate
        funding = p["qty"] * p["entry"] * self.s.funding_rate_8h * max(ts - p["open_ts"], 0) / MS_8H
        self.balance += gross - fee_x - funding
        self.positions.remove(p)
        fees = p["entry_fee"] + fee_x + funding
        return dict(client_id=p["client_id"], symbol=self.s_symbol, side="BUY" if p["side"] == 1 else "SELL", qty=p["qty"],
                    entry=p["entry"], exit=fill, sl=p["sl"], tp=p["tp"], open_ts=p["open_ts"], close_ts=ts,
                    gross=gross, fees=fees, net=gross - fees, reason=reason)

    @property
    def s_symbol(self): return self.s.symbol

    def check_exits(self, bar: dict, tf_ms: int):
        """Stops fill at stop (or open if gapped through) with adverse slippage; TP fills at limit.
        If SL and TP are both inside one bar, SL is assumed first (conservative)."""
        out, ts = [], bar["ts"] + tf_ms
        for p in list(self.positions):
            if p["open_ts"] >= ts: continue
            slip = lambda x: x * self.s.slippage_bps / 1e4
            if p["side"] == 1:
                sl_hit, tp_hit = self.bid(bar["low"]) <= p["sl"], self.bid(bar["high"]) >= p["tp"]
                if sl_hit:
                    base = min(p["sl"], self.bid(bar["open"])); out.append(self._close(p, base - slip(base), ts, "SL"))
                elif tp_hit: out.append(self._close(p, max(p["tp"], 0), ts, "TP"))
            else:
                sl_hit, tp_hit = self.ask(bar["high"]) >= p["sl"], self.ask(bar["low"]) <= p["tp"]
                if sl_hit:
                    base = max(p["sl"], self.ask(bar["open"])); out.append(self._close(p, base + slip(base), ts, "SL"))
                elif tp_hit: out.append(self._close(p, p["tp"], ts, "TP"))
        return out

    def flatten(self, mid, ts, reason):
        out = []
        for p in list(self.positions):
            f = self.bid(mid) - mid * self.s.slippage_bps / 1e4 if p["side"] == 1 else self.ask(mid) + mid * self.s.slippage_bps / 1e4
            out.append(self._close(p, f, ts, reason))
        return out


class PaperSession:
    """Processes one CLOSED bar at a time. Exits are checked on the bar, then the bar-close signal may enter at close."""

    def __init__(self, db: Database, s: Settings, spec: InstrumentSpec | None = None, persist=True, record_equity=True):
        self.db, self.s, self.persist, self.record_equity = db, s, persist, record_equity
        self.spec = spec or InstrumentSpec(s.symbol)
        self.rm = RiskManager(db, s, self.spec)
        self.broker = PaperBroker(s, db.get("paper_state") if persist else None)
        self.tf_ms = s.timeframe_min * 60_000
        self.last_reject = ""
        self.rejects: dict = {}
        self.equity_curve: list[float] = []
        self.trades: list[dict] = []

    def control(self):
        return self.db.get("control", {"state": "RUNNING"}) if self.persist else {"state": "RUNNING"}

    def _closed(self, trades):
        for t in trades:
            self.rm.record_close(t["net"], t["close_ts"])
            self.trades.append(t)
            if self.persist: self.db.insert_trade(t)

    def on_bar(self, bar: dict, row: dict):
        b, rm, s = self.broker, self.rm, self.s
        close_ts = bar["ts"] + self.tf_ms
        rm.roll(close_ts)
        self._closed(b.check_exits(bar, self.tf_ms))
        mid = bar["close"]
        if b.positions and rm.should_flatten(b.unrealized(mid)):
            self._closed(b.flatten(mid, close_ts, "RISK_FLATTEN"))
            if self.persist: self.db.event("WARN", "daily loss limit hit: positions flattened", close_ts)
        ctl = self.control()
        if ctl.get("emergency") and not rm.state["emergency"]: rm.set_emergency(True)
        sig = int(row.get("signal", 0))
        if sig and ctl.get("state", "RUNNING") == "RUNNING":
            self._try_enter(sig, bar, row, close_ts)
        eq = b.equity(mid)
        self.equity_curve.append(eq)
        if self.persist:
            if self.record_equity: self.db.add_equity(close_ts, eq, b.balance)
            self.db.set("paper_state", b.to_state())
        rm.save()

    def _try_enter(self, sig, bar, row, close_ts):
        b, rm, s = self.broker, self.rm, self.s
        mid = bar["close"]
        unreal = b.unrealized(mid)
        ok, why = rm.check_entry(bar["ts"], unreal, len(b.positions), b.open_risk(), s.paper_spread_bps)
        if not ok:
            self.last_reject = why; self.rejects[why] = self.rejects.get(why, 0) + 1; return
        rm.mark_signal(bar["ts"])
        atr = row["atr"]
        sl_d, tp_d = atr * s.sl_atr_mult, atr * s.tp_atr_mult
        fill = b.fill_price(sig, mid)
        if rm.unit_cost(fill, s.paper_spread_bps) > s.max_cost_to_tp * tp_d:
            self.last_reject = "costs too high vs TP"; self.rejects[self.last_reject] = self.rejects.get(self.last_reject, 0) + 1; return
        risk = rm.allowed_risk(unreal, b.open_risk())
        qty, why = rm.size(fill, sl_d, risk, b.free_margin(mid), s.paper_spread_bps)
        if qty is None:
            self.last_reject = why; self.rejects[why] = self.rejects.get(why, 0) + 1; return
        sl, tp = fill - sig * sl_d, fill + sig * tp_d
        b.open(f"{s.symbol}-{bar['ts']}-{sig}", sig, qty, sl, tp, mid, close_ts, risk)
        self.last_reject = ""
