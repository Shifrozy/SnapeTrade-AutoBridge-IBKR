#!/usr/bin/env python3
"""
snaptrade → ibkr bridge (csv or api)
- pulls orders from snaptrade via the official python sdk (SnapTrade facade)
- or reads local csv with the same schema
- normalizes to: symbol, side, quantity, order_type, limit_price, tif, exchange, currency
- dry-run by default; add --live to actually transmit to tws/ib gateway

examples:
  # snaptrade, dry run
  python script.py --source snaptrade

  # snaptrade, paper tws live test
  python script.py --source snaptrade --live --port 7497 --client-id 1

  # csv, dry run
  python script.py --source csv path/to/orders.csv

env (.env file next to this script):
  SNAPTRADE_CLIENT_ID=
  SNAPTRADE_CONSUMER_KEY=
  SNAPTRADE_USER_ID=
  SNAPTRADE_USER_SECRET=
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional, Tuple, List

import pandas as pd
from dotenv import load_dotenv

# ibkr
from ib_insync import IB, Stock, MarketOrder, LimitOrder, Order, util

# snaptrade sdk (modern)
from snaptrade_client import SnapTrade

# ----------------- constants -----------------
REQUIRED_COLS = {"symbol", "side", "quantity", "order_type"}
OPTIONAL_COLS = {"limit_price", "tif", "exchange", "currency"}
VALID_SIDES = {"BUY", "SELL"}
VALID_ORDER_TYPES = {"MKT", "LMT"}
DEFAULTS = {"tif": "DAY", "exchange": "SMART", "currency": "USD"}

load_dotenv()


# ----------------- csv ingest -----------------
def normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={c: c.strip().lower() for c in df.columns})


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


# ----------------- snaptrade ingest (sdk) -----------------
def fetch_orders_from_snaptrade(account_index: int = 0, use_recent: bool = True) -> pd.DataFrame:
    """
    pulls orders from snaptrade and maps to our schema
    note: snaptrade only returns orders that were placed via snaptrade
    """
    for k in ("SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY", "SNAPTRADE_USER_ID", "SNAPTRADE_USER_SECRET"):
        if not os.getenv(k):
            raise RuntimeError(f"missing {k} in environment or .env")

    snaptrade = SnapTrade(
        consumer_key=os.environ["SNAPTRADE_CONSUMER_KEY"],
        client_id=os.environ["SNAPTRADE_CLIENT_ID"],
        # host defaults to https://api.snaptrade.com/api/v1
    )
    user_id = os.environ["SNAPTRADE_USER_ID"]
    user_secret = os.environ["SNAPTRADE_USER_SECRET"]

    # accounts
    accounts_resp = snaptrade.account_information.list_user_accounts(user_id=user_id, user_secret=user_secret)
    accounts = accounts_resp.body or []
    if not accounts:
        raise RuntimeError("no snaptrade accounts found for this user")
    if account_index < 0 or account_index >= len(accounts):
        raise RuntimeError(f"account_index {account_index} out of range 0..{len(accounts)-1}")

    account_id = accounts[account_index]["id"]

    # choose endpoint
    if use_recent:
        orders_resp = snaptrade.account_information.get_user_account_recent_orders(
            user_id=user_id, user_secret=user_secret, account_id=account_id
        )
    else:
        orders_resp = snaptrade.account_information.get_user_account_orders(
            user_id=user_id, user_secret=user_secret, account_id=account_id
        )

    raw_orders = orders_resp.body or []
    if not raw_orders and use_recent:
        # fallback to full list if recent is empty
        orders_resp = snaptrade.account_information.get_user_account_orders(
            user_id=user_id, user_secret=user_secret, account_id=account_id
        )
        raw_orders = orders_resp.body or []

    if not raw_orders:
        raise RuntimeError("snaptrade returned no orders for this account")

    # map to our schema
    normalized: List[dict] = []
    side_map = {
        "BUY": "BUY",
        "SELL": "SELL",
        "BUY_TO_OPEN": "BUY",
        "BUY_TO_CLOSE": "BUY",
        "SELL_TO_OPEN": "SELL",
        "SELL_TO_CLOSE": "SELL",
    }
    type_map = {"MARKET": "MKT", "LIMIT": "LMT", "MKT": "MKT", "LMT": "LMT"}

    for o in raw_orders:
        # tolerant extraction: different brokers structure slightly differently
        sym = (o.get("symbol") or (o.get("legs") or [{}])[0].get("symbol") or "").upper()
        action = (o.get("action") or o.get("order_type") or "").upper()
        qty = o.get("quantity") or o.get("filled_quantity")
        typ = (o.get("type") or o.get("order_sub_type") or "").upper()
        tif = (o.get("time_in_force") or "DAY").upper()
        limit_px = o.get("limit_price") or o.get("limit")

        side = side_map.get(action)
        order_type = type_map.get(typ)

        if not sym or not qty or side not in VALID_SIDES or order_type not in VALID_ORDER_TYPES:
            # skip anything we can’t confidently mirror
            continue

        normalized.append(
            {
                "symbol": sym,
                "side": side,
                "quantity": int(qty),
                "order_type": order_type,
                "limit_price": float(limit_px) if limit_px else None,
                "tif": tif,
                "exchange": "SMART",
                "currency": "USD",
            }
        )

    if not normalized:
        raise RuntimeError("no usable orders after mapping adjust mapping for your brokerage payload")

    return pd.DataFrame(normalized)


# ----------------- validation + ibkr send -----------------
def coerce_row(d: dict) -> Tuple[Optional[dict], Optional[str]]:
    for k, v in list(d.items()):
        if isinstance(v, str):
            d[k] = v.strip()
    for k, v in DEFAULTS.items():
        if not d.get(k):
            d[k] = v

    sym = d.get("symbol")
    if not sym or not isinstance(sym, str):
        return None, "invalid symbol"

    side = d.get("side")
    side = side.upper() if isinstance(side, str) else None
    if side not in VALID_SIDES:
        return None, f"invalid side {d.get('side')}"

    try:
        qty = int(float(d.get("quantity")))
        if qty <= 0:
            return None, "quantity must be positive"
    except Exception:
        return None, f"invalid quantity {d.get('quantity')}"

    otype = d.get("order_type")
    otype = otype.upper() if isinstance(otype, str) else None
    if otype not in VALID_ORDER_TYPES:
        return None, f"invalid order_type {d.get('order_type')}"

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

    return {
        "symbol": sym.upper(),
        "side": side,
        "quantity": qty,
        "order_type": otype,
        "limit_price": lmt,
        "tif": tif,
        "exchange": exch,
        "currency": ccy,
    }, None


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
                successes.append(
                    {
                        "row": int(i),
                        "symbol": row["symbol"],
                        "side": row["side"],
                        "qty": row["quantity"],
                        "status": trade.orderStatus.status,
                        "filled": trade.orderStatus.filled,
                        "avg_px": trade.orderStatus.avgFillPrice,
                        "permId": trade.order.permId,
                    }
                )
            except Exception as e:
                failures.append({"row": int(i), "symbol": row["symbol"], "error": str(e)})
            time.sleep(throttle_sec)
    finally:
        ib.disconnect()
    return successes, failures


# ----------------- cli -----------------
def main():
    ap = argparse.ArgumentParser(description="bridge orders from csv or snaptrade into ibkr")
    ap.add_argument("--source", choices=["csv", "snaptrade"], default="snaptrade")
    ap.add_argument("csv", nargs="?", help="path to csv if --source csv")
    ap.add_argument("--account-index", type=int, default=0, help="which snaptrade account to use if multiple")
    ap.add_argument("--all-orders", action="store_true", help="use full orders list instead of recent")
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
        df = fetch_orders_from_snaptrade(account_index=args.account_index, use_recent=not args.all_orders)

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
        print("\ndry run no orders sent pass --live to actually transmit")
        sys.exit(0)

    print("\nconnecting to tws and sending orders...")
    successes, failures = place_orders_ibkr(
        clean_df, host=args.host, port=args.port, client_id=args.client_id, live=True
    )
    print("\nresults:")
    if successes:
        print("successful orders:")
        for s in successes:
            print(
                f"  row {s['row']} {s['side']} {s['qty']} {s['symbol']} "
                f"status={s['status']} filled={s['filled']} avg_px={s['avg_px']}"
            )
    if failures:
        print("failed orders:", file=sys.stderr)
        for f in failures:
            print(f"  row {f['row']} {f['symbol']}: {f['error']}", file=sys.stderr)


if __name__ == "__main__":
    main()
