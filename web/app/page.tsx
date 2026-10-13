"use client";

import { useEffect, useState } from "react";

type BotStatus = {
  state?: string;
  mode?: string;
  symbol?: string;
  timeframe?: string;
  api?: string;
  updated_at?: string;
  balance?: number;
  equity?: number;
  daily_pnl?: number;
  positions?: Array<{ symbol: string; side: string; qty: number; entry_price?: number; stop_loss?: number; take_profit?: number }>;
  orders?: Array<{ time?: string; symbol?: string; side?: string; qty?: number; status?: string; price?: number; pnl?: number }>;
};

const money = (v?: number) =>
  typeof v === "number" && Number.isFinite(v)
    ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(v)
    : "—";

export default function Dashboard() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [error, setError] = useState("Backend not connected yet");
  const [lastChecked, setLastChecked] = useState<string>("—");

  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_BOT_API_URL?.replace(/\/$/, "");
    if (!base) {
      setError("Worker API URL not configured");
      return;
    }
    let active = true;
    const check = async () => {
      try {
        const response = await fetch(`${base}/api/status`, { cache: "no-store" });
        if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
        const data = (await response.json()) as BotStatus;
        if (!active) return;
        setStatus(data);
        setError("");
        setLastChecked(new Date().toLocaleTimeString());
      } catch (e) {
        if (!active) return;
        setError(e instanceof Error ? e.message : "Unable to reach bot worker");
        setLastChecked(new Date().toLocaleTimeString());
      }
    };
    void check();
    const timer = window.setInterval(check, 15000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const positions = status?.positions ?? [];
  const orders = status?.orders ?? [];

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">T</span><span>TradePilot<span className="brand-sub">BOT CONSOLE</span></span></div>
        <div className="nav-label">WORKSPACE</div>
        <div className="nav-item active"><span>▦</span> Overview</div>
        <div className="nav-item"><span>↗</span> Orders & positions</div>
        <div className="nav-item"><span>◷</span> Strategy monitor</div>
        <div className="nav-item"><span>⚙</span> Risk controls</div>
        <div className="sidebar-bottom"><span className="pulse" /> Demo environment only<br /><small>Live trading is disabled</small></div>
      </aside>

      <section className="content">
        <header className="topbar">
          <div><div className="eyebrow">TRADING WORKSPACE / OVERVIEW</div><h1>Bot dashboard</h1><p className="subtitle">Monitor your strategy, demo orders and risk in one place.</p></div>
          <div className="top-actions"><span className="demo-badge"><span /> DEMO MODE</span><div className="avatar">TB</div></div>
        </header>

        <div className={error ? "connection warning" : "connection good"}>
          <div className="connection-icon">{error ? "!" : "✓"}</div>
          <div><strong>{error ? "Worker not connected" : "Worker connected"}</strong><p>{error || `Last successful status check at ${lastChecked}`}</p></div>
          <span className="check-time">Checked {lastChecked}</span>
        </div>

        <div className="section-heading"><div><h2>Account overview</h2><p>Values come from the worker when connected.</p></div><span className="period">LIVE STATUS · 15 SEC REFRESH</span></div>
        <div className="metrics">
          <article className="metric"><div className="metric-head">Demo balance <span>◉</span></div><div className="metric-value">{money(status?.balance)}</div><div className="metric-foot">Exchange / paper account</div></article>
          <article className="metric"><div className="metric-head">Account equity <span>⌁</span></div><div className="metric-value">{money(status?.equity)}</div><div className="metric-foot">Balance plus unrealized P&amp;L</div></article>
          <article className="metric"><div className="metric-head">Daily P&amp;L <span>↗</span></div><div className={`metric-value ${(status?.daily_pnl ?? 0) >= 0 ? "positive" : "negative"}`}>{money(status?.daily_pnl)}</div><div className="metric-foot">UTC trading day</div></article>
          <article className="metric"><div className="metric-head">Open positions <span>▤</span></div><div className="metric-value">{status ? positions.length : "—"}</div><div className="metric-foot">Maximum allowed: 1</div></article>
        </div>

        <div className="grid-two">
          <section className="panel">
            <div className="panel-title"><div><h2>Strategy monitor</h2><p>Current configured signal engine</p></div><span className="status-chip">EMA CROSS</span></div>
            <div className="strategy-symbol"><div className="coin">₿</div><div><strong>{status?.symbol ?? "BTCUSDT"}</strong><small>{status?.timeframe ?? "5m"} candle · Linear perpetual</small></div><span className="neutral-tag">EMA 9 / 21</span></div>
            <div className="strategy-rows"><div><span>Fast / slow EMA</span><strong>9 / 21</strong></div><div><span>RSI filter</span><strong>14 period</strong></div><div><span>Stop loss</span><strong>1.5 × ATR</strong></div><div><span>Take profit</span><strong>2.25 × ATR</strong></div></div>
            <div className="note">Signals and order execution remain disabled until the worker connection and demo exchange checks pass.</div>
          </section>

          <section className="panel">
            <div className="panel-title"><div><h2>Risk controls</h2><p>Configured safety limits</p></div><span className="safe-chip">GUARDED</span></div>
            <div className="risk-row"><div className="risk-icon">⌁</div><div><strong>Maximum risk per trade</strong><small>Hard limit per entry</small></div><b>$0.50</b></div>
            <div className="risk-row"><div className="risk-icon">⊘</div><div><strong>Maximum daily loss</strong><small>Trading halt threshold</small></div><b>$2.00</b></div>
            <div className="risk-row"><div className="risk-icon">⇄</div><div><strong>Position limit</strong><small>Concurrent positions</small></div><b>1</b></div>
            <div className="risk-row"><div className="risk-icon">⛨</div><div><strong>Martingale</strong><small>Loss chasing</small></div><b className="negative">Disabled</b></div>
          </section>
        </div>

        <section className="panel table-panel">
          <div className="panel-title"><div><h2>Open positions</h2><p>Exchange-reconciled positions only</p></div><span className="count-chip">{positions.length} OPEN</span></div>
          {positions.length ? <div className="table-wrap"><table><thead><tr><th>Symbol</th><th>Side</th><th>Quantity</th><th>Entry</th><th>Stop loss</th><th>Take profit</th></tr></thead><tbody>{positions.map((p, i) => <tr key={i}><td>{p.symbol}</td><td><span className={p.side.toLowerCase() === "buy" || p.side.toLowerCase() === "long" ? "long" : "short"}>{p.side}</span></td><td>{p.qty}</td><td>{p.entry_price ?? "—"}</td><td>{p.stop_loss ?? "—"}</td><td>{p.take_profit ?? "—"}</td></tr>)}</tbody></table></div> : <div className="empty"><span>▤</span><strong>No positions to display</strong><p>Positions will appear here after the worker reports them.</p></div>}
        </section>

        <section className="panel table-panel">
          <div className="panel-title"><div><h2>Recent orders</h2><p>Latest demo order activity</p></div><span className="count-chip">{orders.length} RECORDS</span></div>
          {orders.length ? <div className="table-wrap"><table><thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Quantity</th><th>Status</th><th>Price</th><th>P&amp;L</th></tr></thead><tbody>{orders.map((o, i) => <tr key={i}><td>{o.time ?? "—"}</td><td>{o.symbol ?? "BTCUSDT"}</td><td>{o.side ?? "—"}</td><td>{o.qty ?? "—"}</td><td><span className="status-chip">{o.status ?? "—"}</span></td><td>{o.price ?? "—"}</td><td className={(o.pnl ?? 0) >= 0 ? "positive" : "negative"}>{money(o.pnl)}</td></tr>)}</tbody></table></div> : <div className="empty compact"><span>↗</span><strong>No order activity yet</strong><p>Demo orders will appear here once exchange execution is integrated and verified.</p></div>}
        </section>
        <footer><span>TradePilot · Demo monitoring interface</span><span>Credentials are server-side only · Live trading unavailable</span></footer>
      </section>
    </main>
  );
}
