"""Market data: Bybit public klines/ticker, synthetic generator, validation."""
import time
import numpy as np
import pandas as pd
import requests

PUBLIC = "https://api.bybit.com"
COLS = ["ts", "open", "high", "low", "close", "volume"]


def _get(path, params, session=requests, timeout=8, retries=3):
    err = None
    for i in range(retries):
        try:
            r = session.get(PUBLIC + path, params=params, timeout=timeout)
            r.raise_for_status()
            j = r.json()
            if j.get("retCode") != 0:
                raise RuntimeError(f"api error {j.get('retCode')}: {j.get('retMsg')}")
            return j["result"]
        except Exception as e:  # bounded retry with backoff
            err = e; time.sleep(min(0.5 * 2 ** i, 4))
    raise ConnectionError(f"market data request failed: {err}")


def _to_df(rows):
    df = pd.DataFrame([[int(r[0])] + [float(x) for x in r[1:6]] for r in rows], columns=COLS)
    return df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def fetch_klines(symbol, tf_min, limit=200, end=None, session=requests):
    p = dict(category="linear", symbol=symbol, interval=str(tf_min), limit=limit)
    if end: p["end"] = int(end)
    return _to_df(_get("/v5/market/kline", p, session)["list"])


def fetch_history(symbol, tf_min, n, session=requests):
    parts, end = [], None
    while sum(len(x) for x in parts) < n:
        d = fetch_klines(symbol, tf_min, 1000, end, session)
        if d.empty: break
        parts.append(d); end = int(d.ts.iloc[0]) - 1
    return pd.concat(parts).drop_duplicates("ts").sort_values("ts").tail(n).reset_index(drop=True)


def fetch_ticker(symbol, session=requests):
    r = _get("/v5/market/tickers", dict(category="linear", symbol=symbol), session)["list"][0]
    return float(r["bid1Price"]), float(r["ask1Price"])


def synthetic(n=20000, seed=0, start_price=60000.0, vol_bps=12.0, tf_min=5, drift_bps=0.0, t0=1_700_000_000_000):
    """Random-walk OHLC with volatility clustering. SYNTHETIC: carries no evidence of real-market edge."""
    rng = np.random.default_rng(seed)
    vol = vol_bps / 1e4 * np.exp(0.3 * np.convolve(rng.standard_normal(n), np.ones(50) / 50, "same") * 5)
    ret = drift_bps / 1e4 + vol * rng.standard_normal(n)
    close = start_price * np.exp(np.cumsum(ret))
    open_ = np.r_[start_price, close[:-1]]
    rng_hl = np.abs(vol * rng.standard_normal(n)) * close * 0.6
    high = np.maximum(open_, close) + rng_hl
    low = np.minimum(open_, close) - rng_hl
    ts = t0 + np.arange(n) * tf_min * 60_000
    return pd.DataFrame(dict(ts=ts, open=open_, high=high, low=low, close=close, volume=rng.uniform(1, 100, n)))


def closed_only(df, tf_min, now_ms=None):
    now_ms = now_ms or int(time.time() * 1000)
    return df[df.ts + tf_min * 60_000 <= now_ms].reset_index(drop=True)


def validate(df, tf_min, now_ms=None, stale_factor=2.5, check_stale=True):
    """Return (ok, reason). Rejects empty, NaN, non-monotonic, gapped, malformed or stale data."""
    if df is None or len(df) < 2: return False, "insufficient data"
    if df[COLS].isna().any().any(): return False, "NaN in data"
    if not df.ts.is_monotonic_increasing or df.ts.duplicated().any(): return False, "timestamps not strictly increasing"
    if (df.high < df.low).any() or (df.high < df[["open", "close"]].max(axis=1) - 1e-9).any() \
            or (df.low > df[["open", "close"]].min(axis=1) + 1e-9).any() or (df.close <= 0).any():
        return False, "malformed candle"
    step = tf_min * 60_000
    if (df.ts.diff().dropna() > step).any(): return False, "gap in candles"
    if check_stale:
        now_ms = now_ms or int(time.time() * 1000)
        if now_ms - (df.ts.iloc[-1] + step) > stale_factor * step: return False, "stale data"
    return True, "ok"
