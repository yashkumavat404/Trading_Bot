"""Bybit v5 DEMO REST adapter (https://api-demo.bybit.com). UNVERIFIED against the live demo host in this
environment (no network access). Mock-tested only: signing, bounded retries, idempotent order IDs."""
from __future__ import annotations
import hashlib, hmac, json, os, time
import requests

DEMO_HOST = "https://api-demo.bybit.com"


class OrderStateUncertain(Exception):
    """Order outcome unknown after retries; trading must pause until reconciled."""


class BybitDemoBroker:
    def __init__(self, key=None, secret=None, host=DEMO_HOST, session=None, recv_window=5000, retries=3, timeout=8):
        if host != DEMO_HOST:
            raise ValueError("only the Bybit DEMO host is allowed")  # live trading disabled by design
        self.key = key or os.getenv("BYBIT_DEMO_API_KEY", "")
        self.secret = secret or os.getenv("BYBIT_DEMO_API_SECRET", "")
        self.host, self.http, self.rw, self.retries, self.timeout = host, session or requests, str(recv_window), retries, timeout
        self.uncertain = False

    def _sign(self, ts: str, payload: str) -> str:
        return hmac.new(self.secret.encode(), (ts + self.key + self.rw + payload).encode(), hashlib.sha256).hexdigest()

    def _request(self, method, path, params=None, body=None):
        if not self.key or not self.secret: raise PermissionError("demo API credentials missing (.env)")
        last = None
        for i in range(self.retries):
            ts = str(int(time.time() * 1000))
            if method == "GET":
                payload = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items())); data = None
            else:
                payload = data = json.dumps(body or {}, separators=(",", ":"))
            h = {"X-BAPI-API-KEY": self.key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": self.rw,
                 "X-BAPI-SIGN": self._sign(ts, payload), "Content-Type": "application/json"}
            try:
                url = self.host + path + (("?" + payload) if method == "GET" and payload else "")
                r = self.http.request(method, url, headers=h, data=data, timeout=self.timeout)
                r.raise_for_status(); j = r.json()
                if j.get("retCode") != 0: raise RuntimeError(f"{j.get('retCode')}: {j.get('retMsg')}")
                return j["result"]
            except (requests.Timeout, requests.ConnectionError) as e:
                last = e; time.sleep(min(0.5 * 2 ** i, 4))
        raise ConnectionError(f"request failed after {self.retries} tries: {last}")

    def balance(self):
        r = self._request("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        return float(r["list"][0]["totalEquity"])

    def positions(self, symbol):
        r = self._request("GET", "/v5/position/list", {"category": "linear", "symbol": symbol})["list"]
        return [dict(symbol=p["symbol"], side=1 if p["side"] == "Buy" else -1, qty=float(p["size"]),
                     sl=float(p["stopLoss"] or 0), tp=float(p["takeProfit"] or 0), entry=float(p["avgPrice"]))
                for p in r if float(p["size"]) > 0]

    def set_leverage(self, symbol, lev):
        return self._request("POST", "/v5/position/set-leverage",
                             body=dict(category="linear", symbol=symbol, buyLeverage=str(lev), sellLeverage=str(lev)))

    def order_status(self, symbol, link_id):
        r = self._request("GET", "/v5/order/realtime", {"category": "linear", "symbol": symbol, "orderLinkId": link_id})["list"]
        return r[0] if r else None

    def market_order(self, symbol, side, qty, sl, tp, link_id):
        """SL and TP are mandatory and attached to the order. link_id makes retries idempotent."""
        if not sl or not tp: raise ValueError("SL and TP are mandatory")
        if self.uncertain: raise OrderStateUncertain("previous order unresolved")
        body = dict(category="linear", symbol=symbol, side="Buy" if side == 1 else "Sell", orderType="Market",
                    qty=str(qty), stopLoss=str(sl), takeProfit=str(tp), tpslMode="Full", orderLinkId=link_id)
        try:
            return self._request("POST", "/v5/order/create", body=body)
        except ConnectionError:
            try:
                st = self.order_status(symbol, link_id)  # did it land?
            except Exception:
                st = "unknown"
            if st is None: return {"orderLinkId": link_id, "status": "not_placed"}
            if st != "unknown": return st
            self.uncertain = True
            raise OrderStateUncertain(link_id)
