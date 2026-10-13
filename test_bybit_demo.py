import pytest

from bybit_demo import BybitDemoClient, BybitDemoError, DEMO_BASE_URL


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload or {"retCode": 0, "retMsg": "OK", "result": {"orderId": "demo-1"}}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse()


def client():
    session = FakeSession()
    return BybitDemoClient("demo-key", "demo-secret", session=session), session


def test_client_uses_fixed_demo_host_and_signs_request():
    api, session = client()
    result = api._request("GET", "/v5/position/list", {"category": "linear", "symbol": "BTCUSDT"})
    method, url, kwargs = session.calls[0]
    assert result["orderId"] == "demo-1"
    assert method == "GET"
    assert url.startswith(DEMO_BASE_URL + "/v5/position/list?")
    assert kwargs["headers"]["X-BAPI-API-KEY"] == "demo-key"
    assert kwargs["headers"]["X-BAPI-SIGN-TYPE"] == "2"
    assert len(kwargs["headers"]["X-BAPI-SIGN"]) == 64


def test_missing_credentials_fail_closed(monkeypatch):
    monkeypatch.delenv("BYBIT_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_DEMO_API_SECRET", raising=False)
    with pytest.raises(BybitDemoError, match="Missing"):
        BybitDemoClient()


@pytest.mark.parametrize("side,sl,tp", [
    ("Buy", 99, 101),
    ("Sell", 101, 99),
])
def test_market_order_always_includes_protective_exits(side, sl, tp):
    api, session = client()
    api.place_market_order(
        symbol="BTCUSDT", side=side, qty="0.001",
        stop_loss=sl, take_profit=tp, order_link_id="unit-test-001",
    )
    method, url, kwargs = session.calls[0]
    import json
    payload = json.loads(kwargs["data"])
    assert method == "POST"
    assert url == DEMO_BASE_URL + "/v5/order/create"
    assert payload["stopLoss"] == str(sl)
    assert payload["takeProfit"] == str(tp)
    assert payload["orderType"] == "Market"


def test_market_order_rejects_missing_or_invalid_protection():
    api, session = client()
    with pytest.raises(ValueError):
        api.place_market_order(
            symbol="BTCUSDT", side="Buy", qty="0.001",
            stop_loss=0, take_profit=101, order_link_id="unit-test-002",
        )
    with pytest.raises(ValueError):
        api.place_market_order(
            symbol="BTCUSDT", side="Buy", qty="0.001",
            stop_loss=102, take_profit=101, order_link_id="unit-test-003",
        )
    assert session.calls == []


def test_exchange_error_is_not_ignored():
    session = FakeSession()
    session.request = lambda *args, **kwargs: FakeResponse(
        {"retCode": 10001, "retMsg": "invalid request", "result": {}}
    )
    api = BybitDemoClient("demo-key", "demo-secret", session=session)
    with pytest.raises(BybitDemoError, match="10001"):
        api._request("GET", "/v5/position/list", {"category": "linear"})
