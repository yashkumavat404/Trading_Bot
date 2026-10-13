import os, sys, tempfile, hashlib, hmac
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dataclasses import replace
import numpy as np, pandas as pd, pytest, requests
import config, market_data as md, strategy, backtest as bt
from config import Settings
from database import Database
from paper_trading import PaperBroker, PaperSession, DuplicateOrder
from risk_manager import RiskManager
from execution import BybitDemoBroker, OrderStateUncertain
from position_manager import reconcile

S = Settings(); DAY = 1_700_000_000_000


def bar(ts, o, h, l, c): return dict(ts=ts, open=o, high=h, low=l, close=c)


def test_validation_rejects_bad_settings():
    assert not config.validate(S)
    assert config.validate(replace(S, live_trading=True))
    assert config.validate(replace(S, leverage=500))
    assert config.validate(replace(S, risk_per_trade=1.5))
    assert config.validate(replace(S, max_daily_loss=5))
    assert config.validate(replace(S, sl_atr_mult=0))


def test_indicators_no_lookahead():
    df = md.synthetic(600, seed=3); full = strategy.compute(df, S)
    part = strategy.compute(df.iloc[:400].reset_index(drop=True), S)
    pd.testing.assert_frame_equal(full.iloc[:400].reset_index(drop=True), part)


def test_ema_matches_manual():
    x = pd.Series([1., 2, 3, 4, 5]); a = 2 / 4; e = [1.]
    for v in x[1:]: e.append(a * v + (1 - a) * e[-1])
    assert np.allclose(strategy.ema(x, 3), e)


def test_rsi_bounds():
    r = strategy.rsi(md.synthetic(300).close).dropna(); assert ((r >= 0) & (r <= 100)).all()


def test_sizing_never_exceeds_risk_and_respects_min():
    rm = RiskManager(Database(":memory:"), S)
    q, _ = rm.size(60000, 180, 0.5, 10, 1.0)
    assert q and q * (180 + rm.unit_cost(60000, 1.0)) <= 0.5 + 1e-9
    assert rm.size(60000, 5000, 0.5, 10, 1.0)[0] is None
    assert rm.size(60000, 180, 0.5, 0.01, 1.0)[0] is None


def test_loss_limit_blocks_and_persists_across_restart():
    path = tempfile.mktemp(suffix=".db"); db = Database(path)
    rm = RiskManager(db, S); rm.roll(DAY); rm.record_close(-2.0, DAY)
    assert rm.check_entry(DAY + 1, 0, 0, 0, 1)[1] == "daily loss limit"
    rm2 = RiskManager(Database(path), S); rm2.roll(DAY + 1000)
    assert rm2.state["realized"] == -2.0 and not rm2.check_entry(DAY + 5, 0, 0, 0, 1)[0]


def test_unrealized_counts_toward_limit():
    rm = RiskManager(Database(":memory:"), S); rm.roll(DAY)
    assert rm.should_flatten(-2.0) and not rm.should_flatten(-1.0)
    assert rm.allowed_risk(-1.8, 0) == pytest.approx(0.2)


def test_settings_cannot_loosen_intraday():
    rm = RiskManager(Database(":memory:"), S); rm.roll(DAY)
    rm.apply_settings(replace(S, max_daily_loss=2.5, daily_target=50))
    assert rm.state["day_max_loss"] == 2.0 and rm.state["day_target"] == 5.0
    rm.apply_settings(replace(S, max_daily_loss=1.0, risk_per_trade=0.4)); assert rm.state["day_max_loss"] == 1.0


def test_emergency_halt_persists_and_blocks():
    path = tempfile.mktemp(suffix=".db"); rm = RiskManager(Database(path), S); rm.roll(DAY); rm.set_emergency(True)
    rm2 = RiskManager(Database(path), S); nd = DAY + 10 * 86400_000; rm2.roll(nd)
    assert rm2.check_entry(nd, 0, 0, 0, 1) == (False, "emergency halt")


def test_duplicate_order_and_signal_prevented():
    b = PaperBroker(S); b.open("x", 1, 0.001, 59000, 61000, 60000, DAY, 0.2)
    with pytest.raises(DuplicateOrder): b.open("x", 1, 0.001, 59000, 61000, 60000, DAY, 0.2)
    rm = RiskManager(Database(":memory:"), S); rm.roll(DAY); rm.mark_signal(DAY)
    assert rm.check_entry(DAY, 0, 0, 0, 1)[1] == "duplicate signal bar"


