"""Settings, validation and hard safety limits."""
from __future__ import annotations
from dataclasses import dataclass, asdict, fields
import logging, logging.handlers, os


@dataclass
class Settings:
    symbol: str = "BTCUSDT"
    timeframe_min: int = 5
    initial_balance: float = 10.0
    leverage: float = 100.0
    daily_target: float = 5.0
    max_daily_loss: float = 2.0
    risk_per_trade: float = 0.5
    max_open_positions: int = 1
    # strategy
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    rsi_long_min: float = 50.0
    rsi_short_max: float = 50.0
    atr_period: int = 14
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 2.25
    min_atr_bps: float = 5.0
    max_atr_bps: float = 80.0
    # costs / filters
    taker_fee_rate: float = 0.00055
    paper_spread_bps: float = 1.0
    slippage_bps: float = 1.0
    funding_rate_8h: float = 0.0001   # charged to both sides (conservative)
    max_spread_bps: float = 3.0
    max_cost_to_tp: float = 0.40      # round-trip cost / TP distance
    cooldown_bars_after_loss: int = 3
    max_consecutive_losses: int = 4
    stale_factor: float = 2.5         # data older than factor*timeframe is stale
    live_trading: bool = False        # cannot be enabled via settings


HARD = dict(max_leverage=100.0, max_loss_frac_of_balance=0.25, max_risk_frac_of_daily_loss=0.5)


def validate(s: Settings) -> list[str]:
    e = []
    if s.live_trading: e.append("live_trading cannot be enabled (demo/paper only)")
    if s.initial_balance <= 0: e.append("initial_balance must be > 0")
    if not 1 <= s.leverage <= HARD["max_leverage"]: e.append("leverage must be in [1,100]")
    if s.daily_target <= 0: e.append("daily_target must be > 0")
    if s.max_daily_loss <= 0: e.append("max_daily_loss must be > 0")
    if s.max_daily_loss > HARD["max_loss_frac_of_balance"] * s.initial_balance:
        e.append("max_daily_loss exceeds 25% of balance")
    if s.risk_per_trade <= 0: e.append("risk_per_trade must be > 0")
    if s.risk_per_trade > HARD["max_risk_frac_of_daily_loss"] * s.max_daily_loss:
        e.append("risk_per_trade must be <= 50% of max_daily_loss")
    if s.ema_fast >= s.ema_slow: e.append("ema_fast must be < ema_slow")
    if s.sl_atr_mult <= 0 or s.tp_atr_mult <= 0: e.append("SL/TP ATR multiples must be > 0 (mandatory exits)")
    if not 1 <= s.max_open_positions <= 5: e.append("max_open_positions in [1,5]")
    if s.timeframe_min not in (1, 3, 5, 15, 30, 60): e.append("unsupported timeframe")
    if s.taker_fee_rate < 0 or s.slippage_bps < 0 or s.paper_spread_bps < 0: e.append("costs must be >= 0")
    return e


def from_dict(d: dict) -> Settings:
    names = {f.name: f.type for f in fields(Settings)}
    base = Settings()
    for k, v in d.items():
        if k in names:
            setattr(base, k, type(getattr(base, k))(v))
    return base


def to_dict(s: Settings) -> dict:
    return asdict(s)


def setup_logging(path: str = "logs/bot.log") -> logging.Logger:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    log = logging.getLogger("bot")
    if not log.handlers:
        log.setLevel(logging.INFO)
        fmt = logging.Formatter('{"t":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}')
        for h in (logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3), logging.StreamHandler()):
            h.setFormatter(fmt); log.addHandler(h)
    return log
