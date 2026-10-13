"""Read-only HTTP API for the dashboard. Run with: uvicorn worker_api:app --host 0.0.0.0 --port 8000"""
from __future__ import annotations

import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from bybit_demo import BybitDemoClient, BybitDemoError

app = FastAPI(title="Trading Bot Worker API", version="0.1.0")

origins = [x.strip().rstrip("/") for x in os.getenv("DASHBOARD_ORIGINS", "").split(",") if x.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Content-Type"],
    )


def _number(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "trading-bot-worker-api", "timestamp": int(time.time() * 1000)}


@app.get("/api/status")
def status() -> dict:
    """Read-only demo account status. This endpoint never places or cancels orders."""
    try:
        client = BybitDemoClient()
        positions_raw = client.get_positions(os.getenv("BOT_SYMBOL", "BTCUSDT"))
        orders_raw = client.get_open_orders(os.getenv("BOT_SYMBOL", "BTCUSDT"))
        positions = []
        for p in positions_raw:
            size = _number(p.get("size"))
            if size <= 0:
                continue
            side = str(p.get("side", ""))
            positions.append({
                "symbol": p.get("symbol", os.getenv("BOT_SYMBOL", "BTCUSDT")),
                "side": side,
                "qty": size,
                "entry_price": _number(p.get("avgPrice")),
                "stop_loss": _number(p.get("stopLoss")) or None,
                "take_profit": _number(p.get("takeProfit")) or None,
            })
        orders = [{
            "time": o.get("createdTime"),
            "symbol": o.get("symbol"),
            "side": o.get("side"),
            "qty": _number(o.get("qty")),
            "status": o.get("orderStatus"),
            "price": _number(o.get("avgPrice") or o.get("price")),
            "pnl": None,
        } for o in orders_raw[:25]]
        return {
            "state": "CONNECTED",
            "mode": "BYBIT_DEMO",
            "symbol": os.getenv("BOT_SYMBOL", "BTCUSDT"),
            "timeframe": os.getenv("BOT_TIMEFRAME", "5m"),
            "api": "OK",
            "updated_at": int(time.time() * 1000),
            "balance": None,
            "equity": None,
            "daily_pnl": None,
            "positions": positions,
            "orders": orders,
        }
    except BybitDemoError as exc:
        raise HTTPException(status_code=502, detail=f"Bybit demo API error: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Worker status check failed: {type(exc).__name__}") from exc
