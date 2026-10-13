"""Indicators and signals. All indicators are causal (no look-ahead); signal at row i uses data <= i."""
import numpy as np
import pandas as pd
from config import Settings


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan)).fillna(np.inf).replace(np.inf, 1e12)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df.close.shift(1)
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def compute(df: pd.DataFrame, s: Settings) -> pd.DataFrame:
    """Return df with ema_f, ema_s, rsi, atr, atr_bps, signal (+1 long, -1 short, 0 none).
    Expects CLOSED candles only. Signal on bar i => act after bar i closes."""
    o = df.copy()
    o["ema_f"], o["ema_s"] = ema(o.close, s.ema_fast), ema(o.close, s.ema_slow)
    o["rsi"], o["atr"] = rsi(o.close, s.rsi_period), atr(o, s.atr_period)
    o["atr_bps"] = o.atr / o.close * 1e4
    up = (o.ema_f > o.ema_s) & (o.ema_f.shift(1) <= o.ema_s.shift(1))
    dn = (o.ema_f < o.ema_s) & (o.ema_f.shift(1) >= o.ema_s.shift(1))
    vol_ok = (o.atr_bps >= s.min_atr_bps) & (o.atr_bps <= s.max_atr_bps)
    sig = np.where(up & (o.rsi > s.rsi_long_min) & vol_ok, 1, np.where(dn & (o.rsi < s.rsi_short_max) & vol_ok, -1, 0))
    warm = 3 * max(s.ema_slow, s.rsi_period, s.atr_period)
    sig[:warm] = 0
    o["signal"] = sig
    return o


def random_signals(df: pd.DataFrame, s: Settings, seed: int, rate: float) -> pd.DataFrame:
    """Baseline: random-direction entries at `rate` per bar, same filters/exits/sizing as the strategy."""
    o = compute(df, s)
    rng = np.random.default_rng(seed)
    pick = rng.random(len(o)) < rate
    o["signal"] = np.where(pick & (o.atr_bps >= s.min_atr_bps) & (o.atr_bps <= s.max_atr_bps),
                           rng.choice([-1, 1], len(o)), 0)
    o.loc[o.index[:3 * s.atr_period], "signal"] = 0
    return o
