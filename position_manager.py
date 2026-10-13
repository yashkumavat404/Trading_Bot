"""Position reconciliation between local state and the exchange, plus protective-exit checks."""


def reconcile(local: list[dict], exchange: list[dict], qty_tol: float = 1e-9) -> list[tuple]:
    """Pure function. local/exchange items: {symbol, side(+1/-1), qty, sl, tp}.
    Returns actions: ('adopt', pos) exchange-only position; ('orphan', pos) local-only (closed remotely);
    ('size_mismatch', local, exch); ('unprotected', pos) missing SL/TP -> must be fixed or closed."""
    acts, key = [], lambda p: (p["symbol"], p["side"])
    L, E = {key(p): p for p in local}, {key(p): p for p in exchange}
    for k, e in E.items():
        if k not in L: acts.append(("adopt", e))
        elif abs(L[k]["qty"] - e["qty"]) > qty_tol: acts.append(("size_mismatch", L[k], e))
        if not e.get("sl") or not e.get("tp"): acts.append(("unprotected", e))
    for k, l in L.items():
        if k not in E: acts.append(("orphan", l))
    return acts


def protective_levels(side: int, entry: float, atr: float, sl_mult: float, tp_mult: float):
    return entry - side * atr * sl_mult, entry + side * atr * tp_mult