def test_sl_first_when_both_hit_and_costs_applied():
    b = PaperBroker(S); b.open("a", 1, 0.01, 59000, 61000, 60000, DAY, 0.2)
    t = b.check_exits(bar(DAY, 60000, 62000, 58000, 60000), 300_000)[0]
    assert t["reason"] == "SL" and t["net"] < t["gross"] < 0 and t["fees"] > 0


def test_gap_through_stop_fills_at_open():
    b = PaperBroker(S); b.open("a", 1, 0.01, 59000, 61000, 60000, DAY, 0.2)
    t = b.check_exits(bar(DAY, 57000, 57500, 56000, 57000), 300_000)[0]
    assert t["exit"] < 57100


def test_margin_rejection_in_session():
    s = replace(S, leverage=1.0); sess = PaperSession(Database(":memory:"), s, persist=False)
    sess.on_bar(bar(DAY, 60000, 60100, 59900, 60000), dict(signal=1, atr=120.0))
    assert not sess.broker.positions


def test_session_trade_has_sl_tp_and_bounded_risk():
    sess = PaperSession(Database(":memory:"), S, persist=False)
    sess.on_bar(bar(DAY, 60000, 60100, 59900, 60000), dict(signal=1, atr=120.0))
    p = sess.broker.positions[0]
    assert p["sl"] < p["entry"] < p["tp"] and (p["entry"] - p["sl"]) * p["qty"] <= S.risk_per_trade


def test_stale_and_bad_data_rejected():
    df = md.synthetic(50, tf_min=5); end = int(df.ts.iloc[-1]) + 300_000
    assert md.validate(df, 5, now_ms=end + 10_000)[0]
    assert md.validate(df, 5, now_ms=end + 5 * 300_000) == (False, "stale data")
    g = df.drop(index=20).reset_index(drop=True); assert md.validate(g, 5, check_stale=False)[1] == "gap in candles"
    n = df.copy(); n.loc[5, "close"] = np.nan; assert not md.validate(n, 5, check_stale=False)[0]


def test_backtest_costs_and_daily_loss_cap():
    r = bt.run(md.synthetic(6000, seed=5), S); t = r["trades"]
    assert (t.fees > 0).all()
    t["day"] = pd.to_datetime(t.close_ts, unit="ms").dt.date
    assert t.groupby("day").net.sum().min() > -(S.max_daily_loss * 1.5)


def test_monte_carlo_shape():
    mc = bt.monte_carlo([0.3, -0.5, 0.2, -0.1, 0.4, -0.3], 10); assert mc["end_p5"] <= mc["end_p95"]


class Resp:
    def __init__(s, j): s.j = j
    def raise_for_status(s): pass
    def json(s): return s.j


class FakeHTTP:
    def __init__(s, script): s.script, s.calls = list(script), []
    def request(s, method, url, headers=None, data=None, timeout=None):
        s.calls.append((method, url, headers, data)); x = s.script.pop(0)
        if isinstance(x, Exception): raise x
        return Resp(x)


def test_bybit_signing_host_lock_and_mandatory_sl_tp():
    with pytest.raises(ValueError): BybitDemoBroker("k", "s", host="https://api.bybit.com")
    h = FakeHTTP([{"retCode": 0, "result": {"ok": 1}}]); b = BybitDemoBroker("k", "s", session=h)
    with pytest.raises(ValueError): b.market_order("BTCUSDT", 1, 0.001, 0, 61000, "id1")
    b.market_order("BTCUSDT", 1, 0.001, 59000, 61000, "id1")
    hd = h.calls[0][2]
    exp = hmac.new(b"s", (hd["X-BAPI-TIMESTAMP"] + "k" + "5000" + h.calls[0][3]).encode(), hashlib.sha256).hexdigest()
    assert hd["X-BAPI-SIGN"] == exp and '"orderLinkId":"id1"' in h.calls[0][3]


def test_bybit_timeouts_bounded_then_uncertain_blocks_orders(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    h = FakeHTTP([requests.Timeout()] * 6); b = BybitDemoBroker("k", "s", session=h, retries=3)
    with pytest.raises(OrderStateUncertain): b.market_order("BTCUSDT", 1, 0.001, 59000, 61000, "id2")
    assert len(h.calls) == 6
    with pytest.raises(OrderStateUncertain): b.market_order("BTCUSDT", 1, 0.001, 59000, 61000, "id3")


def test_reconcile_actions():
    L = [dict(symbol="BTCUSDT", side=1, qty=0.002, sl=1, tp=2)]
    E = [dict(symbol="BTCUSDT", side=-1, qty=0.001, sl=0, tp=0)]
    assert {a[0] for a in reconcile(L, E)} == {"adopt", "orphan", "unprotected"}
