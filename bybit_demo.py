"""Minimal, demo-host-only Bybit V5 REST client.

This module deliberately has no live/mainnet switch. It uses system-generated
HMAC API keys from Bybit Demo Trading mode and attaches exchange-side SL/TP to
every market entry. Never put credentials in source control.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode

import requests


DEMO_BASE_URL = "https://api-demo.bybit.com"
RECV_WINDOW = "5000"


class BybitDemoError(RuntimeError):
    """A request failed or Bybit returned a non-zero retCode."""


class BybitDemoClient:
    def __init__(self, api_key: str | None = None, api_secret: str | None = None,
                 timeout: float = 10.0, session: requests.Session | None = None):
        self.api_key = (api_key or os.getenv("BYBIT_DEMO_API_KEY", "")).strip()
        self.api_secret = (api_secret or os.getenv("BYBIT_DEMO_API_SECRET", "")).strip()
        if not self.api_key or not self.api_secret:
            raise BybitDemoError(
                "Missing BYBIT_DEMO_API_KEY / BYBIT_DEMO_API_SECRET. "
                "Create credentials inside Bybit Demo Trading mode."
            )
        self.base_url = DEMO_BASE_URL  # intentionally fixed: never accept a live host
        self.timeout = timeout
        self.session = session or requests.Session()

    def _request(self, method: str, path: str, params: dict | None = None) -> dict:
        method = method.upper()
        params = params or {}
        timestamp = str(int(time.time() * 1000))
        if method == "GET":
            # Keep signing and transmitted query strings byte-for-byte identical.
            payload = urlencode([(k, str(v)) for k, v in params.items() if v is not None])
            body = None
            url = f"{self.base_url}{path}" + (f"?{payload}" if payload else "")
        elif method == "POST":
            payload = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
            body = payload
            url = f"{self.base_url}{path}"
        else:
            raise ValueError("Only GET and POST are supported")

        sign_text = timestamp + self.api_key + RECV_WINDOW + payload
        signature = hmac.new(
            self.api_secret.encode("utf-8"), sign_text.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        headers = {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": RECV_WINDOW,
            "Content-Type": "application/json",
        }
        response = self.session.request(
            method, url, headers=headers, data=body, timeout=self.timeout
        )
        response.raise_for_status()
        try:
            result = response.json()
        except ValueError as exc:
            raise BybitDemoError("Bybit returned invalid JSON") from exc
        if result.get("retCode") != 0:
            raise BybitDemoError(
                f"Bybit error {result.get('retCode')}: {result.get('retMsg', 'unknown error')}"
            )
        return result.get("result", {})

    def get_instruments_info(self, symbol: str = "BTCUSDT") -> dict:
        result = self._request("GET", "/v5/market/instruments-info", {
            "category": "linear", "symbol": symbol.upper()
        })
        items = result.get("list", [])
        if not items:
            raise BybitDemoError(f"No linear instrument metadata returned for {symbol}")
        return items[0]

    def get_positions(self, symbol: str = "BTCUSDT") -> list[dict]:
        result = self._request("GET", "/v5/position/list", {
            "category": "linear", "symbol": symbol.upper()
        })
        return result.get("list", [])

    def get_open_orders(self, symbol: str = "BTCUSDT") -> list[dict]:
        result = self._request("GET", "/v5/order/realtime", {
            "category": "linear", "symbol": symbol.upper(), "openOnly": 0
        })
        return result.get("list", [])

    def place_market_order(self, *, symbol: str, side: str, qty: str | float,
                           stop_loss: str | float, take_profit: str | float,
                           order_link_id: str) -> dict:
        """Place a demo market order with exchange-side protective exits.

        This method intentionally requires SL and TP. It does not size trades;
        the strategy/risk layer must calculate a valid quantity and price levels.
        """
        side = side.capitalize()
        if side not in {"Buy", "Sell"}:
            raise ValueError("side must be Buy or Sell")
        if float(qty) <= 0:
            raise ValueError("qty must be positive")
        sl, tp = float(stop_loss), float(take_profit)
        if sl <= 0 or tp <= 0:
            raise ValueError("stop_loss and take_profit must be positive")
        if side == "Buy" and not sl < tp:
            raise ValueError("Long order requires stop_loss < take_profit")
        if side == "Sell" and not tp < sl:
            raise ValueError("Short order requires take_profit < stop_loss")
        if not order_link_id or len(order_link_id) > 36:
            raise ValueError("order_link_id must be 1–36 characters")

        return self._request("POST", "/v5/order/create", {
            "category": "linear",
            "symbol": symbol.upper(),
            "side": side,
            "orderType": "Market",
            "qty": str(qty),
            "positionIdx": 0,
            "orderLinkId": order_link_id,
            "stopLoss": str(stop_loss),
            "takeProfit": str(take_profit),
            "slTriggerBy": "MarkPrice",
            "tpTriggerBy": "MarkPrice",
            "tpslMode": "Full",
            "slOrderType": "Market",
            "tpOrderType": "Market",
        })
