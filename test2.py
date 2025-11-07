#!/usr/bin/env python3
# pulls recent orders from snaptrade then mirrors simple stock orders into ibkr tws
# supports market and limit buy sell only skips options multi leg and nonsense

import os, sys, time, argparse
import pandas as pd
from dotenv import load_dotenv
from snaptrade_client import SnapTrade
from ib_insync import IB, Stock, MarketOrder, LimitOrder, util

VALID_SIDES = {"BUY","SELL"}
TYPE_MAP = {"MARKET":"MKT","LIMIT":"LMT","MKT":"MKT","LMT":"LMT"}

def fetch_snaptrade_orders(account_index=0, recent_first=True):
    load_dotenv()
    need = ["SNAPTRADE_CLIENT_ID","SNAPTRADE_CONSUMER_KEY","SNAPTRADE_USER_ID","SNAPTRADE_USER_SECRET"]
    if not all(os.getenv(k) for k in need):
        raise SystemExit("missing snaptrade env values check .env")

    st = SnapTrade(consumer_key=os.environ["SNAPTRADE_CONSUMER_KEY"],
                   client_id=os.environ["SNAPTRADE_CLIENT_ID"])
    uid = os.environ["SNAPTRADE_USER_ID"]; usec = os.environ["SNAPTRADE_USER_SECRET"]

    accts = st.account_information.list_user_accounts(user_id=uid, user_secret=usec).body or []
    if not accts: raise SystemExit("no linked accounts for this snaptrade user")
    if account_index < 0 or account_index >= len(accts):
        raise SystemExit(f"account_index out of range 0..{len(accts)-1}")
    account_id = accts[account_index]["id"]

    if recent_first:
        resp = st.account_information.get_user_account_recent_orders(
            user_id=uid, user_secret=usec, account_id=account_id).body or []
        if not resp:
            resp = st.account_information.get_user_account_orders(
                user_id=uid, user_secret=usec, account_id=account_id).body or []
    else:
        resp = st.account_information.get_user_account_orders(
            user_id=uid, user_secret=usec, account_id=account_id).body or []
    if not resp:
        raise SystemExit("snaptrade returned no orders")

    # normalize to ib schema
    rows = []
    for o in resp:
        sym = (o.get("symbol") or (o.get("legs") or [{}])[0].get("symbol") or "").upper()
        side = (o.get("action") or o.get("order_type") or "").upper()
        qty  = o.get("quantity") or o.get("filled_quantity")
        otype = TYPE_MAP.get((o.get("type") or o.get("order_sub_type") or "").upper())
        tif = (o.get("time_in_force") or "DAY").upper()
        limit_px = o.get("limit_price") or o.get("limit")
        if not sym or side not in VALID_SIDES or not qty or otype not in {"MKT","LMT"}:
            continue
        rows.append({
            "symbol": sym, "side": side, "quantity": int(qty),
            "order_type": otype, "limit_price": float(limit_px) if limit_px else None,
            "tif": tif, "exchange": "SMART", "currency": "USD"
        })
    if not rows:
        raise SystemExit("no usable orders after mapping only simple stock mkt lmt supported")
    return pd.DataFrame(rows)

def place_to_tws(df, host, port, client_id, live):
    ib = IB(); util.logToConsole()
    ib.connect(host, port, clientId=client_id, readonly=not live)
    try:
        for _, r in df.iterrows():
            c = Stock(r["symbol"], exchange=r["exchange"], currency=r["currency"])
            o = MarketOrder(r["side"], r["quantity"]) if r["order_type"]=="MKT" else LimitOrder(r["side"], r["quantity"], r["limit_price"])
            o.tif = r["tif"]
            t = ib.placeOrder(c, o)
            while not t.isDone(): ib.waitOnUpdate(timeout=1)
            print(f"{r['side']} {r['quantity']} {r['symbol']} status={t.orderStatus.status} filled={t.orderStatus.filled} avg={t.orderStatus.avgFillPrice}")
            time.sleep(0.3)
    finally:
        ib.disconnect()

def main():
    ap = argparse.ArgumentParser(description="mirror snaptrade orders into ibkr tws")
    ap.add_argument("--account-index", type=int, default=0)
    ap.add_argument("--all-orders", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=101)
    ap.add_argument("--live", action="store_true", help="transmit to tws")
    args = ap.parse_args()

    df = fetch_snaptrade_orders(account_index=args.account_index, recent_first=not args.all_orders)
    print("\norders to process:\n", df.to_string(index=False))

    if not args.live:
        print("\ndry run done add --live to transmit to tws"); sys.exit(0)

    place_to_tws(df, args.host, args.port, args.client_id, live=True)

if __name__ == "__main__":
    main()
