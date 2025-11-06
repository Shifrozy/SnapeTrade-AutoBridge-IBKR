#!/usr/bin/env python3
"""
snaptrade -> ibkr bridge
- source can be CSV or SnapTrade API
- normalizes to columns: symbol, side, quantity, order_type, limit_price, tif, exchange, currency
- sends via ib_insync to TWS / IB Gateway

examples:
  # read local csv (dry run)
  python test.py --source csv c:/path/orders.csv

  # read from snaptrade api (dry run)
  python test.py --source snaptrade

  # actually transmit to paper TWS
  python test.py --source snaptrade --live --port 7497
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional, Tuple, List

import pandas as pd
from dotenv import load_dotenv
from ib_insync import IB, Stock, MarketOrder, LimitOrder, Order, util

# ----------------- config / constants -----------------
REQUIRED_COLS = {"symbol", "side", "quantity", "order_type"}
OPTIONAL_COLS = {"limit_price", "tif", "exchange", "currency"}
VALID_SIDES = {"BUY", "SELL"}
VALID_ORDER_TYPES = {"MKT", "LMT"}
DEFAULTS = {"tif": "DAY", "exchange": "SMART", "currency": "USD"}

load_dotenv()

# ----------------- csv ingest -----------------
def normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns={c: c.strip().lower() for c in df.columns})
    return df

def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = normalize_headers(df)
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"csv missing required columns: {sorted(list(missing))}")
    for col in OPTIONAL_COLS:
        if col not in df.columns:
            df[col] = None
    return df

# ----------------- snaptrade ingest -----------------
def fetch_orders_from_snaptrade() -> pd.DataFrame:
    """
    fetches orders/trade-intents from snaptrade api and returns a dataframe
    with the same columns as the csv flow.

    you have two options below:
    A) raw https with requests (works anywhere)
    B) official sdk (nicer models; uncomment if you prefer)

    whichever route you choose, end by building a list[dict] like:
      {"symbol":"AAPL","side":"BUY","quantity":10,"order_type":"MKT",
       "limit_price":None,"tif":"DAY","exchange":"SMART","currency":"USD"}
    """
    client_id = os.getenv("SNAPTRADE_CLIENT_ID")
    consumer_key = os.getenv("SNAPTRADE_CONSUMER_KEY")
    user_id = os.getenv("SNAPTRADE_USER_ID")
    user_secret = os.getenv("SNAPTRADE_USER_SECRET")

    for k in ("SNAPTRADE_CLIENT_ID","SNAPTRADE_CONSUMER_KEY","SNAPTRADE_USER_ID","SNAPTRADE_USER_SECRET"):
        if not os.getenv(k):
            raise RuntimeError(f"missing {k} in environment")

    # ------------ option A: raw http (example endpoints; adjust to your app’s endpoints) ------------
    # docs give you the exact paths. the auth pattern is typically headers with client_id, consumer_key,
    # and per-user id/secret to scope the call.
    import requests

    base = "https://api.snaptrade.com/api/v1"
    headers = {
        "Content-Type": "application/json",
        "clientId": client_id,
        "consumerKey": consumer_key,
        "userId": user_id,
        "userSecret": user_secret,
    }

    # example: fetch "suggested trades" or "open signals" your app produces
    # replace the endpoint with whatever object you actually want to turn into orders
    # e.g., /trade/algos/signals, /orders/pending, etc. check your app’s endpoints.
    # fallback demo pulls nothing if endpoint doesn’t exist; you will customize this.
    resp = requests.get(f"{base}/trade/orders", headers=headers, timeout=30)
    if resp.status_code == 404:
        # try another common list endpoint name
        resp = requests.get(f"{base}/orders", headers=headers, timeout=30)
    resp.raise_for_status()
    items = resp.json() if isinstance(resp.json(), list) else resp.json().get("data", [])

    # map snaptrade’s payload to our normalized schema
    normalized: List[dict] = []
    for it in items:
        # adapt these keys to your actual payload fields
        sym = (it.get("symbol") or it.get("ticker") or "").upper()
        side = (it.get("side") or it.get("action") or "").upper()
        qty = it.get("quantity") or it.get("qty")
        otype = (it.get("order_type") or it.get("type") or "MKT").upper()
        limit_px = it.get("limit_price") or it.get("limit")
        tif = (it.get("tif") or it.get("time_in_force") or "DAY").upper()
        exch = (it.get("exchange") or "SMART").upper()
        ccy = (it.get("currency") or "USD").upper()

        if not sym or side not in VALID_SIDES or otype not in VALID_ORDER_TYPES:
            # skip garbage; we’ll validate later anyway
            continue

        normalized.append({
            "symbol": sym,
            "side": side,
            "quantity": int(qty),
            "order_type": otype,
            "limit_price": float(limit_px) if limit_px else None,
            "tif": tif,
            "exchange": exch,
            "currency": ccy,
        })

    if not normalized:
        raise RuntimeError("snaptrade returned no orders; confirm endpoint and mapping")

    df = pd.DataFrame(normalized)
    # ensure optional columns exist
    for col in OPTIONAL_COLS:
        if col not in df.columns:
            df[col] = None
    return df

    # ------------ option B: official sdk (reference; uncomment and tailor) ------------
    # import snaptrade_client
    # cfg = snaptrade_client.Configuration(host="https://api.snaptrade.com")
    # api_client = snaptrade_client.ApiClient(cfg)
    # auth = snaptrade_client.AuthenticationApi(api_client)
    # # validate/refresh user secret if needed:
    # # auth.reset_user_secret(client_id=client_id, consumer_key=consumer_key, user_id=user_id)
    # orders_api = snaptrade_client.OrdersApi(api_client)
    # # this method name depends on what you want (open orders, suggested trades, etc.)
    # api_orders = orders_api.list_orders(
    #     client_id=client_id,
    #     consumer_key=consumer_key,
    #     user_id=user_id,
    #     user_secret=user_secret,
    # )
    # # convert api_orders to the normalized list like above and return a dataframe

# ----------------- validation and ibkr send -----------------
def coerce_row(d: dict) -> Tuple[Optional[dict], Optional[str]]:
    for k, v in list(d.items()):
        if isinstance(v, str):
            d[k] = v.strip()
    for k, v in DEFAULTS.items():
        if not d.get(k):
            d[k] = v

    sym = d["symbol"]
    if not sym or not isinstance(sym, str):
        return None, "invalid symbol"
    side = d["side"].upper() if isinstance(d["side"], str) else None
    if side not in VALID_SIDES:
        return None, f"invalid side {d['side']}"
    try:
        qty = int(float(d["quantity"]))
        if qty <= 0:
            return None, "quantity must be positive"
    except Exception:
        return None, f"invalid quantity {d['quantity']}"
    otype = d["order_type"].upper() if isinstance(d["order_type"], str) else None
    if otype not in VALID_ORDER_TYPES:
        return None, f"invalid order_type {d['order_type']}"
    lmt = None
    if otype == "LMT":
        try:
            lmt = float(d.get("limit_price"))
            if not (lmt and lmt > 0):
                return None, "limit_price must be > 0 for LMT"
        except Exception:
            return None, f"invalid limit_price {d.get('limit_price')}"
    tif = (d.get("tif") or "DAY").upper()
    exch = (d.get("exchange") or "SMART").upper()
    ccy = (d.get("currency") or "USD").upper()

    clean = {
        "symbol": sym.upper(),
        "side": side,
        "quantity": qty,
        "order_type": otype,
        "limit_price": lmt,
        "tif": tif,
        "exchange": exch,
        "currency": ccy,
    }
    return clean, None

def build_contract(row: dict):
    return Stock(row["symbol"], exchange=row["exchange"], currency=row["currency"])

def build_order(row: dict) -> Order:
    if row["order_type"] == "MKT":
        order = MarketOrder(row["side"], row["quantity"])
    else:
        order = LimitOrder(row["side"], row["quantity"], row["limit_price"])
    order.tif = row["tif"]
    return order

def place_orders_ibkr(df: pd.DataFrame, host: str, port: int, client_id: int, live: bool, throttle_sec: float = 0.3):
    ib = IB()
    ib.connect(host, port, clientId=client_id, readonly=not live)
    util.logToConsole()
    successes, failures = [], []
    try:
        for i, row in df.iterrows():
            contract = build_contract(row)
            order = build_order(row)
            try:
                trade = ib.placeOrder(contract, order)
                while not trade.isDone():
                    ib.waitOnUpdate(timeout=1)
                successes.append({
                    "row": int(i),
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "qty": row["quantity"],
                    "status": trade.orderStatus.status,
                    "filled": trade.orderStatus.filled,
                    "avg_px": trade.orderStatus.avgFillPrice,
                    "permId": trade.order.permId,
                })
            except Exception as e:
                failures.append({"row": int(i), "symbol": row["symbol"], "error": str(e)})
            time.sleep(throttle_sec)
    finally:
        ib.disconnect()
    return successes, failures

# ----------------- cli -----------------
def main():
    ap = argparse.ArgumentParser(description="bridge orders from csv or snaptrade into ibkr")
    ap.add_argument("--source", choices=["csv","snaptrade"], default="csv")
    ap.add_argument("csv", nargs="?", help="path to csv if --source csv")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497, help="7497 paper 7496 live")
    ap.add_argument("--client-id", type=int, default=999)
    ap.add_argument("--live", action="store_true", help="actually send orders")
    ap.add_argument("--dry-run", action="store_true", help="print only; do not send")
    args = ap.parse_args()

    dry_run = args.dry_run or not args.live

    if args.source == "csv":
        if not args.csv:
            ap.error("csv path is required when --source csv")
        path = Path(args.csv)
        if not path.exists():
            print(f"file not found: {path}", file=sys.stderr)
            sys.exit(2)
        df = load_csv(str(path))
    else:
        df = fetch_orders_from_snaptrade()

    # normalize/validate
    cleaned, rejects = [], []
    for idx, row in df.iterrows():
        rec, err = coerce_row(row.to_dict())
        if err:
            rejects.append({"row": int(idx), "error": err})
        else:
            cleaned.append(rec)

    if rejects:
        print("rejected rows:", file=sys.stderr)
        for r in rejects:
            print(f"  row {r['row']}: {r['error']}", file=sys.stderr)

    if not cleaned:
        print("no valid orders found; nothing to do", file=sys.stderr)
        sys.exit(1)

    clean_df = pd.DataFrame(cleaned)
    print("\norders to process:")
    print(clean_df.to_string(index=False))

    if dry_run:
        print("\nDRY RUN – no orders sent. pass --live to actually transmit.")
        sys.exit(0)

    print("\nconnecting to tws/gateway and sending orders...")
    successes, failures = place_orders_ibkr(
        clean_df, host=args.host, port=args.port, client_id=args.client_id, live=True
    )
    print("\nresults:")
    if successes:
        print("successful orders:")
        for s in successes:
            print(f"  row {s['row']} {s['side']} {s['qty']} {s['symbol']} status={s['status']} filled={s['filled']} avg_px={s['avg_px']}")
    if failures:
        print("failed orders:", file=sys.stderr)
        for f in failures:
            print(f"  row {f['row']} {f['symbol']}: {f['error']}", file=sys.stderr)

if __name__ == "__main__":
    main()
