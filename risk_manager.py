"""Position sizing, exposure and persistent daily limits."""
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from config import Settings
from database import Database


@dataclass
class InstrumentSpec:
    """Defaults for BTCUSDT linear perp. VERIFY against /v5/market/instruments-info before demo use."""
    symbol: str = "BTCUSDT"
    min_qty: float = 0.001
    qty_step: float = 0.001
    contract_size: float = 1.0


def day_of(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


class RiskManager:
    KEY = "risk_state"

    def __init__(self, db: Database, s: Settings, spec: InstrumentSpec | None = None):
        self.db, self.s, self.spec = db, s, spec or InstrumentSpec(s.symbol)
        self.state = db.get(self.KEY) or self._fresh("", {})
        self._dirty = False

    # ---- state ----
    def _fresh(self, day, prev):
        return dict(day=day, realized=0.0, day_max_loss=self.s.max_daily_loss, day_target=self.s.daily_target,
                    trades=0, consec_losses=0, cooldown_until=prev.get("cooldown_until", 0),
                    emergency=prev.get("emergency", False), last_signal_ts=prev.get("last_signal_ts", 0))

    def save(self):
        if self._dirty:
            self.db.set(self.KEY, self.state); self._dirty = False

    def roll(self, ts_ms: int):
        d = day_of(ts_ms)
        if self.state["day"] != d:
            self.state = self._fresh(d, self.state)  # emergency flag, cooldown persist; daily P&L resets only on a new UTC day
            self._dirty = True; self.save()

    def apply_settings(self, new: Settings):
        """Intraday, limits may only tighten; loosening applies from the next UTC day."""
        self.s = new
        self.state["day_max_loss"] = min(self.state["day_max_loss"], new.max_daily_loss)
        self.state["day_target"] = min(self.state["day_target"], new.daily_target)
        self._dirty = True; self.save()

    def set_emergency(self, on: bool):
        self.state["emergency"] = on; self._dirty = True; self.save()

    def mark_signal(self, ts):
        self.state["last_signal_ts"] = ts; self._dirty = True

    def record_close(self, net: float, ts_ms: int):
        st = self.state
        st["realized"] += net; st["trades"] += 1
        if net < 0:
            st["consec_losses"] += 1
            st["cooldown_until"] = ts_ms + self.s.cooldown_bars_after_loss * self.s.timeframe_min * 60_000
        else:
            st["consec_losses"] = 0
        self._dirty = True; self.save()

    # ---- decisions ----
    def day_pnl(self, unrealized=0.0):
        return self.state["realized"] + unrealized

    def should_flatten(self, unrealized: float) -> bool:
        return self.day_pnl(unrealized) <= -self.state["day_max_loss"]

    def allowed_risk(self, unrealized: float, open_risk: float) -> float:
        """Worst-case loss budget left today, capped by per-trade risk (fees/slippage are inside sizing)."""
        budget = self.state["day_max_loss"] + self.day_pnl(unrealized) - open_risk
        return max(0.0, min(self.s.risk_per_trade, budget))

    def check_entry(self, ts_ms: int, unrealized: float, n_open: int, open_risk: float, spread_bps: float):
        st = self.state
        if st["emergency"]: return False, "emergency halt"
        if st["last_signal_ts"] and ts_ms <= st["last_signal_ts"]: return False, "duplicate signal bar"
        if self.day_pnl(unrealized) <= -st["day_max_loss"]: return False, "daily loss limit"
        if st["realized"] >= st["day_target"]: return False, "daily target reached"
        if ts_ms < st["cooldown_until"]: return False, "cooldown after loss"
        if st["consec_losses"] >= self.s.max_consecutive_losses: return False, "too many consecutive losses"
        if n_open >= self.s.max_open_positions: return False, "max open positions"
        if spread_bps > self.s.max_spread_bps: return False, "spread too wide"
        if self.allowed_risk(unrealized, open_risk) <= 0: return False, "no risk budget left"
        return True, "ok"

    def unit_cost(self, price: float, spread_bps: float) -> float:
        """Round-trip cost per unit: 2 fees + spread + 2 slippage."""
        return price * (2 * self.s.taker_fee_rate + (spread_bps + 2 * self.s.slippage_bps) / 1e4)

    def size(self, entry: float, stop_dist: float, risk: float, free_margin: float, spread_bps: float):
        """qty from monetary risk (never rounded up), then capped by margin. None if below min size."""
        if stop_dist <= 0 or risk <= 0 or entry <= 0: return None, "bad inputs"
        qty = risk / (stop_dist + self.unit_cost(entry, spread_bps))
        max_by_margin = max(0.0, free_margin * 0.9) * self.s.leverage / entry
        qty = min(qty, max_by_margin)
        step = self.spec.qty_step
        qty = math.floor(qty / step + 1e-9) * step
        if qty < self.spec.min_qty - 1e-12:
            return None, "size below minimum (risk/margin too small)"
        return round(qty, 8), "ok"
